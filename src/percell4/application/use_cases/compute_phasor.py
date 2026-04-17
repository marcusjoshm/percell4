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

    def execute(self, channel: str, harmonic: int = 1) -> PhasorResult:
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Read decay data
        decay_path = f"decay/{channel}"
        try:
            decay = self._repo.read_array(handle, decay_path)
        except KeyError:
            try:
                decay = self._repo.read_array(handle, "decay")
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

        # Apply per-channel calibration if available
        meta = handle.metadata
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

        n_valid = int(np.isfinite(g_map).sum())
        return PhasorResult(
            g_map=g_map, s_map=s_map,
            channel=channel, harmonic=harmonic, n_valid=n_valid,
        )
