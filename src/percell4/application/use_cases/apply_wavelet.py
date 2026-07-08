"""Use case: apply DTCWT wavelet denoising to phasor data."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository

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

    def execute(
        self, channel: str, filter_level: int = 9, view_bin: int = 1
    ) -> WaveletResult:
        """Apply wavelet denoising to phasor G/S maps for ``channel``.

        ``view_bin`` is the session-level view bin (>= 1). G, S, and the
        decay used for intensity weighting are all read at the binned
        resolution -- the store dispatch handles the per-path rule
        (mean_bin for /phasor/*, sum_bin_decay for /decay/*).
        """
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Read phasor G/S maps
        try:
            g_map = self._repo.read_array(
                handle, f"phasor/{channel}/g", view_bin=view_bin
            )
            s_map = self._repo.read_array(
                handle, f"phasor/{channel}/s", view_bin=view_bin
            )
        except KeyError:
            raise ValueError(
                f"No phasor data for '{channel}'. Compute Phasor first."
            )

        # Per-pixel intensity comes from /decay (NEVER the /intensity stack) so
        # it stays spatially aligned with g/s — denoise does f_real = g*intensity
        # pointwise. On a time-lapse dataset /decay is 4-D and the phasor is
        # (T_acq, H, W); each frame is filtered independently because the 2-D
        # DTCWT kernel can't take a 3-D stack (it does ``h, w = data.shape``).
        def _intensity_frame(tp):
            """(H, W) decay-derived intensity for acquisition frame ``tp`` (or
            the whole 3-D decay sum when ``tp`` is None)."""
            try:
                if tp is not None:
                    reader = getattr(self._repo, "read_decay", None)
                    if reader is not None:
                        dk = reader(
                            handle, channel, view_bin=view_bin, timepoint=tp
                        )
                    else:
                        dk = self._repo.read_array(
                            handle, f"decay/{channel}", view_bin=view_bin
                        )[tp]
                else:
                    dk = self._repo.read_array(
                        handle, f"decay/{channel}", view_bin=view_bin
                    )
            except KeyError:
                raise ValueError(
                    f"No /decay/{channel} layer for wavelet filter "
                    "intensity weighting."
                )
            return dk.sum(axis=-1).astype(np.float64)

        # Frequency for the lifetime map. Read /metadata fresh from disk —
        # handle.metadata is a snapshot from set_dataset time (see compute_phasor
        # for the snapshot-staleness rationale).
        meta = self._read_fresh_metadata(handle)
        freq = meta.get("flim_frequency_mhz", None)
        omega = 2.0 * np.pi * freq if (freq and freq > 0) else None

        from percell4.domain.flim.wavelet_filter import denoise_phasor

        native = meta.get("native_shape")

        def _upsample(arr):
            # Bin-aware: derived maps are stored at native_shape. No-op at k=1.
            if view_bin <= 1 or arr is None:
                return arr
            from percell4.domain.io.view_bin import nn_upsample_2d
            if native is None:
                raise ValueError(
                    "Cannot write a binned wavelet result: "
                    "/metadata.native_shape is missing."
                )
            target = (int(native[0]), int(native[1]))
            return nn_upsample_2d(arr, view_bin, target_hw=target).astype(
                arr.dtype, copy=False
            )

        def _filter_frame(g2d, s2d, tp):
            """Denoise one 2-D (H, W) phasor frame, returning native-shape maps."""
            res = denoise_phasor(
                g2d.astype(np.float64), s2d.astype(np.float64),
                _intensity_frame(tp), filter_level=filter_level, omega=omega,
            )
            return _upsample(res["G"]), _upsample(res["S"]), _upsample(res.get("T"))

        # Time-lapse: (T_acq, H, W) phasor -> filter each frame, stack to
        # (T_acq, H, W). Single-timepoint: a plain 2-D filter (unchanged).
        if g_map.ndim == 3:
            nt = int(g_map.shape[0])
            g_frames, s_frames, lt_frames = [], [], []
            for t in range(nt):
                gf_t, sf_t, lt_t = _filter_frame(g_map[t], s_map[t], t)
                g_frames.append(gf_t)
                s_frames.append(sf_t)
                lt_frames.append(lt_t)
            g_filtered = np.stack(g_frames, axis=0)
            s_filtered = np.stack(s_frames, axis=0)
            lifetime = (
                np.stack(lt_frames, axis=0)
                if all(f is not None for f in lt_frames) else None
            )
            dims = ["Tacq", "H", "W"]
        else:
            g_filtered, s_filtered, lifetime = _filter_frame(g_map, s_map, None)
            dims = ["H", "W"]

        # Write filtered results (dims tracks the time-lapse layout).
        write_attrs: dict = {
            "dims": dims, "channel": channel, "filter_level": filter_level,
        }
        if view_bin > 1:
            write_attrs["created_at_bin"] = int(view_bin)
        self._repo.write_array(
            handle, f"phasor/{channel}/g_filtered", g_filtered, attrs=write_attrs,
        )
        self._repo.write_array(
            handle, f"phasor/{channel}/s_filtered", s_filtered, attrs=write_attrs,
        )

        if lifetime is not None:
            lifetime_attrs: dict = {"dims": dims, "channel": channel}
            if view_bin > 1:
                lifetime_attrs["created_at_bin"] = int(view_bin)
            self._repo.write_array(
                handle, f"phasor/{channel}/lifetime_filtered", lifetime,
                attrs=lifetime_attrs,
            )

        n_valid = int(np.isfinite(g_filtered).sum())
        return WaveletResult(
            g_filtered=g_filtered, s_filtered=s_filtered,
            lifetime=lifetime, channel=channel,
            filter_level=filter_level, n_valid=n_valid,
        )
