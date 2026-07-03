"""Use case: load cached phasor + wavelet results from the dataset.

Single read path consumed by both the FlimPanel button handlers
(_on_compute_phasor / _on_apply_wavelet) and the Phasor window's
auto-load on showEvent / active-channel switch. Eliminates duplicate
read code; keeps cache semantics in one tested place.

Trusts the upstream cache-invalidation chain — TCSPC re-import clears
/phasor/<ch> via add_decay_to_dataset; recompute_phasor deletes stale
g_filtered/s_filtered/lifetime_filtered. A non-empty /phasor/<ch> is,
by invariant, aligned with the current /decay/<ch>. Documented in
docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md.

Asymmetric cache (g present, s missing) cannot occur under normal
operation but is defended against — a missing s after g succeeded
raises NoCachedPhasorError so callers fall through to compute rather
than surfacing a stack trace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoCachedPhasorError, NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository

logger = logging.getLogger(__name__)


@dataclass
class CachedPhasorResult:
    """Result of reading cached phasor data for one channel.

    Fields are scoped to what the two consumers (FlimPanel buttons,
    PhasorPlot auto-load) actually use. ``cached_filter_level`` is the
    DTCWT level ApplyWavelet stamped on ``g_filtered``, surfaced via the
    repo port's ``read_array_attrs`` so the Apply-Wavelet handler can
    recompute when the user picks a different level instead of serving a
    stale cache. It is ``None`` when there is no filtered cache, when the
    attr is absent (pre-attr files), or when the repo does not implement
    ``read_array_attrs`` (test fakes). ``cached_harmonic`` is the Fourier
    harmonic ComputePhasor stamped on the raw ``g`` array, surfaced the
    same way so the Compute-Phasor handler can recompute when the user
    picks a different harmonic instead of serving a stale cache computed
    at another harmonic. It is ``None`` when the attr is absent (pre-attr
    files) or the repo lacks ``read_array_attrs`` (test fakes). Remaining
    writer attrs (flim_frequency_mhz) stay unsurfaced — no consumer needs
    them.
    """

    g_map: NDArray[np.float32]
    s_map: NDArray[np.float32]
    g_filtered: NDArray[np.float32] | None
    s_filtered: NDArray[np.float32] | None
    intensity: NDArray[np.float32] | None
    channel: str
    cached_filter_level: int | None = None
    cached_harmonic: int | None = None


class LoadCachedPhasor:
    """Load cached /phasor/<channel>/{g,s,g_filtered,s_filtered} from the active dataset.

    Raises:
        NoDatasetError: when ``session.dataset`` is None.
        NoCachedPhasorError: when /phasor/<channel>/g is absent.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(self, channel: str, view_bin: int = 1) -> CachedPhasorResult:
        """Load cached phasor at the session view bin.

        ``view_bin`` is the session-level view bin (>= 1). All reads
        forward it to the store's per-path dispatch:

          * ``phasor/<ch>/{g,s,g_filtered,s_filtered}`` -> mean_bin_2d
            (intensive quantities; magnitudes preserved at any k)
          * ``decay/<ch>`` -> sum_bin_decay (T axis preserved)

        So the returned (g, s) and intensity-from-decay are all at the
        binned shape that the phasor plot expects to display.
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Required: raw g, s. KeyError on either is a missing-cache signal.
        try:
            g_map = self._repo.read_array(
                handle, f"phasor/{channel}/g", view_bin=view_bin
            )
        except KeyError as exc:
            raise NoCachedPhasorError(channel) from exc

        try:
            s_map = self._repo.read_array(
                handle, f"phasor/{channel}/s", view_bin=view_bin
            )
        except KeyError as exc:
            # Asymmetric cache: g present, s missing. Should not happen
            # under normal compute_phasor write order but defend against
            # crash-mid-write so callers fall through to compute rather
            # than crash with a bare KeyError.
            logger.warning(
                "Asymmetric phasor cache for channel %s: g present, s missing. "
                "Treating as no cache.", channel,
            )
            raise NoCachedPhasorError(channel) from exc

        # Optional filtered results — both must be present together.
        g_filtered: NDArray[np.float32] | None = None
        s_filtered: NDArray[np.float32] | None = None
        try:
            g_filtered = self._repo.read_array(
                handle, f"phasor/{channel}/g_filtered", view_bin=view_bin,
            )
            s_filtered = self._repo.read_array(
                handle, f"phasor/{channel}/s_filtered", view_bin=view_bin,
            )
        except KeyError:
            # Asymmetric filtered cache: g_filtered present without
            # s_filtered (or vice versa). Treat as no filtered cache.
            if g_filtered is not None and s_filtered is None:
                logger.warning(
                    "Asymmetric wavelet cache for channel %s: g_filtered "
                    "present, s_filtered missing. Treating as no filtered "
                    "cache.", channel,
                )
            g_filtered = None
            s_filtered = None

        # Time-lapse: /phasor/<ch>/{g,s,...} are (T_acq, H, W). Slice the active
        # acquisition frame (clamped) so consumers get a 2-D map matching the
        # napari dims slider — never a combined-all-timepoints cloud. Legacy 2-D
        # phasor passes through unchanged.
        is_timelapse = g_map.ndim == 3
        t_acq = 0
        if is_timelapse:
            nt = int(g_map.shape[0])
            t_acq = max(0, min(int(self._session.active_timepoint), nt - 1))
            g_map = g_map[t_acq]
            s_map = s_map[t_acq]
            if g_filtered is not None and g_filtered.ndim == 3:
                g_filtered = g_filtered[t_acq]
            if s_filtered is not None and s_filtered.ndim == 3:
                s_filtered = s_filtered[t_acq]

        # Filter level stamped on g_filtered by ApplyWavelet — lets the
        # Apply-Wavelet handler detect a level change and recompute rather
        # than serving a stale cache. Read defensively: a repo without
        # read_array_attrs (test fakes) or a pre-attr file leaves this None,
        # which the caller treats as "level unknown → recompute".
        cached_filter_level: int | None = None
        if g_filtered is not None and s_filtered is not None:
            attr_reader = getattr(self._repo, "read_array_attrs", None)
            if attr_reader is not None:
                try:
                    attrs = attr_reader(handle, f"phasor/{channel}/g_filtered")
                    level = attrs.get("filter_level")
                    if level is not None:
                        cached_filter_level = int(level)
                except Exception:
                    logger.debug(
                        "Failed to read cached wavelet filter_level for %s",
                        channel, exc_info=True,
                    )

        # Harmonic stamped on the raw g array by ComputePhasor — lets the
        # Compute-Phasor handler detect a harmonic change and recompute
        # rather than serving a cache computed at a different harmonic.
        # Same defensive read as filter_level: a repo without
        # read_array_attrs (test fakes) or a pre-attr file leaves this
        # None, which the caller treats as "harmonic unknown → serve cache".
        cached_harmonic: int | None = None
        attr_reader = getattr(self._repo, "read_array_attrs", None)
        if attr_reader is not None:
            try:
                attrs = attr_reader(handle, f"phasor/{channel}/g")
                harm = attrs.get("harmonic")
                if harm is not None:
                    cached_harmonic = int(harm)
            except Exception:
                logger.debug(
                    "Failed to read cached phasor harmonic for %s",
                    channel, exc_info=True,
                )

        # Decay-derived intensity for the intensity-weighted histogram.
        # Per the cross-layer-alignment learning, intensity MUST come
        # from decay.sum(axis=-1), NOT from /intensity[ch_idx]. None is
        # acceptable — the phasor window's set_phasor_data accepts None.
        intensity: NDArray[np.float32] | None = None
        try:
            if is_timelapse:
                # Active decay frame (4-D /decay) — the SAME frame as the phasor
                # sliced above (cross-layer alignment across the new axis).
                reader = getattr(self._repo, "read_decay", None)
                if reader is not None:
                    decay_frame = reader(
                        handle, channel, view_bin=view_bin, timepoint=t_acq
                    )
                else:
                    decay_frame = self._repo.read_array(
                        handle, f"decay/{channel}", view_bin=view_bin
                    )[t_acq]
                intensity = decay_frame.sum(axis=-1).astype(np.float32)
            else:
                decay = self._repo.read_array(
                    handle, f"decay/{channel}", view_bin=view_bin
                )
                intensity = decay.sum(axis=-1).astype(np.float32)
        except KeyError:
            pass

        return CachedPhasorResult(
            g_map=g_map.astype(np.float32, copy=False),
            s_map=s_map.astype(np.float32, copy=False),
            g_filtered=(
                g_filtered.astype(np.float32, copy=False)
                if g_filtered is not None else None
            ),
            s_filtered=(
                s_filtered.astype(np.float32, copy=False)
                if s_filtered is not None else None
            ),
            intensity=intensity,
            channel=channel,
            cached_filter_level=cached_filter_level,
            cached_harmonic=cached_harmonic,
        )
