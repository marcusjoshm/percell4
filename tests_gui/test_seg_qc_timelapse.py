"""Time-lapse segmentation QC: stack load + frame-scoped edits (U8)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.gui.workflows.single_cell.seg_qc import SegmentationQCController
from percell4.model import CellDataModel
from percell4.store import DatasetStore
from percell4.workflows.models import DatasetSource, WorkflowDatasetEntry


def _timelapse_dataset(path: Path, n_t=2):
    s = DatasetStore(path)
    s.create(metadata={"channel_names": ["GFP"]})
    # (T, C, H, W) multichannel time-lapse — read_channel would raise on 4D.
    s.write_array("intensity", np.ones((n_t, 1, 16, 16), dtype=np.float32),
                  attrs={"dims": ["T", "C", "H", "W"]})
    labels = np.zeros((n_t, 16, 16), dtype=np.int32)
    labels[:, 4:8, 4:8] = 1  # cell 1 present in every frame
    s.write_labels("cellpose_qc", labels)
    return s


@pytest.fixture
def controller(qtbot, tmp_path):
    path = tmp_path / "movie.h5"
    _timelapse_dataset(path, n_t=2)
    viewer_win = ViewerWindow(CellDataModel(session=Session()))
    entry = WorkflowDatasetEntry(name="movie", source=DatasetSource.H5_EXISTING,
                                 h5_path=path, channel_names=["GFP"])
    ctrl = SegmentationQCController(
        viewer_win=viewer_win, entry=entry, queue_index=0, queue_total=1,
        on_complete=lambda result: None, channel_idx=0, seg_name="cellpose_qc",
    )
    ctrl.start()
    yield ctrl, viewer_win, path
    try:
        viewer_win.viewer.close()
    except Exception:
        pass


def test_loads_4d_timelapse_as_stack(controller):
    ctrl, viewer_win, _path = controller
    # The seg channel loaded as a (T, H, W) stack (no 4D read_channel crash).
    assert ctrl._intensity.shape == (2, 16, 16)
    assert ctrl._labels.shape == (2, 16, 16)
    # napari shows a time slider.
    assert viewer_win.viewer.dims.ndim == 3


def test_delete_scoped_to_displayed_timepoint(controller):
    ctrl, viewer_win, _path = controller
    layer = ctrl._labels_layer()
    viewer_win.viewer.dims.set_current_step(0, 1)  # display frame 1
    layer.selected_label = 1

    ctrl._on_delete_selected()

    data = ctrl._labels_layer().data
    assert not np.any(data[1] == 1)  # cleared in displayed frame
    assert np.any(data[0] == 1)      # untouched in frame 0


def test_accept_persists_full_stack(controller):
    ctrl, viewer_win, path = controller
    layer = ctrl._labels_layer()
    viewer_win.viewer.dims.set_current_step(0, 0)
    layer.selected_label = 1
    ctrl._on_delete_selected()  # remove cell from frame 0 only

    ctrl._on_accept_clicked()

    # The persisted (T,H,W) stack reflects the frame-scoped edit.
    persisted = DatasetStore(path).read_labels("cellpose_qc")
    assert persisted.shape == (2, 16, 16)
    assert not np.any(persisted[0] == 1)
    assert np.any(persisted[1] == 1)
