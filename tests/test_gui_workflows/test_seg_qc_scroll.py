"""U1: QC dock wrapped in a vertical-only QScrollArea.

Regression-guards against accidentally clipping any of the four
groups on smaller windows. The dock width remains fixed (horizontal
scroll is disabled).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QGroupBox, QScrollArea

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.gui.workflows.single_cell.seg_qc import SegmentationQCController
from percell4.model import CellDataModel
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    EdgeMode,
    WorkflowDatasetEntry,
)

# Builds a real napari viewer, so this module carries the ``napari_viewer``
# marker: skipped by default (see pyproject addopts), run explicitly on CI.
pytestmark = pytest.mark.napari_viewer


def _make_dataset(path: Path) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.ones((16, 16), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    labels = np.zeros((16, 16), dtype=np.int32)
    labels[4:8, 4:8] = 1
    store.write_labels("cellpose_qc", labels)


@pytest.fixture
def viewer_win():
    win = ViewerWindow(CellDataModel(session=Session()))
    yield win
    try:
        win.viewer.close()
    except Exception:
        pass


@pytest.fixture
def controller(tmp_path, viewer_win):
    path = tmp_path / "ds.h5"
    _make_dataset(path)
    entry = WorkflowDatasetEntry(
        name="ds",
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["GFP"],
    )
    ctrl = SegmentationQCController(
        viewer_win=viewer_win,
        entry=entry,
        queue_index=0,
        queue_total=1,
        on_complete=lambda r: None,
        channel_idx=0,
        seg_name="cellpose_qc",
        cellpose_settings=CellposeSettings(diameter=30, gpu=False),
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        edge_margin_px=0,
    )
    ctrl.start()
    yield ctrl
    ctrl._finish(PhaseResult(success=True, message="test teardown"))


def test_qc_window_central_widget_is_scroll_area(controller):
    central = controller._window.centralWidget()
    assert isinstance(central, QScrollArea)


def test_scroll_area_is_widget_resizable(controller):
    central = controller._window.centralWidget()
    assert central.widgetResizable() is True


def test_horizontal_scrollbar_disabled(controller):
    central = controller._window.centralWidget()
    assert central.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


def test_all_four_groups_reachable_inside_scroll_area(controller):
    """Regression: the wrap didn't drop any of the existing groups."""
    central = controller._window.centralWidget()
    inner = central.widget()
    titles = {
        child.title()
        for child in inner.findChildren(QGroupBox)
    }
    assert "Label Tools" in titles
    assert "Cleanup" in titles
    assert "Re-run Cellpose" in titles
    assert "Modify Channel" in titles
