"""Tests for :class:`WorkflowConfigDialog`.

These drive the dialog programmatically (setting widgets, reading
internals) rather than through the file dialogs, which are impractical
to test without GUI automation. The internal helpers ``_add_h5_paths``
and ``_add_pending`` are exercised directly to simulate user adds.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from qtpy.QtWidgets import QMessageBox

from percell4.gui.workflows.single_cell.config_dialog import (
    WorkflowConfigDialog,
    _PendingDataset,
)
from percell4.store import DatasetStore
from percell4.workflows.models import (
    DatasetSource,
    ThresholdAlgorithm,
)

# ── Fixtures ────────────────────────────────────────────────────────────


def _make_h5(tmp_path: Path, name: str, channels: list[str]) -> Path:
    """Create a minimal h5 file with the given channel names in metadata."""
    path = tmp_path / f"{name}.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": channels})
    # Also write a tiny intensity dataset so the file looks real.
    arr = np.ones((len(channels), 16, 16), dtype=np.float32)
    store.write_array("intensity", arr, attrs={"dims": ["C", "H", "W"]})
    return path


@pytest.fixture
def dialog(qtbot):
    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)
    return dlg


@pytest.fixture
def h5_ds1(tmp_path) -> Path:
    return _make_h5(tmp_path, "DS1", ["GFP", "RFP", "DAPI"])


@pytest.fixture
def h5_ds2(tmp_path) -> Path:
    return _make_h5(tmp_path, "DS2", ["GFP", "RFP"])


@pytest.fixture
def h5_ds3_outlier(tmp_path) -> Path:
    return _make_h5(tmp_path, "DS3", ["Cy5", "Hoechst"])


# ── Dialog construction ─────────────────────────────────────────────────


def test_dialog_initial_state(dialog):
    assert dialog.windowTitle() == "Single-cell thresholding analysis workflow"
    assert dialog._pending_datasets == []
    assert dialog._rounds_table.rowCount() == 0
    # Start disabled with empty state
    assert dialog._start_btn.isEnabled() is False
    assert dialog.workflow_config is None


def test_dialog_modal(dialog):
    assert dialog.isModal()


# ── Dataset picker ──────────────────────────────────────────────────────


def test_add_h5_file_populates_channel_names(dialog, h5_ds1):
    added, skipped = dialog._add_h5_paths([h5_ds1])
    assert added == 1
    assert skipped == []
    assert len(dialog._pending_datasets) == 1
    pd = dialog._pending_datasets[0]
    assert pd.source is DatasetSource.H5_EXISTING
    assert pd.channel_names == ["GFP", "RFP", "DAPI"]
    assert pd.h5_path == h5_ds1.resolve()


def test_add_h5_file_twice_dedupes(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    added, skipped = dialog._add_h5_paths([h5_ds1])
    assert added == 0
    assert len(skipped) == 1
    assert "duplicate" in skipped[0]
    assert len(dialog._pending_datasets) == 1


def test_add_two_different_h5_files(dialog, h5_ds1, h5_ds2):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    assert len(dialog._pending_datasets) == 2


def test_add_h5_disambiguates_display_names(dialog, tmp_path):
    # Two files with the same stem under different directories.
    sub1 = tmp_path / "a"
    sub1.mkdir()
    sub2 = tmp_path / "b"
    sub2.mkdir()
    p1 = _make_h5(sub1, "sample", ["GFP"])
    p2 = _make_h5(sub2, "sample", ["GFP"])

    dialog._add_h5_paths([p1, p2])
    names = [pd.display_name for pd in dialog._pending_datasets]
    assert names[0] == "sample"
    assert names[1] == "sample (2)"


def test_add_nonexistent_h5_is_skipped(dialog, tmp_path):
    bogus = tmp_path / "does_not_exist.h5"
    added, skipped = dialog._add_h5_paths([bogus])
    assert added == 0
    assert skipped
    assert "not a file" in skipped[0]


def test_add_h5_with_no_channel_names_metadata(dialog, tmp_path):
    """A real .h5 without channel_names metadata is still accepted, but
    lands with an empty channel list — so it will be flagged by the
    intersection check later."""
    path = tmp_path / "no_channels.h5"
    store = DatasetStore(path)
    store.create(metadata={})
    added, _ = dialog._add_h5_paths([path])
    assert added == 1
    assert dialog._pending_datasets[0].channel_names == []


def test_remove_selected_dataset(dialog, h5_ds1, h5_ds2):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    # Select the first tree row
    dialog._dataset_tree.setCurrentItem(dialog._dataset_tree.topLevelItem(0))
    dialog._on_remove_dataset()
    assert len(dialog._pending_datasets) == 1
    assert dialog._pending_datasets[0].display_name == "DS2"


# ── Cellpose group ──────────────────────────────────────────────────────


def test_cellpose_defaults(dialog):
    assert dialog._cp_model.currentText() == "cpsam"
    assert dialog._cp_diameter.value() == 30.0
    assert dialog._cp_gpu.isChecked() is True
    assert dialog._cp_min_size.value() == 15


# ── Rounds table ────────────────────────────────────────────────────────


def test_add_round_populates_row(dialog, h5_ds1, h5_ds2):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    assert dialog._rounds_table.rowCount() == 1
    data = dialog._read_round_row(0)
    assert data["name"] == "round_1"
    assert data["channel"] in ("GFP", "RFP")  # from intersection
    assert data["metric"] == "mean_intensity"
    assert data["algorithm"] == "gmm"


def test_add_round_with_no_datasets_shows_placeholder(dialog):
    dialog._on_add_round()
    ch_combo = dialog._rounds_table.cellWidget(0, 1)
    assert ch_combo.isEnabled() is False
    assert ch_combo.currentText() == "(add datasets first)"


def test_remove_round(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    assert dialog._rounds_table.rowCount() == 2
    dialog._rounds_table.setCurrentCell(0, 0)
    dialog._on_remove_round()
    assert dialog._rounds_table.rowCount() == 1


def test_round_name_invalid_regex_colors_red(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    name_item = dialog._rounds_table.item(0, 0)
    # Trigger the regex validator via the itemChanged signal
    name_item.setText("has space")
    # The background color should be non-default (red-ish) — we just
    # verify the tooltip got set, which happens in the same handler.
    assert "must match" in name_item.toolTip()


def test_round_name_valid_regex_clears_tooltip(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    name_item = dialog._rounds_table.item(0, 0)
    name_item.setText("has space")
    assert name_item.toolTip() != ""
    name_item.setText("ok_name")
    assert name_item.toolTip() == ""


def test_algo_toggles_enabled_spinboxes(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    algo_combo = dialog._rounds_table.cellWidget(0, 3)
    gmm_spin = dialog._rounds_table.cellWidget(0, 4)
    kmeans_spin = dialog._rounds_table.cellWidget(0, 5)

    # Default: GMM → gmm_max enabled, kmeans_k disabled
    assert gmm_spin.isEnabled() is True
    assert kmeans_spin.isEnabled() is False

    algo_combo.setCurrentText(ThresholdAlgorithm.KMEANS.value)
    assert gmm_spin.isEnabled() is False
    assert kmeans_spin.isEnabled() is True


# ── Column picker ───────────────────────────────────────────────────────


def test_seg_channel_combo_populates_from_intersection(dialog, h5_ds1, h5_ds2):
    """Seg channel combo should show GFP and RFP (the intersection)."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    combo = dialog._cp_seg_channel
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "GFP" in items
    assert "RFP" in items
    # DAPI is NOT in the intersection (only in DS1)
    assert "DAPI" not in items
    assert combo.isEnabled()


def test_seg_channel_combo_placeholder_without_datasets(dialog):
    combo = dialog._cp_seg_channel
    assert not combo.isEnabled()
    assert combo.currentText() == "(add datasets first)"


def test_csv_export_selection_persists(dialog, h5_ds1, h5_ds2):
    """Setting channel + metric selections updates the summary label."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._selected_csv_channels = {"GFP"}
    dialog._selected_csv_metrics = {"mean_intensity", "area"}
    dialog._update_csv_summary()
    text = dialog._csv_summary_label.text()
    assert "1 channel" in text
    assert "2 metric" in text


def test_csv_export_prunes_invalid_channels(dialog, h5_ds1, h5_ds2):
    """If datasets change and a channel drops out of the intersection,
    the selection set is pruned."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._selected_csv_channels = {"GFP", "DAPI"}  # DAPI not in intersection
    dialog._refresh_column_picker()
    # DAPI should be pruned because it's not in the intersection
    assert "DAPI" not in dialog._selected_csv_channels
    assert "GFP" in dialog._selected_csv_channels


def test_build_selected_csv_columns_cross_product(dialog, h5_ds1, h5_ds2):
    """The cross-product builder should produce the expected column names."""
    from percell4.workflows.models import ThresholdAlgorithm, ThresholdingRound

    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._selected_csv_channels = {"GFP"}
    dialog._selected_csv_metrics = {"mean_intensity"}

    rounds = [
        ThresholdingRound(
            name="R1",
            channel="GFP",
            metric="mean_intensity",
            algorithm=ThresholdAlgorithm.GMM,
        )
    ]
    cols = dialog._build_selected_csv_columns(["GFP", "RFP"], rounds)

    # Whole-cell: GFP_mean_intensity (only GFP selected, not RFP)
    assert "GFP_mean_intensity" in cols
    assert "RFP_mean_intensity" not in cols
    # Group column
    assert "group_R1" in cols
    # Per-round in/out
    assert "GFP_mean_intensity_in_R1" in cols
    assert "GFP_mean_intensity_out_R1" in cols
    # RFP was NOT selected so its in/out should be absent
    assert "RFP_mean_intensity_in_R1" not in cols


# ── Start button / accept ───────────────────────────────────────────────


def test_start_disabled_without_datasets(dialog):
    dialog._on_add_round()  # round added but no dataset
    # After add_round, _update_start_enabled runs
    assert dialog._start_btn.isEnabled() is False


def test_start_disabled_without_rounds(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    assert dialog._start_btn.isEnabled() is False


def test_start_enabled_with_datasets_and_rounds(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    assert dialog._start_btn.isEnabled() is True


def test_accept_without_output_folder_warns(dialog, h5_ds1, tmp_path):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._output_edit.setText("")
    with patch.object(QMessageBox, "warning") as warn_mock:
        dialog._on_start_clicked()
    warn_mock.assert_called_once()
    assert "output parent" in warn_mock.call_args[0][2].lower()


def test_accept_with_valid_config_builds_workflow_config(
    dialog, h5_ds1, h5_ds2, tmp_path
):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))

    dialog._on_start_clicked()

    cfg = dialog.workflow_config
    assert cfg is not None
    assert len(cfg.datasets) == 2
    assert len(cfg.thresholding_rounds) == 1
    assert cfg.output_parent == tmp_path / "runs"
    assert cfg.cellpose.model == "cpsam"
    # Seg channel is auto-selected from the first channel in the intersection
    assert cfg.seg_channel_name in ("GFP", "RFP")


def test_accept_with_outlier_dataset_prompts_user(
    dialog, h5_ds1, h5_ds2, h5_ds3_outlier, tmp_path
):
    """DS3 has zero channel overlap with DS1/DS2 — intersection is empty,
    so validation shows a warning box and refuses to accept."""
    dialog._add_h5_paths([h5_ds1, h5_ds2, h5_ds3_outlier])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))

    with patch.object(QMessageBox, "exec_", return_value=QMessageBox.Cancel):
        dialog._on_start_clicked()

    assert dialog.workflow_config is None  # dialog did NOT accept


def test_accept_with_invalid_round_name_warns(dialog, h5_ds1, tmp_path):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    name_item = dialog._rounds_table.item(0, 0)
    name_item.setText("has space")
    dialog._output_edit.setText(str(tmp_path / "runs"))

    with patch.object(QMessageBox, "warning") as warn_mock:
        dialog._on_start_clicked()
    warn_mock.assert_called_once()
    assert dialog.workflow_config is None


def test_accept_saves_output_parent_to_qsettings(
    dialog, h5_ds1, tmp_path
):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    target = tmp_path / "saved_runs"
    dialog._output_edit.setText(str(target))
    dialog._on_start_clicked()

    from qtpy.QtCore import QSettings
    qs = QSettings("LeeLabPerCell4", "PerCell4")
    assert qs.value("single_cell_threshold_workflow/output_parent", "", type=str) == str(target)


# ── _PendingDataset helper ──────────────────────────────────────────────


def test_pending_dataset_dedupe_key_differs_for_h5_vs_tiff(tmp_path):
    p = tmp_path / "x.h5"
    p.write_bytes(b"")
    h5 = _PendingDataset(
        display_name="x",
        source=DatasetSource.H5_EXISTING,
        h5_path=p,
        channel_names=[],
    )
    tiff = _PendingDataset(
        display_name="x",
        source=DatasetSource.TIFF_PENDING,
        h5_path=p,
        channel_names=[],
        compress_plan={"source_dir": "/tmp", "files": []},
    )
    assert h5.dedupe_key() != tiff.dedupe_key()


def test_pending_dataset_to_entry_round_trips_channels(tmp_path):
    p = tmp_path / "x.h5"
    p.write_bytes(b"")
    pd = _PendingDataset(
        display_name="x",
        source=DatasetSource.H5_EXISTING,
        h5_path=p,
        channel_names=["GFP", "RFP"],
    )
    entry = pd.to_entry()
    assert entry.name == "x"
    assert entry.channel_names == ["GFP", "RFP"]
    assert entry.source is DatasetSource.H5_EXISTING
