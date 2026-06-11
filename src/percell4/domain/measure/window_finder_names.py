"""Dependency-light window-finder name tuple (the single validation source).

Mirrors :mod:`percell4.domain.measure.puncta_names` /
:mod:`percell4.domain.measure.iterative_otsu_names`: imports no scikit-image or
scipy so any config/settings validation that references the auto-window method
stays light at import time. The registry in
:mod:`percell4.domain.measure.window_finders` asserts its keys equal
``WINDOW_FINDER_NAMES`` at import (drift guard).

The tuple lists only the finders that are actually implemented in the current
phase — Phase 2 candidates (``log-scale`` / ``granulometry`` / ``autocorr`` /
``sweep-knee`` / ``fixed-point``) are appended together with their registry
entries when they land, so the drift guard never needs stubs.
"""

from __future__ import annotations

__all__ = ["WINDOW_FINDER_NAMES"]

WINDOW_FINDER_NAMES: tuple[str, ...] = (
    "otsu-mean",
)
