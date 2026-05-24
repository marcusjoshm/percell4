"""Use case: compute lifetime map from phasor data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.flim.phasor import median_filter_gs, phasor_to_lifetime
from percell4.ports.dataset_repository import DatasetRepository
from percell4.domain.errors import NoDatasetError

# Valid lifetime sources, in user-facing order.
LIFETIME_SOURCES = ("unfiltered", "median", "wavelet")


def lifetime_channel_name(source_channel: str, source: str) -> str:
    """Canonical channel name for a derived lifetime channel.

    Encodes the source channel and the filter method so the three sources
    coexist as distinct channels (``ch0_unfiltered_lifetime``,
    ``ch0_median_lifetime``, ``ch0_wavelet_lifetime``). Re-running Compute
    Lifetime with the same source overwrites that channel's slice.
    """
    return f"{source_channel}_{source}_lifetime"


@dataclass
class LifetimeResult:
    """Result of a lifetime computation."""

    lifetime: NDArray[np.float32]
    channel: str  # the SOURCE photon channel (e.g. "ch0")
    channel_name: str  # the registered derived-channel name
    source: str  # one of LIFETIME_SOURCES
    mean_tau: float | None
    frequency_mhz: float
    median_size: int | None = None  # set when source == "median"


class ComputeLifetime:
    """Compute lifetime from phasor G/S using an explicit, caller-chosen source.

    The lifetime is written as a derived channel slice of ``/intensity`` and
    registered in ``channel_names`` so it shows up wherever ordinary
    channels do (Session.active_channel, the channel selector, the viewer).
    Three filter sources produce three distinct channels per source
    photon channel; re-running with the same source overwrites that slice.

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
        for intensive phasor quantities); the resulting lifetime map is
        upsampled back to ``native_shape`` before being appended to
        ``/intensity`` so the channel sits at the canonical resolution.
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

        # Time-lapse intensities live as (T, H, W) or (T, C, H, W); appending
        # a single-timepoint 2D lifetime would either need broadcasting
        # across T or per-timepoint compute. Neither is wired here today
        # (mirrors the "Add Channel" dialog's same limitation), so refuse
        # explicitly rather than write a wrong-shape slice.
        n_timepoints = int(meta.get("n_timepoints", 1) or 1)
        if n_timepoints > 1:
            raise ValueError(
                "Compute Lifetime as a channel is not yet supported for "
                "time-lapse datasets (n_timepoints > 1)."
            )

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

        # Upsample back to native_shape if we computed at a binned view.
        # The resulting 2D plane is what gets appended to /intensity.
        created_at_bin: int | None = None
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
            created_at_bin = int(view_bin)

        # Append (or replace) the lifetime as a new /intensity channel
        # slice. Mirrors gui/add_layer_dialog._write_layer's Channel branch
        # so the on-disk shape contract stays consistent: 2D → (2, H, W),
        # (C, H, W) → (C+1, H, W). Replacing a same-named channel updates
        # the slice in-place without growing C.
        channel_name = lifetime_channel_name(channel, source)
        names = list(meta.get("channel_names", []))
        existing: NDArray | None = None
        try:
            existing = self._repo.read_array(handle, "intensity")
        except KeyError:
            existing = None

        lifetime_2d = lifetime.astype(np.float32, copy=False)
        if existing is None:
            stacked = lifetime_2d
            dims = ["H", "W"]
            names = [channel_name]
        elif channel_name in names:
            # Overwrite existing slice in place.
            idx = names.index(channel_name)
            if existing.ndim == 2:
                stacked = lifetime_2d
                dims = ["H", "W"]
            else:
                stacked = existing.copy()
                stacked[idx] = lifetime_2d
                dims = ["C", "H", "W"]
        else:
            if existing.ndim == 2:
                stacked = np.stack([existing, lifetime_2d], axis=0)
            else:
                stacked = np.concatenate(
                    [existing, lifetime_2d[np.newaxis]], axis=0
                )
            dims = ["C", "H", "W"]
            names.append(channel_name)

        write_attrs: dict = {"dims": dims}
        if created_at_bin is not None:
            write_attrs["created_at_bin"] = created_at_bin
        self._repo.write_array(handle, "intensity", stacked, attrs=write_attrs)

        # Persist channel_names + n_channels to /metadata so the new
        # channel survives a reload, and refresh the session's in-memory
        # snapshot so the channel selector picks it up immediately.
        self._repo.write_metadata(
            handle, {"channel_names": list(names), "n_channels": len(names)}
        )
        self._session.refresh_resource_lists(channel_names=list(names))

        valid = np.isfinite(lifetime)
        mean_tau = float(np.nanmean(lifetime[valid])) if valid.any() else None

        return LifetimeResult(
            lifetime=lifetime,
            channel=channel,
            channel_name=channel_name,
            source=source,
            mean_tau=mean_tau,
            frequency_mhz=float(freq),
            median_size=applied_median_size,
        )
