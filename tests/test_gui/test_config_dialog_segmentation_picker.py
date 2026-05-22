"""Segmentation picker in the workflow config dialog (U12 wiring)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.store import DatasetStore
from percell4.workflows.models import DatasetSource


def _h5(path: Path, labels: list[str]):
    s = DatasetStore(path)
    s.create(metadata={"channel_names": ["GFP"]})
    s.write_array("intensity", np.zeros((8, 8), dtype=np.float32),
                  attrs={"dims": ["H", "W"]})
    cell = np.zeros((8, 8), dtype=np.int32)
    cell[2:5, 2:5] = 1
    for name in labels:
        s.write_labels(name, cell)


@pytest.fixture
def dialog_with_datasets(qtbot, tmp_path):
    from percell4.gui.workflows.single_cell.config_dialog import (
        WorkflowConfigDialog,
        _PendingDataset,
    )

    # DS1: three segmentations (raw + tracked + double-tracked).
    p1 = tmp_path / "DS1.h5"
    _h5(p1, ["cellpose", "cellpose_tracked", "cellpose_tracked_tracked"])
    # DS2: no segmentation yet.
    p2 = tmp_path / "DS2.h5"
    _h5(p2, [])

    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)
    dlg._pending_datasets = [
        _PendingDataset(display_name="DS1", source=DatasetSource.H5_EXISTING,
                        h5_path=p1, channel_names=["GFP"]),
        _PendingDataset(display_name="DS2", source=DatasetSource.H5_EXISTING,
                        h5_path=p2, channel_names=["GFP"]),
    ]
    dlg._refresh_dataset_tree()  # rebuilds the segmentation picker too
    return dlg


def test_picker_lists_segmentations_with_tracked_default(dialog_with_datasets):
    dlg = dialog_with_datasets
    combos = {pd.display_name: combo for pd, combo in dlg._seg_combos}

    # DS1 lists all three; defaults to the (first) tracked layer.
    ds1 = combos["DS1"]
    items = [ds1.itemText(i) for i in range(ds1.count())]
    assert set(items) == {"cellpose", "cellpose_tracked", "cellpose_tracked_tracked"}
    assert ds1.currentText() == "cellpose_tracked"
    assert ds1.isEnabled()


def test_picker_blocks_datasets_without_segmentation(dialog_with_datasets):
    dlg = dialog_with_datasets
    combos = {pd.display_name: combo for pd, combo in dlg._seg_combos}
    ds2 = combos["DS2"]
    assert not ds2.isEnabled()
    assert "None" in ds2.currentText()


def test_overrides_reflect_selection_and_omit_unsegmented(dialog_with_datasets):
    dlg = dialog_with_datasets
    # Default overrides: DS1 -> tracked; DS2 omitted (Cellpose will run).
    assert dlg.segmentation_overrides == {"DS1": "cellpose_tracked"}

    # User picks the double-tracked layer for DS1.
    combos = {pd.display_name: combo for pd, combo in dlg._seg_combos}
    combos["DS1"].setCurrentText("cellpose_tracked_tracked")
    assert dlg.segmentation_overrides == {"DS1": "cellpose_tracked_tracked"}
