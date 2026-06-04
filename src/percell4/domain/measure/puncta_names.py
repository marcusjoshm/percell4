"""Canonical, dependency-light name tuples for the puncta detection registries.

This module is intentionally free of scikit-image / scipy imports so that
:mod:`percell4.workflows.models` can validate a ``PunctaDetectorSettings`` at
config-construction time without dragging heavyweight image libraries into
config load. The registry modules
(:mod:`percell4.domain.measure.puncta_detectors`,
:mod:`percell4.domain.measure.bg_estimators`) re-export these tuples and assert
at import time that their registry keys match exactly, so the names here are the
single source of truth.

Both axes are described in
``docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md``.
"""

from __future__ import annotations

# Detector axis. Pass-2 detectors and the pass-1 seed detector draw from the
# same set. ``otsu`` is the legacy/baseline; ``atrous-wavelet`` ships as a stub
# pending evidence it is needed (plan U9).
DETECTOR_NAMES: tuple[str, ...] = (
    "otsu",
    "bg-k-sigma",
    "white-tophat",
    "log",
    "dog",
    "h-maxima",
    "otsu-floored",
    "adaptive",
    "local-otsu",
    "refine-otsu",
    "refine-cell-otsu",
    "atrous-wavelet",
)

# Background-estimator axis. ``donut-surface`` ships as a stub (plan U2);
# the fallback ladder drops to ``gaussian-peak`` while it is unimplemented.
BG_ESTIMATOR_NAMES: tuple[str, ...] = (
    "donut-median",
    "donut-mean",
    "gaussian-peak",
    "percentile",
    "mad",
    "rolling-ball",
    "donut-surface",
)
