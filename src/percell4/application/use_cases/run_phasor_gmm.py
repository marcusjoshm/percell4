"""Use case: fit a GMM to filtered phasor pixels and produce ROI geometries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository

logger = logging.getLogger(__name__)


@dataclass
class PhasorROIGeometry:
    """One GMM cluster expressed as ROI geometry + eigenstructure.

    The phasor plot's ``place_gmm_rois`` method uses ``center``, ``radii``,
    and ``angle_deg`` to construct the ``PhasorROI`` dataclass; the
    eigenstructure fields (``mean_g``, ``mean_s``, ``lambda_*``,
    ``principal_angle_rad``) feed the per-ROI ``GMMFit`` so the
    Selected-ROI panel's cov_f / shift spinboxes can regeneratively
    recompute geometry without re-running the fit.
    """

    center: tuple[float, float]
    radii: tuple[float, float]
    angle_deg: float
    mean_g: float
    mean_s: float
    lambda_major: float
    lambda_minor: float
    principal_angle_rad: float
    label: int  # 1-indexed cluster label, used as the integer label on the mask


@dataclass
class RunPhasorGMMResult:
    """Output of a single GMM run.

    ``dataset_path`` is the snapshot of the source dataset's path captured
    at execute-time. The FlimPanel's ``_on_gmm_finished`` slot compares
    against ``session.dataset.path`` and discards the result if the user
    switched datasets while the worker was running.
    """

    geometries: list[PhasorROIGeometry]
    chosen_n: int
    criterion: str | None
    criterion_value: float | None
    sampled_pixels: int
    dataset_path: Path


class RunPhasorGMM:
    """Fit a GMM to filtered phasor pixels for a given channel.

    Reads phasor (g, s) maps, derives intensity from ``decay.sum(axis=-1)``
    (never ``/intensity[ch_idx]`` — see
    ``flim-phasor-cross-layer-alignment-2026-04-29.md``), composes
    intensity / reference-circle / mask / cell-selection filters via the
    pure ``compute_valid_phasor_pixels``, subsamples to at most
    ``MAX_GMM_PIXELS`` weighted by intensity, fits sklearn's
    GaussianMixture, and returns one ``PhasorROIGeometry`` per component.
    """

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def _read_fresh_metadata(self, handle) -> dict:
        """Read /metadata fresh from disk, with snapshot fallback.

        Mirrors the helper in ``compute_phasor.py`` and ``apply_wavelet.py``.
        Defeats the in-session staleness hazard where ``handle.metadata``
        is a snapshot taken at ``set_dataset`` time and may not reflect
        keys written after that point (e.g., ``flim_frequency_mhz`` from
        a TCSPC import).
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
        self,
        *,
        channel: str,
        shape: str,
        n_components: int | None,
        criterion: str | None,
        cov_f: float = 2.0,
        shift: float = 0.0,
        intensity_threshold: float = 0.0,
        ref_circle_tau_ns: float | None = None,
        ref_circle_radius: float | None = None,
        mask_filter_active: bool = False,
        use_filtered_gs: bool = True,
        harmonic: int = 1,
        n_max: int = 4,
        view_bin: int = 1,
    ) -> RunPhasorGMMResult:
        # Lazy imports — keep this module's import-time cost low and avoid
        # pulling sklearn during application/ load (mirrors the pattern at
        # domain/measure/grouper.py and the compute_phasor lazy-imports
        # for downstream FLIM helpers).
        from percell4.domain.flim.phasor import (
            gmm_eigenstructure,
            gmm_fit_phasor,
            gmm_to_phasor_roi_geometry,
            single_component_fit_phasor,
            universal_circle_gs,
        )
        from percell4.domain.flim.phasor_display import compute_valid_phasor_pixels

        # ── Resolve dataset handle and snapshot identity ─────────
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")
        dataset_path = handle.path

        # ── Read phasor (g, s) ───────────────────────────────────
        # Filtered path means the wavelet output (g_filtered, s_filtered);
        # unfiltered means the raw compute_phasor output. Distinguish the
        # two error messages so the user knows which step is missing.
        if use_filtered_gs:
            try:
                g = self._repo.read_array(
                    handle, f"phasor/{channel}/g_filtered", view_bin=view_bin
                )
                s = self._repo.read_array(
                    handle, f"phasor/{channel}/s_filtered", view_bin=view_bin
                )
            except KeyError:
                raise ValueError(
                    f"Wavelet-filtered phasor not found for channel '{channel}'. "
                    "Apply Wavelet Filter first or uncheck Filtered."
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
                    f"Phasor data not found for channel '{channel}'. Compute Phasor first."
                )

        # Time-lapse: /phasor/<ch>/{g,s} are (T_acq, H, W). Slice the active
        # acquisition frame so g/s are 2-D and align with the (active-frame)
        # labels/mask read below. Single-timepoint phasor is already 2-D.
        if g.ndim == 3:
            t_idx = max(
                0, min(int(self._session.active_timepoint), g.shape[0] - 1)
            )
            g = g[t_idx]
            s = s[t_idx]

        # The active acquisition frame (time-lapse) or None (single-timepoint),
        # used for the decay-derived intensity AND the labels/mask reads so all
        # ravel to the SAME H*W and never mismatch T*H*W vs H*W.
        tp = (
            self._session.active_timepoint
            if self._session.n_timepoints > 1
            else None
        )

        # ── Derive intensity from decay (NEVER /intensity[ch_idx]) ─
        # The cross-layer alignment learning forbids reading /intensity
        # because /decay and /intensity can drift out of alignment after
        # a later add-layer or rotation pass. ``dtype=np.float64`` on the
        # sum prevents precision loss for high-photon-count pixels (sums
        # > 2^24 ≈ 1.7e7 lose precision in float32). On a time-lapse dataset
        # read the active 4-D decay frame (the same frame as g/s above).
        try:
            reader = getattr(self._repo, "read_decay", None)
            if tp is not None and reader is not None:
                decay = reader(handle, channel, view_bin=view_bin, timepoint=tp)
            else:
                decay = self._repo.read_array(
                    handle, f"decay/{channel}", view_bin=view_bin
                )
                if decay.ndim == 4 and tp is not None:
                    decay = decay[tp]
        except KeyError:
            raise ValueError(
                f"No /decay/{channel} layer — cannot derive intensity weights."
            )
        intensity = decay.sum(axis=-1, dtype=np.float64).astype(np.float32)

        # ── Read mask (optional, only when filter is engaged) ────
        mask_array: NDArray[np.uint8] | None = None
        if mask_filter_active and self._session.active_mask is not None:
            try:
                mask_array = self._repo.read_mask(
                    handle, self._session.active_mask, view_bin=view_bin, timepoint=tp
                )
            except (KeyError, ValueError, OSError):
                mask_array = None  # silent bypass — pure helper handles None

        # ── Read segmentation labels for the cell-selection filter ─
        # Brainstorm R6: cell-selection composes via boolean AND with the
        # other filters. Without this read, ``filter_ids`` is silently
        # dropped (compute_valid_phasor_pixels skips the cell filter when
        # labels_flat is None).
        labels_array: NDArray[np.int32] | None = None
        seg_name = self._session.active_segmentation
        if seg_name:
            try:
                labels_array = self._repo.read_labels(
                    handle, seg_name, view_bin=view_bin, timepoint=tp
                )
            except (KeyError, ValueError, OSError):
                labels_array = None

        # ── Resolve reference-circle center on the universal semicircle ─
        ref_center: tuple[float, float] | None = None
        if ref_circle_tau_ns is not None:
            meta = self._read_fresh_metadata(handle)
            freq_mhz = meta.get("flim_frequency_mhz")
            if freq_mhz is None:
                raise ValueError(
                    "Reference-circle filter requires flim_frequency_mhz in dataset metadata"
                )
            ref_center = universal_circle_gs(harmonic, ref_circle_tau_ns, float(freq_mhz))

        # ── Compose the pixel-validity mask ──────────────────────
        labels_flat = labels_array.ravel() if labels_array is not None else None
        mask_flat = mask_array.ravel() if mask_array is not None else None

        valid = compute_valid_phasor_pixels(
            g.ravel(), s.ravel(),
            labels_flat=labels_flat,
            filter_ids=self._session.filter_ids,
            mask_flat=mask_flat,
            intensity_flat=intensity.ravel(),
            intensity_threshold=intensity_threshold,
            ref_circle_center=ref_center,
            ref_circle_radius=ref_circle_radius,
        )

        g_valid = g.ravel()[valid].astype(np.float64)
        s_valid = s.ravel()[valid].astype(np.float64)
        intensity_valid = intensity.ravel()[valid].astype(np.float64)

        # ── Fit ──────────────────────────────────────────────────
        # n=1 is a closed-form intensity-weighted mean/covariance — no EM,
        # no subsampling, no criterion. Anything else goes through the
        # multi-component GMM path (fixed n if n_components is set,
        # BIC/AIC sweep when n_components is None).
        if n_components == 1:
            fit = single_component_fit_phasor(g_valid, s_valid, intensity_valid)
        else:
            fit = gmm_fit_phasor(
                g_valid, s_valid, intensity_valid,
                n_components=n_components,
                criterion=criterion,
                n_min=2,
                n_max=n_max,
            )

        # ── Map components → PhasorROIGeometry ───────────────────
        geometries: list[PhasorROIGeometry] = []
        for i in range(fit.chosen_n):
            mean_g = float(fit.means[i, 0])
            mean_s = float(fit.means[i, 1])
            lambda_major, lambda_minor, angle_rad = gmm_eigenstructure(
                fit.covariances[i]
            )
            # FlimPanel's scalar cov_f / shift become per-axis defaults:
            # both stretch axes share cov_f; shift_parallel = shift,
            # shift_perpendicular = 0. Per-ROI fine-tuning of the four
            # axis coefficients lives on the Selected-ROI panel.
            center, radii, angle_deg = gmm_to_phasor_roi_geometry(
                mean=(mean_g, mean_s),
                lambda_major=lambda_major,
                lambda_minor=lambda_minor,
                principal_angle_rad=angle_rad,
                stretch_parallel=cov_f,
                stretch_perpendicular=cov_f,
                shift_parallel=shift,
                shift_perpendicular=0.0,
                shape=shape,
            )
            geometries.append(PhasorROIGeometry(
                center=center, radii=radii, angle_deg=angle_deg,
                mean_g=mean_g, mean_s=mean_s,
                lambda_major=lambda_major, lambda_minor=lambda_minor,
                principal_angle_rad=angle_rad,
                label=i + 1,
            ))

        return RunPhasorGMMResult(
            geometries=geometries,
            chosen_n=fit.chosen_n,
            criterion=criterion,
            criterion_value=fit.criterion_value,
            sampled_pixels=fit.sampled_pixels,
            dataset_path=dataset_path,
        )
