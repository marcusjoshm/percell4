"""Tests for PhasorPlotWindow cache invalidation on session.active_bin toggle (U14).

A bin toggle stales every ndarray cache that the phasor plot keeps in
memory -- they were materialized at the previous bin's sampling. The
``_invalidate_for_bin_change`` chokepoint is the single anchor for the
enumerated cache list; this file exercises every enumerated member.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


class FakeRepo:
    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.disk_metadata: dict = {}

    def write_array(self, handle, path, data, attrs=None):
        self.arrays[path] = data

    def read_array(self, handle, path, view_bin=1):
        if path not in self.arrays:
            raise KeyError(f"Array not found: {path}")
        return self.arrays[path]

    def read_metadata(self, handle):
        return dict(self.disk_metadata)


@pytest.fixture
def session_with_dataset(tmp_path):
    s = Session()
    handle = DatasetHandle(
        path=tmp_path / "fake.h5",
        metadata={"flim_frequency_mhz": 80.0},
    )
    s._dataset = handle
    s._active_channel = "ch0"
    return s


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def window(qtbot, session_with_dataset, repo):
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    return win


def _hydrate_all_caches(win, shape=(8, 8)):
    """Plant a value in every enumerated cache so we can verify each
    gets cleared by _invalidate_for_bin_change."""
    flat_size = shape[0] * shape[1]
    win._g_map = np.zeros(shape, dtype=np.float32)
    win._g_map_unfiltered = np.zeros(shape, dtype=np.float32)
    win._s_map = np.zeros(shape, dtype=np.float32)
    win._s_map_unfiltered = np.zeros(shape, dtype=np.float32)
    win._intensity = np.zeros(shape, dtype=np.float32)
    win._labels = np.zeros(shape, dtype=np.int32)
    win._labels_flat = np.zeros(flat_size, dtype=np.int32)
    win._active_mask_array = np.zeros(shape, dtype=np.uint8)
    win._active_mask_flat = np.zeros(flat_size, dtype=np.uint8)
    win._cleared_mask = np.zeros(shape, dtype=bool)


def test_invalidate_clears_every_enumerated_cache(window):
    """The U14 cache list -- every one of these MUST go to None after
    a bin toggle. Add new caches here when expanding _invalidate_for_bin_change."""
    _hydrate_all_caches(window)
    # Verify hydration worked.
    assert window._g_map is not None
    assert window._active_mask_flat is not None

    window._invalidate_for_bin_change()

    assert window._g_map is None
    assert window._g_map_unfiltered is None
    assert window._s_map is None
    assert window._s_map_unfiltered is None
    assert window._intensity is None
    assert window._labels is None
    assert window._labels_flat is None
    assert window._active_mask_array is None
    assert window._active_mask_flat is None
    assert window._cleared_mask is None


def test_session_set_active_bin_triggers_invalidation(window, session_with_dataset):
    """The wired path: session.set_active_bin emits ACTIVE_BIN_CHANGED,
    which the phasor plot's subscription routes into
    _invalidate_for_bin_change."""
    _hydrate_all_caches(window)
    session_with_dataset.set_active_bin(3)

    assert window._g_map is None
    assert window._s_map is None
    assert window._intensity is None
    assert window._active_mask_array is None
    assert window._cleared_mask is None


def test_invalidate_clears_per_roi_cached_mask(window):
    """ROI widgets each carry a shape-dependent cached_mask. The
    invalidator must reach every one of them."""
    # Set up a couple of fake ROI widgets with cached_mask attrs.
    class _ROIStub:
        def __init__(self):
            self.cached_mask = np.zeros((8, 8), dtype=bool)

    window._roi_widgets = [_ROIStub(), _ROIStub()]

    window._invalidate_for_bin_change()

    for w in window._roi_widgets:
        assert w.cached_mask is None


def test_invalidate_safe_when_caches_already_none(window):
    """A no-op invalidation (all caches already None) must not raise.

    Guards against an early-init bin toggle, before any hydration ran.
    """
    # Already None by construction; just ensure the call is safe.
    window._invalidate_for_bin_change()  # must not raise
