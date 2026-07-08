"""Use case: compute phasor G/S maps from TCSPC decay data."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.domain.flim.phasor import compute_phasor, resolve_calibration
from percell4.ports.dataset_repository import DatasetRepository

logger = logging.getLogger(__name__)


@dataclass
class PhasorResult:
    """Result of a phasor computation."""

    g_map: NDArray[np.float32]
    s_map: NDArray[np.float32]
    channel: str
    harmonic: int
    n_valid: int


class ComputePhasor:
    """Compute phasor G/S from TCSPC decay data for a given channel.

    Reads decay data from the repository, applies calibration, and writes
    truly-unfiltered results to the store. Spatial filtering (median or
    wavelet) is applied downstream as an opt-in view, not here.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def _read_fresh_metadata(self, handle) -> dict:
        """Read /metadata attrs fresh from disk, with snapshot fallback.

        Falls back to ``handle.metadata`` if the repository doesn't expose
        ``read_metadata`` (e.g., older test stubs).
        """
        reader = getattr(self._repo, "read_metadata", None)
        if reader is not None:
            try:
                return reader(handle)
            except Exception:
                logger.warning(
                    "read_metadata failed; falling back to handle snapshot",
                    exc_info=True,
                )
        return dict(handle.metadata)

    def execute(
        self,
        channel: str,
        harmonic: int = 1,
        view_bin: int = 1,
        cal_phase_override: float | None = None,
        cal_mod_override: float | None = None,
    ) -> PhasorResult:
        """Compute phasor G/S maps for ``channel``.

        ``view_bin`` is the session-level view bin (>= 1). Decay is read
        from the repository at the binned resolution. At k=1 behavior is
        byte-identical to before the binning model existed.

        ``cal_phase_override`` / ``cal_mod_override`` are an optional,
        non-persisted calibration override. When either is not None it
        replaces the channel's stored ``flim_cal_phase_<ch>`` /
        ``flim_cal_mod_<ch>`` for THIS computation only — the stored
        calibration on disk is never touched. Their purpose is testing a
        phasor at a harmonic other than the one the channel was calibrated
        for (calibration φ/M are frequency-dependent, so the stored
        harmonic-1 values misplace a harmonic-2 phasor). The effective
        φ/M actually used are stamped on the written g/s arrays for
        provenance.
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Whether /decay is time-lapse 4-D (Tacq, H, W, T_bins): read its dims
        # attr (metadata-only, no decode). Each acquisition frame is phasor-
        # computed independently and weighted by ITS OWN decay.sum — never a
        # sibling /intensity[t] — the cross-layer alignment rule across the new
        # axis. Legacy 3-D decay computes one 2-D phasor (byte-identical).
        decay_path = f"decay/{channel}"
        try:
            decay_attrs = self._repo.read_array_attrs(handle, decay_path)
        except Exception:
            decay_attrs = {}
        decay_dims = decay_attrs.get("dims")
        is_timelapse = decay_dims is not None and len(decay_dims) == 4

        # Calibration + native shape are channel-wide and time-invariant; read
        # once FRESH from disk (handle.metadata may be a stale snapshot that
        # predates an in-session TCSPC append's flim_cal_* writes).
        meta = self._read_fresh_metadata(handle)
        # Per-harmonic calibration: flim_cal_{phase,mod}_<ch>_h<n>, falling
        # back to the legacy single-value keys for harmonic 1 and to the
        # identity (0.0, 1.0) for an uncalibrated higher harmonic.
        cal_phase, cal_mod = resolve_calibration(meta, channel, harmonic)

        # Non-persisted override for testing off-calibration harmonics.
        # Replaces the stored calibration for this run only; disk is
        # untouched. Either field may be overridden independently.
        if cal_phase_override is not None:
            cal_phase = float(cal_phase_override)
        if cal_mod_override is not None:
            cal_mod = float(cal_mod_override)

        def _frame_gs(decay_frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            """Phasor (g, s) for one 3-D (H, W, T_bins) decay frame: compute,
            zero low-photon pixels (intensity from THIS frame's decay.sum),
            calibrate, then bin-aware upsample to native_shape."""
            g_map, s_map = compute_phasor(decay_frame, harmonic=harmonic)
            intensity_sum = decay_frame.sum(axis=-1).astype(np.float32)
            low_signal = intensity_sum <= 0
            g_map[low_signal] = 0.0
            s_map[low_signal] = 0.0
            if cal_phase != 0.0 or cal_mod != 1.0:
                cos_phi = np.cos(cal_phase)
                sin_phi = np.sin(cal_phase)
                g_cal = g_map * cal_mod * cos_phi - s_map * cal_mod * sin_phi
                s_cal = g_map * cal_mod * sin_phi + s_map * cal_mod * cos_phi
                g_map = g_cal
                s_map = s_cal
            if view_bin > 1:
                from percell4.domain.io.view_bin import nn_upsample_2d
                native = meta.get("native_shape")
                if native is None:
                    raise ValueError(
                        "Cannot write a binned phasor: /metadata.native_shape "
                        "is missing. Re-compress the dataset to populate it."
                    )
                target = (int(native[0]), int(native[1]))
                g_map = nn_upsample_2d(g_map, view_bin, target_hw=target)
                s_map = nn_upsample_2d(s_map, view_bin, target_hw=target)
            return (g_map.astype(np.float32, copy=False),
                    s_map.astype(np.float32, copy=False))

        # The canonical /phasor/<ch>/{g,s} are unfiltered; median/wavelet are
        # opt-in derived views (median_filter_gs / ApplyWavelet). created_at_bin
        # records the bin the phasor was computed at.
        write_attrs: dict = {
            "channel": channel,
            "harmonic": harmonic,
            # Effective calibration actually applied (stored φ/M, or the
            # override when one was supplied) — provenance for off-harmonic
            # test runs where these differ from the channel's stored values.
            "cal_phase": cal_phase,
            "cal_mod": cal_mod,
        }
        if view_bin > 1:
            write_attrs["created_at_bin"] = int(view_bin)

        if is_timelapse:
            # One (g, s) plane per acquisition frame -> (T_acq, H, W) stacks.
            nt = int(meta.get("n_timepoints", 1) or 1)
            g_frames, s_frames = [], []
            for t in range(nt):
                frame = self._repo.read_decay(
                    handle, channel, view_bin=view_bin, timepoint=t
                )
                g_t, s_t = _frame_gs(frame)
                g_frames.append(g_t)
                s_frames.append(s_t)
            g_map = np.stack(g_frames, axis=0)
            s_map = np.stack(s_frames, axis=0)
            write_attrs["dims"] = ["Tacq", "H", "W"]
        else:
            # Single-timepoint: read the whole 3-D decay via read_array (the
            # store dispatch applies view_bin's sum_bin_decay on /decay/* paths).
            try:
                decay = self._repo.read_array(handle, decay_path, view_bin=view_bin)
            except KeyError:
                try:
                    decay = self._repo.read_array(
                        handle, "decay", view_bin=view_bin
                    )
                except KeyError:
                    raise ValueError(
                        f"No TCSPC data found for '{channel}'. Import with FLIM "
                        "enabled."
                    )
            g_map, s_map = _frame_gs(decay)
            write_attrs["dims"] = ["H", "W"]

        # Write to store
        self._repo.write_array(
            handle, f"phasor/{channel}/g", g_map, attrs=write_attrs,
        )
        self._repo.write_array(
            handle, f"phasor/{channel}/s", s_map, attrs=write_attrs,
        )

        # Invalidate downstream wavelet output and lifetime maps. Each of
        # these is derived from the (g, s) we just rewrote — leaving them
        # in place lets a "Filtered" toggle display a stale wavelet result
        # computed from the OLD (g, s). Concrete bug this fixes: after a
        # TCSPC import, the first compute_phasor and the first
        # apply_wavelet ran with stale calibration; a later compute_phasor
        # corrected /phasor/<ch>/g, s but left g_filtered untouched, so
        # toggling Filtered showed an apparently "wrong" mask-filtered
        # phasor (Image #3 in the bug report) until the user restarted
        # the app and didn't re-run wavelet.
        for stale in ("g_filtered", "s_filtered", "lifetime_filtered"):
            try:
                deleter = getattr(self._repo, "delete_path", None)
                if deleter is not None:
                    deleter(handle, f"phasor/{channel}/{stale}")
            except Exception:
                logger.debug(
                    "Failed to invalidate phasor/%s/%s; downstream may be stale",
                    channel, stale, exc_info=True,
                )

        n_valid = int(np.isfinite(g_map).sum())
        return PhasorResult(
            g_map=g_map, s_map=s_map,
            channel=channel, harmonic=harmonic, n_valid=n_valid,
        )
