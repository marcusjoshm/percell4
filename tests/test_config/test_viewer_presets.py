"""Tests for the centralized napari display presets module.

Two concerns covered:
- Import purity: ``percell4.config.viewer_presets`` must not pull in
  napari/Qt at module top. Mirrors the pattern in
  ``tests/test_workflows/test_qt_free_imports.py`` (checks ``mod.__dict__``
  rather than ``sys.modules`` because pytest-qt and sibling tests may have
  already populated ``sys.modules['napari']``).
- ``_colormap_for_channel`` round-trip: the helper moved from
  ``viewer.py`` into the presets module; behavioral equivalence verified
  against current source (viewer.py:154-165 at time of move).
"""

from __future__ import annotations

import sys

_FORBIDDEN_SYMBOLS = (
    "qtpy", "PyQt5", "PyQt6", "PySide2", "PySide6", "napari", "DirectLabelColormap",
)


def test_viewer_presets_imports_without_napari_or_qt():
    """The presets module must stay napari- and Qt-free at module top."""
    # Drop any prior cached version so we observe a fresh import.
    sys.modules.pop("percell4.config.viewer_presets", None)
    sys.modules.pop("percell4.config", None)

    import percell4.config.viewer_presets as vp  # noqa: F401

    offenders = [bad for bad in _FORBIDDEN_SYMBOLS if bad in vp.__dict__]
    assert not offenders, (
        f"percell4.config.viewer_presets has forbidden symbols: {offenders}. "
        f"Keep the module data-only — call sites construct DirectLabelColormap "
        f"via lazy import."
    )


def test_colormap_for_channel_recognized_name():
    """A channel name containing a known pattern returns the mapped colormap."""
    from percell4.config.viewer_presets import _colormap_for_channel

    cmap, idx = _colormap_for_channel("DAPI", 0)
    assert cmap == "blue"
    assert idx == 0  # index unchanged when a pattern matches


def test_colormap_for_channel_substring_match():
    """Substring match on the normalized channel name (whitespace, dashes,
    underscores stripped, lower-cased)."""
    from percell4.config.viewer_presets import _colormap_for_channel

    cmap, idx = _colormap_for_channel("DAPI_channel_1", 3)
    assert cmap == "blue"
    assert idx == 3  # index unchanged on hit


def test_colormap_for_channel_unknown_falls_back_and_increments():
    """Unrecognized name falls back to the first cycle entry; index advances."""
    from percell4.config.viewer_presets import (
        CHANNEL_FALLBACK_CYCLE,
        _colormap_for_channel,
    )

    cmap, idx = _colormap_for_channel("unknown_chan", 0)
    assert cmap == CHANNEL_FALLBACK_CYCLE[0]
    assert idx == 1


def test_colormap_for_channel_cycle_wraps_modulo():
    """Index past cycle length wraps modulo cycle length."""
    from percell4.config.viewer_presets import (
        CHANNEL_FALLBACK_CYCLE,
        _colormap_for_channel,
    )

    cmap, idx = _colormap_for_channel("unknown_chan", len(CHANNEL_FALLBACK_CYCLE))
    assert cmap == CHANNEL_FALLBACK_CYCLE[0]
    assert idx == len(CHANNEL_FALLBACK_CYCLE) + 1
