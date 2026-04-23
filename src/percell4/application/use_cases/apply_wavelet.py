"""Use case: apply DTCWT wavelet denoising to phasor data.

Dispatches between the two registered filter algorithms (``boe_2021``
and ``jcb_2025``) via :mod:`percell4.domain.flim.wavelet`. Writes the
three filtered datasets (``g_filtered``, ``s_filtered``,
``lifetime_filtered``) under a single HDF5 handle with rich provenance
attrs and a ``filter_status`` sentinel so partial writes are
detectable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

import percell4
from percell4 import store_schema
from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.domain.flim.wavelet import (
    ALGORITHM_CHOICES,
    DEFAULT_FILTER_LEVEL,
    AlgorithmId,
    denoise_phasor,
)
from percell4.ports.dataset_repository import DatasetRepository
from percell4.store import WriteItem

logger = logging.getLogger(__name__)

_DEFAULT_ALGORITHM: AlgorithmId = "boe_2021"


@dataclass
class WaveletResult:
    """Result of wavelet filtering."""

    g_filtered: NDArray[np.float32]
    s_filtered: NDArray[np.float32]
    lifetime: NDArray[np.float32] | None
    channel: str
    filter_level: int
    n_valid: int
    algorithm: str = _DEFAULT_ALGORITHM  # "boe_2021" or "jcb_2025"


class ApplyWavelet:
    """Apply DTCWT wavelet denoising to an existing phasor dataset.

    Reads unfiltered phasor G/S + intensity from the repository, runs
    the selected wavelet denoiser, and writes the filtered results
    through a multi-dataset write so all three filtered outputs agree
    on the algorithm attr.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(
        self,
        channel: str,
        *,
        filter_level: int = DEFAULT_FILTER_LEVEL,
        algorithm: AlgorithmId = _DEFAULT_ALGORITHM,
    ) -> WaveletResult:
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Read unfiltered phasor G/S maps.
        try:
            g_map = self._repo.read_array(handle, f"phasor/{channel}/g")
            s_map = self._repo.read_array(handle, f"phasor/{channel}/s")
        except KeyError:
            raise ValueError(
                f"No phasor data for '{channel}'. Compute Phasor first."
            ) from None

        # Read intensity (2D or 3D by channel).
        try:
            intensity_data = self._repo.read_array(handle, "intensity")
        except KeyError:
            raise ValueError("No intensity data in dataset") from None

        if intensity_data.ndim == 3:
            channel_names = list(handle.metadata.get("channel_names", []))
            if channel in channel_names:
                intensity = intensity_data[channel_names.index(channel)]
            else:
                intensity = intensity_data[0]
        else:
            intensity = intensity_data

        # Derive angular frequency for lifetime (optional).
        freq = handle.metadata.get("flim_frequency_mhz")
        omega = 2.0 * np.pi * freq if freq and freq > 0 else None

        # Run the selected filter. MissingOptionalDependencyError is
        # raised by the dispatcher if dtcwt is not installed; let it
        # bubble unchanged so GUI callers can render an install hint.
        result = denoise_phasor(
            g_map, s_map, intensity.astype(np.float64),
            algorithm=algorithm,
            filter_level=filter_level,
            omega=omega,
        )

        g_filtered = result["G"]
        s_filtered = result["S"]
        lifetime = result.get("T")

        # Build provenance attrs once so all three datasets agree.
        attrs = self._build_provenance_attrs(
            channel=channel,
            algorithm=algorithm,
            filter_level=filter_level,
        )

        # Write g/s/lifetime atomically under a single h5py.File handle,
        # bracketed by a `filter_status` sentinel on the channel group
        # so a crash mid-write leaves detectable state.
        items: list[WriteItem] = [
            WriteItem(
                path=store_schema.PHASOR_G_FILTERED.format(channel=channel),
                array=g_filtered,
                attrs=attrs,
            ),
            WriteItem(
                path=store_schema.PHASOR_S_FILTERED.format(channel=channel),
                array=s_filtered,
                attrs=attrs,
            ),
        ]
        if lifetime is not None:
            lifetime_attrs = dict(attrs)
            if omega is not None:
                lifetime_attrs["omega_rad_per_ns"] = float(omega)
            items.append(WriteItem(
                path=store_schema.PHASOR_LIFETIME_FILTERED.format(
                    channel=channel),
                array=lifetime,
                attrs=lifetime_attrs,
            ))

        group_path = f"phasor/{channel}"
        self._repo.write_arrays(
            handle, items,
            group_attrs={
                group_path: {
                    store_schema.FILTER_STATUS_ATTR:
                        store_schema.FILTER_STATUS_COMPLETE,
                },
            },
        )

        # Cross-dataset invariant: all three filtered datasets must
        # carry matching algorithm attrs. Assertion helps catch
        # repository-adapter bugs during development.
        self._assert_algorithm_attrs_agree(handle, channel, algorithm)

        n_valid = int(np.isfinite(g_filtered).sum())
        return WaveletResult(
            g_filtered=g_filtered,
            s_filtered=s_filtered,
            lifetime=lifetime,
            channel=channel,
            filter_level=filter_level,
            n_valid=n_valid,
            algorithm=algorithm,
        )

    # ── Helpers ──────────────────────────────────────────────

    def _build_provenance_attrs(
        self, *, channel: str, algorithm: AlgorithmId, filter_level: int,
    ) -> dict:
        params = store_schema.ALGORITHM_PROVENANCE[algorithm]
        n_local = params["n_local_window"]
        if n_local is None:
            # JCB's N depends on filter_level at runtime.
            n_local = store_schema.jcb_n_local_window(filter_level)

        try:
            import dtcwt
            dtcwt_version = getattr(dtcwt, "__version__", "unknown")
        except ImportError:  # should never reach here — filter already ran
            dtcwt_version = "unavailable"

        return store_schema.wavelet_provenance_attrs(
            channel=channel,
            algorithm=algorithm,
            filter_level=filter_level,
            biort=params["biort"],
            qshift=params["qshift"],
            n_local_window=int(n_local),
            sigma_g_estimator=params["sigma_g_estimator"],
            shrinkage=params["shrinkage"],
            dtcwt_version=dtcwt_version,
            percell4_version=getattr(percell4, "__version__", "unknown"),
        )

    def _assert_algorithm_attrs_agree(
        self, handle, channel: str, expected: str,
    ) -> None:
        for tmpl in store_schema.FILTERED_DATASETS:
            path = tmpl.format(channel=channel)
            try:
                attrs = self._repo.read_array_attrs(handle, path)
            except KeyError:
                # lifetime_filtered may be absent if omega is None;
                # that's the only expected miss.
                if tmpl is store_schema.PHASOR_LIFETIME_FILTERED:
                    continue
                raise
            got = attrs.get(store_schema.ALGORITHM_ATTR)
            if got != expected:
                logger.error(
                    "Algorithm attr mismatch at %s: expected %r, got %r",
                    path, expected, got,
                )
                raise AssertionError(
                    f"Algorithm attr mismatch at {path}: "
                    f"expected {expected!r}, got {got!r}"
                )


# Re-export for GUI callers so they don't need two imports.
__all__ = ["ApplyWavelet", "WaveletResult", "ALGORITHM_CHOICES"]
