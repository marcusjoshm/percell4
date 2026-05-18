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
    PhasorPlot auto-load) actually use. Other attrs that
    compute_phasor / apply_wavelet write (harmonic, filter_level,
    flim_frequency_mhz) are not surfaced here — the existing repo port
    does not expose array-attr reads, and neither consumer needs them
    for cache correctness. If a future caller needs them, the right
    move is to extend the repo port, not to bolt on a separate read.
    """

    g_map: NDArray[np.float32]
    s_map: NDArray[np.float32]
    g_filtered: NDArray[np.float32] | None
    s_filtered: NDArray[np.float32] | None
    intensity: NDArray[np.float32] | None
    channel: str


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

        # Decay-derived intensity for the intensity-weighted histogram.
        # Per the cross-layer-alignment learning, intensity MUST come
        # from decay.sum(axis=-1), NOT from /intensity[ch_idx]. None is
        # acceptable — the phasor window's set_phasor_data accepts None.
        intensity: NDArray[np.float32] | None = None
        try:
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
        )
