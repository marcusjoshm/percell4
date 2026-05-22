"""Use case: compute lifetime map from phasor data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.flim.phasor import median_filter_gs, phasor_to_lifetime
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError, NoMaskError, NoSegmentationError

# Valid lifetime sources, in user-facing order.
LIFETIME_SOURCES = ("unfiltered", "median", "wavelet")


@dataclass
class LifetimeResult:
    """Result of a lifetime computation."""

    lifetime: NDArray[np.float32]
    channel: str
    source: str  # one of LIFETIME_SOURCES
    mean_tau: float | None
    frequency_mhz: float
    median_size: int | None = None  # set when source == "median"


class ComputeLifetime:
    """Compute lifetime from phasor G/S using an explicit, caller-chosen source.

    The source is one of ``unfiltered`` (raw ``phasor/<ch>/{g,s}``),
    ``median`` (a spatial median of the raw maps), or ``wavelet`` (the
    DTCWT result at ``phasor/<ch>/{g_filtered,s_filtered}``). There is no
    implicit fallback — an absent wavelet result raises rather than
    silently degrading to unfiltered.
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

    def execute(
        self,
        channel: str,
        source: str = "unfiltered",
        median_size: int = 3,
        view_bin: int = 1,
    ) -> LifetimeResult:
        """Compute lifetime from phasor g/s for ``channel``.

        ``source`` selects which phasor maps feed the lifetime:
        ``"unfiltered"`` reads the raw ``phasor/<ch>/{g,s}``; ``"median"``
        applies a ``median_size``-pixel square median to those raw maps;
        ``"wavelet"`` reads ``phasor/<ch>/{g_filtered,s_filtered}`` and
        raises if they are absent (compute the wavelet filter first).

        ``view_bin`` is the session-level view bin (>= 1). G and S are
        read at the binned resolution via the store dispatch (mean_bin
        for intensive phasor quantities).
        """
        if source not in LIFETIME_SOURCES:
            raise ValueError(
                f"source must be one of {LIFETIME_SOURCES}, got {source!r}"
            )

        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Read /metadata fresh — handle.metadata is a snapshot that
        # doesn't reflect in-session writes (e.g., TCSPC import).
        meta = self._read_fresh_metadata(handle)
        freq = meta.get("flim_frequency_mhz", None)
        if not freq or freq <= 0:
            raise ValueError("No laser frequency in metadata")

        applied_median_size: int | None = None
        if source == "wavelet":
            try:
                g = self._repo.read_array(
                    handle, f"phasor/{channel}/g_filtered", view_bin=view_bin
                )
                s = self._repo.read_array(
                    handle, f"phasor/{channel}/s_filtered", view_bin=view_bin
                )
            except KeyError:
                raise ValueError(
                    f"No wavelet-filtered phasor for '{channel}'. "
                    "Apply the wavelet filter first."
                )
        else:
            try:
                g = self._repo.read_array(
                    handle, f"phasor/{channel}/g", view_bin=view_bin
                )
                s = self._repo.read_array(
                    handle, f"phasor/{channel}/s", view_bin=view_bin
                )
            except KeyError:
                raise ValueError(
                    f"No phasor data for '{channel}'. Compute Phasor first."
                )
            if source == "median":
                g, s = median_filter_gs(g, s, size=median_size)
                applied_median_size = int(median_size)

        lifetime = phasor_to_lifetime(g, s, frequency_mhz=freq)

        # Bin-aware write: lifetime stays at native_shape so the canonical
        # /phasor/<ch>/lifetime path doesn't shrink at higher view bins.
        write_attrs: dict = {
            "dims": ["H", "W"], "channel": channel, "source": source,
        }
        if applied_median_size is not None:
            write_attrs["median_size"] = applied_median_size
        if view_bin > 1:
            from percell4.domain.io.view_bin import nn_upsample_2d
            native = meta.get("native_shape")
            if native is None:
                raise ValueError(
                    "Cannot write a binned lifetime: "
                    "/metadata.native_shape is missing."
                )
            target = (int(native[0]), int(native[1]))
            lifetime = nn_upsample_2d(
                lifetime, view_bin, target_hw=target
            ).astype(lifetime.dtype, copy=False)
            write_attrs["created_at_bin"] = int(view_bin)

        self._repo.write_array(
            handle, f"phasor/{channel}/lifetime", lifetime,
            attrs=write_attrs,
        )

        valid = np.isfinite(lifetime)
        mean_tau = float(np.nanmean(lifetime[valid])) if valid.any() else None

        return LifetimeResult(
            lifetime=lifetime,
            channel=channel,
            source=source,
            mean_tau=mean_tau,
            frequency_mhz=float(freq),
            median_size=applied_median_size,
        )
