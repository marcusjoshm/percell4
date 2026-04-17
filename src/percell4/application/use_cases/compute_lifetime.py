"""Use case: compute lifetime map from phasor data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.flim.phasor import phasor_to_lifetime
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError


@dataclass
class LifetimeResult:
    """Result of a lifetime computation."""

    lifetime: NDArray[np.float32]
    channel: str
    source: str  # "filtered" or "unfiltered"
    mean_tau: float | None
    frequency_mhz: float


class ComputeLifetime:
    """Compute lifetime from phasor G/S. Prefers filtered phasor if available."""

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(self, channel: str) -> LifetimeResult:
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        meta = handle.metadata
        freq = meta.get("flim_frequency_mhz", None)
        if not freq or freq <= 0:
            raise ValueError("No laser frequency in metadata")

        # Try filtered phasor first, fall back to unfiltered
        try:
            g = self._repo.read_array(handle, f"phasor/{channel}/g_filtered")
            s = self._repo.read_array(handle, f"phasor/{channel}/s_filtered")
            source = "filtered"
        except KeyError:
            try:
                g = self._repo.read_array(handle, f"phasor/{channel}/g")
                s = self._repo.read_array(handle, f"phasor/{channel}/s")
                source = "unfiltered"
            except KeyError:
                raise ValueError(
                    f"No phasor data for '{channel}'. Compute Phasor first."
                )

        lifetime = phasor_to_lifetime(g, s, frequency_mhz=freq)

        self._repo.write_array(
            handle, f"phasor/{channel}/lifetime", lifetime,
            attrs={"dims": ["H", "W"], "channel": channel, "source": source},
        )

        valid = np.isfinite(lifetime)
        mean_tau = float(np.nanmean(lifetime[valid])) if valid.any() else None

        return LifetimeResult(
            lifetime=lifetime,
            channel=channel,
            source=source,
            mean_tau=mean_tau,
            frequency_mhz=float(freq),
        )
