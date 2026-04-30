"""Use case: apply DTCWT wavelet denoising to phasor data."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError

logger = logging.getLogger(__name__)


@dataclass
class WaveletResult:
    """Result of wavelet filtering."""

    g_filtered: NDArray[np.float32]
    s_filtered: NDArray[np.float32]
    lifetime: NDArray[np.float32] | None
    channel: str
    filter_level: int
    n_valid: int


class ApplyWavelet:
    """Apply DTCWT wavelet denoising to an existing phasor dataset.

    Reads unfiltered phasor G/S + intensity from the repository,
    runs wavelet denoising, writes filtered results.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def _read_fresh_metadata(self, handle) -> dict:
        """Read /metadata fresh from disk, with snapshot fallback."""
        reader = getattr(self._repo, "read_metadata", None)
        if reader is not None:
            try:
                return reader(handle)
            except Exception:
                pass
        return dict(handle.metadata)

    def execute(self, channel: str, filter_level: int = 9) -> WaveletResult:
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Read phasor G/S maps
        try:
            g_map = self._repo.read_array(handle, f"phasor/{channel}/g")
            s_map = self._repo.read_array(handle, f"phasor/{channel}/s")
        except KeyError:
            raise ValueError(
                f"No phasor data for '{channel}'. Compute Phasor first."
            )

        # Compute the per-pixel intensity from the decay layer itself —
        # NOT from the /intensity stack. The wavelet filter does
        # ``f_real = g * intensity`` (pointwise) and the same for s, so
        # ``intensity`` MUST be spatially aligned with g and s. The /intensity
        # stack can drift out of alignment when /decay is rewritten by a
        # later add-layer pass (different stitching, rotation, or a
        # different acquisition mode); using ``decay.sum(axis=-1)`` here
        # makes the alignment guaranteed by construction. compute_phasor
        # already follows the same pattern (compute_phasor.py:60).
        try:
            decay = self._repo.read_array(handle, f"decay/{channel}")
        except KeyError:
            raise ValueError(
                f"No /decay/{channel} layer for wavelet filter intensity weighting."
            )
        intensity = decay.sum(axis=-1).astype(np.float64)

        # Get frequency for lifetime calculation. Read /metadata fresh
        # from disk — handle.metadata is a snapshot from set_dataset
        # time and may not reflect flim_frequency_mhz if TCSPC data was
        # imported in this session. See compute_phasor for the full
        # explanation of this snapshot-staleness hazard.
        meta = self._read_fresh_metadata(handle)
        freq = meta.get("flim_frequency_mhz", None)
        omega = None
        if freq and freq > 0:
            omega = 2.0 * np.pi * freq

        # Run wavelet denoising
        from percell4.domain.flim.wavelet_filter import denoise_phasor

        result = denoise_phasor(
            g_map, s_map, intensity.astype(np.float64),
            filter_level=filter_level,
            omega=omega,
        )

        g_filtered = result["G"]
        s_filtered = result["S"]

        # Write filtered results
        self._repo.write_array(
            handle, f"phasor/{channel}/g_filtered", g_filtered,
            attrs={"dims": ["H", "W"], "channel": channel, "filter_level": filter_level},
        )
        self._repo.write_array(
            handle, f"phasor/{channel}/s_filtered", s_filtered,
            attrs={"dims": ["H", "W"], "channel": channel, "filter_level": filter_level},
        )

        lifetime = result.get("T")
        if lifetime is not None:
            self._repo.write_array(
                handle, f"phasor/{channel}/lifetime_filtered", lifetime,
                attrs={"dims": ["H", "W"], "channel": channel},
            )

        n_valid = int(np.isfinite(g_filtered).sum())
        return WaveletResult(
            g_filtered=g_filtered, s_filtered=s_filtered,
            lifetime=lifetime, channel=channel,
            filter_level=filter_level, n_valid=n_valid,
        )
