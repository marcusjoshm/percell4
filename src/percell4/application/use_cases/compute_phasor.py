"""Use case: compute phasor G/S maps from TCSPC decay data."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter

from percell4.application.session import Session
from percell4.domain.flim.phasor import compute_phasor
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError

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

    Reads decay data from the repository, applies calibration and
    median filtering, writes results to store.
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
        self, channel: str, harmonic: int = 1, view_bin: int = 1
    ) -> PhasorResult:
        """Compute phasor G/S maps for ``channel``.

        ``view_bin`` is the session-level view bin (>= 1). Decay is read
        from the repository at the binned resolution. At k=1 behavior is
        byte-identical to before the binning model existed.
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Read decay data (view_bin applied by the store dispatch on
        # /decay/* paths via sum_bin_decay).
        decay_path = f"decay/{channel}"
        try:
            decay = self._repo.read_array(handle, decay_path, view_bin=view_bin)
        except KeyError:
            try:
                decay = self._repo.read_array(handle, "decay", view_bin=view_bin)
            except KeyError:
                raise ValueError(
                    f"No TCSPC data found for '{channel}'. Import with FLIM enabled."
                )

        # Compute phasor
        g_map, s_map = compute_phasor(decay, harmonic=harmonic)

        # Zero out low-photon pixels
        intensity_sum = decay.sum(axis=-1).astype(np.float32)
        low_signal = intensity_sum <= 0
        g_map[low_signal] = 0.0
        s_map[low_signal] = 0.0

        # Apply per-channel calibration if available.
        # Read /metadata FRESH from disk rather than from handle.metadata —
        # handle.metadata is a snapshot taken when the dataset was opened
        # via set_dataset. If TCSPC data was appended in this session,
        # the import wrote flim_cal_phase_<ch> / flim_cal_mod_<ch> /
        # flim_frequency_mhz to /metadata AFTER the snapshot, so the
        # snapshot has stale defaults (phase=0, mod=1). Without a fresh
        # read here, calibration is silently skipped and the resulting
        # phasor is wildly off — looks "fixed" only after app restart
        # because the new snapshot picks up the disk values.
        meta = self._read_fresh_metadata(handle)
        cal_phase = float(meta.get(f"flim_cal_phase_{channel}", 0.0))
        cal_mod = float(meta.get(f"flim_cal_mod_{channel}", 1.0))

        if cal_phase != 0.0 or cal_mod != 1.0:
            cos_phi = np.cos(cal_phase)
            sin_phi = np.sin(cal_phase)
            g_cal = g_map * cal_mod * cos_phi - s_map * cal_mod * sin_phi
            s_cal = g_map * cal_mod * sin_phi + s_map * cal_mod * cos_phi
            g_map = g_cal.astype(np.float32)
            s_map = s_cal.astype(np.float32)

        # Spatial median filter (3x3)
        g_map = median_filter(g_map, size=3).astype(np.float32)
        s_map = median_filter(s_map, size=3).astype(np.float32)

        # Write to store
        self._repo.write_array(
            handle, f"phasor/{channel}/g", g_map,
            attrs={"dims": ["H", "W"], "channel": channel, "harmonic": harmonic},
        )
        self._repo.write_array(
            handle, f"phasor/{channel}/s", s_map,
            attrs={"dims": ["H", "W"], "channel": channel, "harmonic": harmonic},
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
