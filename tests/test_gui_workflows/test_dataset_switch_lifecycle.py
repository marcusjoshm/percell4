"""Anchor regression tests for AE1 (auto-select on load) and AE2
(clean slate on dataset switch with overlapping mask names).

These cover the user-observed bugs from
docs/brainstorms/2026-05-01-dataset-lifecycle-and-resource-list-events-requirements.md
specifically C1 and C2.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


def _phasor_window(session, mask_array=None):
    repo = MagicMock()
    if mask_array is not None:
        repo.read_mask = MagicMock(return_value=mask_array)
    # A mock dataset has no cached phasor on disk: reading /phasor/<ch>/g must
    # raise KeyError so the auto-load-cached path resolves to NoCachedPhasorError
    # (a bare MagicMock would otherwise feed mock "arrays" into the histogram).
    repo.read_array = MagicMock(side_effect=KeyError("no cached phasor"))
    win = PhasorPlotWindow(session, get_repo=lambda: repo)
    return win, repo


def test_AE1_auto_selects_all_three_slots_on_load(qtbot):
    """Covers AE1. Loading a dataset auto-selects channel, seg, mask."""
    session = Session()
    handle = DatasetHandle(
        path="/tmp/x.h5",
        metadata={
            "channel_names": ["mNG", "CA-SiR"],
            "segmentation_names": ["cellpose"],
            "mask_names": ["SG_mask"],
        },
    )
    session.set_dataset(handle)
    assert session.active_channel == "mNG"
    assert session.active_segmentation == "cellpose"
    assert session.active_mask == "SG_mask"


def test_AE2_dataset_switch_with_overlapping_mask_name_resets_phasor_caches(qtbot):
    """Covers AE2. Loading dataset 2 with the same mask name as dataset 1
    must clear the phasor's cached mask array so the checkbox can engage
    correctly against dataset 2's G/S maps.
    """
    session = Session()
    # Dataset 1: 64x64 mask
    mask1 = np.ones((64, 64), dtype=np.uint8)
    session.set_dataset(DatasetHandle(
        path="/tmp/d1.h5",
        metadata={"channel_names": ["mNG"], "mask_names": ["SG_mask"]},
    ))
    win, repo1 = _phasor_window(session, mask_array=mask1)
    qtbot.addWidget(win)

    # Simulate phasor data + cached mask
    win.set_phasor_data(
        g_map=np.ones((64, 64)) * 0.3,
        s_map=np.ones((64, 64)) * 0.4,
        intensity=np.ones((64, 64)),
    )
    win._mask_filter_check.setChecked(True)
    qtbot.wait(50)
    # Force the cache to populate by triggering the load path
    win._load_active_mask_flat()
    assert win._active_mask_flat is not None

    # Switch datasets — dataset 2 also has SG_mask but at 128x128
    session.set_dataset(DatasetHandle(
        path="/tmp/d2.h5",
        metadata={"channel_names": ["CA-SiR"], "mask_names": ["SG_mask"]},
    ))
    qtbot.wait(50)

    # Per-dataset caches must be cleared
    assert win._g_map is None, "G/S maps survived dataset switch"
    assert win._s_map is None
    assert win._active_mask_flat is None, "mask cache survived"
    assert win._active_mask_array is None
    assert win._roi_widgets == [], "ROIs survived dataset switch"
    # Status bar reflects the no-data state
    assert "No phasor" in win._status.currentMessage() or \
           "0" in win._status.currentMessage()


def test_AE2_phasor_checkbox_re_engages_after_dataset_switch(qtbot):
    """Covers AE2. After switching datasets, the mask-filter checkbox
    can be checked again on the first try (previously it was stuck
    unclickable because of stale cached_mask + overlapping name).
    """
    session = Session()
    mask1 = np.ones((64, 64), dtype=np.uint8)
    session.set_dataset(DatasetHandle(
        path="/tmp/d1.h5",
        metadata={"channel_names": ["mNG"], "mask_names": ["SG_mask"]},
    ))
    win, repo = _phasor_window(session, mask_array=mask1)
    qtbot.addWidget(win)
    win.set_phasor_data(
        g_map=np.ones((64, 64)) * 0.3,
        s_map=np.ones((64, 64)) * 0.4,
        intensity=np.ones((64, 64)),
    )
    win._mask_filter_check.setChecked(True)
    qtbot.wait(50)

    # Switch to dataset 2 (also has SG_mask)
    session.set_dataset(DatasetHandle(
        path="/tmp/d2.h5",
        metadata={"channel_names": ["CA-SiR"], "mask_names": ["SG_mask"]},
    ))
    qtbot.wait(50)

    # Recompute phasor for dataset 2 (different shape if you like)
    mask2 = np.ones((96, 96), dtype=np.uint8)
    repo.read_mask = MagicMock(return_value=mask2)
    win.set_phasor_data(
        g_map=np.ones((96, 96)) * 0.5,
        s_map=np.ones((96, 96)) * 0.5,
        intensity=np.ones((96, 96)),
    )
    qtbot.wait(50)

    # The checkbox should be enabled (active_mask is "SG_mask" again
    # after the auto-selection from set_dataset)
    assert session.active_mask == "SG_mask"
    assert win._mask_filter_check.isEnabled()
    # User can engage the filter
    win._mask_filter_check.setChecked(True)
    qtbot.wait(50)
    assert win._mask_filter_check.isChecked()
