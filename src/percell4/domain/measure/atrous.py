"""Hand-rolled à-trous (undecimated B3-spline) wavelet spot detector (STUB).

Phase 1 ships this as a registry stub raising :class:`NotImplementedError`, so
the :data:`percell4.domain.measure.puncta_detectors.DETECTORS` registry and the
``DETECTOR_NAMES`` tuple stay complete and the rest of the pipeline (U4) can
proceed on the library-backed detectors. The full implementation is
evidence-gated to Phase 3 of the plan (U9): it lands only if the library
detectors (LoG/DoG + white-top-hat) fall short of the recall bar in the U5
validation harness.

Intended Phase-3 implementation (Olivo-Marin 2002, *Extraction of spots in
biological images using multiscale products*):

* **B3-spline à-trous transform.** Separable convolution of the (NaN-filled)
  residual with the 1-D kernel ``[1, 4, 6, 4, 1] / 16`` along each axis. At
  level ``i`` the kernel is dilated by inserting ``2**i - 1`` zero "holes"
  between taps (the *à trous* / "with holes" step), giving an undecimated
  transform whose planes all share the input shape.
* **Wavelet planes.** Plane ``i`` is the detail ``level_i - level_{i+1}`` (the
  difference of successive smoothings). Small, compact spots concentrate their
  energy in the fine planes.
* **Multiscale product.** The pointwise product of planes 2 and 3 (``K=3``
  default) suppresses correlated background structure and isolated noise while
  reinforcing pixels that are bright across both scales — the spot signature.
* **Per-plane thresholding.** Each plane is thresholded at ``k_d * sigma_plane``
  where ``sigma_plane`` is a robust per-plane MAD estimate (``k_d ~ 3``);
  surviving pixels of the multiscale product form the binary spot mask.

Like every detector, the full version will own its convolution and must route
NaN through normalized handling / a fill mask
(:func:`percell4.domain.image.gaussian.nan_safe_gaussian_filter`) before
convolving, operate on the caller's per-group-isolated residual, and restrict
its output to ``group_mask & np.isfinite(residual)`` as ``{0, 1}`` ``uint8`` —
the same ``detector(residual, group_mask, sigma, params)`` contract as every
other :data:`DETECTORS` entry.

Pure: ``numpy`` / ``scipy`` only — no Qt, napari, h5py, or store.
"""

from __future__ import annotations

import numpy as np

__all__ = ["atrous_wavelet"]


def atrous_wavelet(
    residual: np.ndarray,
    group_mask: np.ndarray,
    sigma: float | None,
    params: dict,
) -> np.ndarray:
    """à-trous wavelet spot detector — NOT YET IMPLEMENTED (plan U9, Phase 3).

    Registered under the ``"atrous-wavelet"`` key purely so the registry and
    ``DETECTOR_NAMES`` stay complete; the full hand-rolled B3-spline
    implementation is evidence-gated (see the module docstring). Calling it
    raises so no code silently selects an empty detector.
    """
    raise NotImplementedError(
        "a-trous wavelet detector is not yet implemented; "
        "see plan U9 (evidence-gated to Phase 3)"
    )
