"""Canonical, dependency-light name tuples for iterative-Otsu peeling.

Intentionally free of scikit-image / scipy imports so that
:mod:`percell4.workflows.models` can validate an ``IterativeOtsuSettings`` at
config-construction time without dragging heavyweight image libraries into
config load. :mod:`percell4.domain.measure.iterative_otsu` re-exports
:data:`STOP_CRITERION_NAMES` and asserts at import time that its
``STOP_CRITERIA`` registry keys match exactly, so the names here are the single
source of truth.

See ``docs/plans/2026-06-08-002-feat-iterative-otsu-thresholding-plan.md``.
"""

from __future__ import annotations

# The iteration unit. Selects what one Otsu pass thresholds:
#   - ``groups``      reuse the existing GMM/k-means intensity grouping
#   - ``per-cell``    one unit per segmentation label
#   - ``whole-field`` a single all-cells unit
SCOPE_NAMES: tuple[str, ...] = ("groups", "per-cell", "whole-field")

# Stopping-criterion axis. Each name maps to a pure predicate in
# ``iterative_otsu.STOP_CRITERIA`` over a per-unit ``RoundContext``. A run names
# the active criteria and combines them by ``any``/``all``; a hard ``max_rounds``
# cap and the degenerate-residual guard always apply on top.
STOP_CRITERION_NAMES: tuple[str, ...] = (
    "bg-floor",
    "separability",
    "positive-fraction-high",
    "min-positive",
    "diminishing-returns",
    "peak-prominence",
    "min-area-components",
)
