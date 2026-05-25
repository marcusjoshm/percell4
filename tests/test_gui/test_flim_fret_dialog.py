"""Tests for the FLIM-FRET analysis setup dialog.

Use qtbot to construct the dialog and drive widget state programmatically.
The orchestrator is injected for controllable per-test reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import h5py
import numpy as np
import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QComboBox, QDialog, QMessageBox, QPushButton

from percell4.gui.flim_fret_dialog import (
    FlimFretDialog,
    _ConfigurePairDialog,
    _PairConfig,
)
from percell4.workflows.models import (
    FlimFretPair,
    FlimFretPairResult,
    FlimFretReport,
    FlimFretStatus,
)


# ── Fixture builders ──────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channel_names: list[str],
    mask_names: list[str] = (),
    label_names: list[str] = (),
    intensity_shape: tuple[int, ...] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if intensity_shape is None:
        intensity_shape = (len(channel_names), 4, 4)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channel_names
        f.create_dataset(
            "intensity", data=np.ones(intensity_shape, dtype=np.float32)
        )
        if mask_names:
            mg = f.create_group("masks")
            for n in mask_names:
                mg.create_dataset(n, data=np.ones((4, 4), dtype=np.uint8))
        if label_names:
            lg = f.create_group("labels")
            for n in label_names:
                lg.create_dataset(n, data=np.ones((4, 4), dtype=np.int32))
    return path


def _good_dataset(tmp_path: Path, name: str = "ds.h5") -> Path:
    return _make_h5(
        tmp_path / name,
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        label_names=["cellpose_qc"],
    )


# ── Dialog construction ───────────────────────────────────


def test_dialog_constructs_with_expected_widgets(qtbot):
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    assert dlg._single_cell_check is not None
    assert dlg._source_folder_edit is not None
    assert dlg._output_folder_edit is not None
    assert dlg._pair_table is not None
    assert dlg._start_btn is not None
    assert dlg._pair_table.rowCount() == 0


def test_dialog_uses_dialog_helpers():
    """wrap_in_scroll + cap_to_screen must appear in the source.

    The dialog-helper-compliance AST walker (see
    tests/test_gui/test_dialog_helper_compliance.py) enforces this for
    every gui/*_dialog.py; we duplicate the assertion here so a failure
    points directly at this dialog.
    """
    import inspect

    import percell4.gui.flim_fret_dialog as mod

    src = inspect.getsource(mod)
    assert "wrap_in_scroll" in src
    assert "cap_to_screen" in src


def test_start_disabled_with_no_pairs(qtbot):
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    assert dlg._start_btn.isEnabled() is False


# ── Add / remove pair ─────────────────────────────────────


def test_add_pair_appends_row(qtbot, tmp_path):
    _good_dataset(tmp_path, "a.h5")
    _good_dataset(tmp_path, "b.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)

    dlg._on_add_pair()
    assert dlg._pair_table.rowCount() == 1
    assert len(dlg._pair_configs) == 1
    donor_combo = dlg._pair_table.cellWidget(0, 1)
    da_combo = dlg._pair_table.cellWidget(0, 2)
    assert isinstance(donor_combo, QComboBox)
    assert isinstance(da_combo, QComboBox)
    assert donor_combo.count() == 2  # two eligible datasets


def test_remove_pair_drops_state(qtbot, tmp_path):
    _good_dataset(tmp_path, "a.h5")
    _good_dataset(tmp_path, "b.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)

    dlg._on_add_pair()
    dlg._on_add_pair()
    assert dlg._pair_table.rowCount() == 2
    dlg._on_remove_pair()  # removes last
    assert dlg._pair_table.rowCount() == 1
    assert len(dlg._pair_configs) == 1


# ── Source folder + discovery ─────────────────────────────


def test_source_folder_filters_dropdowns_by_eligibility(qtbot, tmp_path):
    _good_dataset(tmp_path, "good.h5")
    # A dataset missing _phasor → ineligible.
    _make_h5(
        tmp_path / "bad.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask"],
    )
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)

    dlg._on_add_pair()
    donor_combo = dlg._pair_table.cellWidget(0, 1)
    names = [donor_combo.itemText(i) for i in range(donor_combo.count())]
    assert "good.h5" in names
    assert "bad.h5" not in names


def test_discovery_status_label_shows_exclusions(qtbot, tmp_path):
    _good_dataset(tmp_path, "good.h5")
    _make_h5(
        tmp_path / "bad.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask"],  # no _phasor → excluded
    )
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)

    label = dlg._discovery_status_label.text()
    assert "1 eligible" in label
    assert "Excluded" in label
    assert "bad.h5" in label


def test_empty_source_folder_disables_pairs(qtbot, tmp_path):
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._on_add_pair()
    donor_combo = dlg._pair_table.cellWidget(0, 1)
    assert donor_combo.count() == 0


def test_single_cell_toggle_reruns_discovery(qtbot, tmp_path):
    # Dataset without /labels/ — eligible in whole-field, ineligible in single-cell.
    _make_h5(
        tmp_path / "no_labels.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)

    eligible_wf = dlg._eligible_paths()
    dlg._single_cell_check.setChecked(True)
    eligible_sc = dlg._eligible_paths()

    assert len(eligible_wf) == 1
    assert len(eligible_sc) == 0


# ── Configure sub-dialog ──────────────────────────────────


def test_configure_subdialog_lists_all_masks_in_both_dropdowns(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    initial = _PairConfig()

    parent = QPushButton()
    qtbot.addWidget(parent)
    sub = _ConfigurePairDialog(
        parent,
        donor_path=donor,
        da_path=da,
        single_cell=False,
        initial=initial,
    )
    qtbot.addWidget(sub)

    donor_mask_combo = sub._donor_widgets["mask"]
    donor_phasor_combo = sub._donor_widgets["phasor"]
    # Both dropdowns list ALL /masks/ entries (unfiltered).
    mask_items = [donor_mask_combo.itemText(i) for i in range(donor_mask_combo.count())]
    phasor_items = [donor_phasor_combo.itemText(i) for i in range(donor_phasor_combo.count())]
    assert "cells_mask" in mask_items
    assert "phasor_ch0_1_phasor" in mask_items
    assert "cells_mask" in phasor_items
    assert "phasor_ch0_1_phasor" in phasor_items


def test_configure_subdialog_defaults_to_suffix_matched_entries(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    parent = QPushButton()
    qtbot.addWidget(parent)
    sub = _ConfigurePairDialog(
        parent,
        donor_path=donor,
        da_path=da,
        single_cell=False,
        initial=_PairConfig(),
    )
    qtbot.addWidget(sub)

    assert sub._donor_widgets["mask"].currentText() == "cells_mask"
    assert sub._donor_widgets["phasor"].currentText() == "phasor_ch0_1_phasor"
    assert sub._donor_widgets["lifetime"].currentText() == "ch0_unfiltered_lifetime"


def test_configure_subdialog_shows_segmentation_only_in_single_cell(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")

    parent_wf = QPushButton()
    qtbot.addWidget(parent_wf)
    sub_wf = _ConfigurePairDialog(
        parent_wf,
        donor_path=donor,
        da_path=da,
        single_cell=False,
        initial=_PairConfig(),
    )
    qtbot.addWidget(sub_wf)
    assert sub_wf._donor_widgets["segmentation"] is None

    parent_sc = QPushButton()
    qtbot.addWidget(parent_sc)
    sub_sc = _ConfigurePairDialog(
        parent_sc,
        donor_path=donor,
        da_path=da,
        single_cell=True,
        initial=_PairConfig(),
    )
    qtbot.addWidget(sub_sc)
    assert isinstance(sub_sc._donor_widgets["segmentation"], QComboBox)
    assert sub_sc._donor_widgets["segmentation"].currentText() == "cellpose_qc"


def test_configure_apply_writes_picks_into_pair_config(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    cfg = _PairConfig()
    parent = QPushButton()
    qtbot.addWidget(parent)
    sub = _ConfigurePairDialog(
        parent,
        donor_path=donor,
        da_path=da,
        single_cell=False,
        initial=cfg,
    )
    qtbot.addWidget(sub)

    sub.apply_to(cfg)
    assert cfg.donor_mask == "cells_mask"
    assert cfg.donor_phasor == "phasor_ch0_1_phasor"
    assert cfg.donor_lifetime == "ch0_unfiltered_lifetime"


# ── Build config / Start gating ───────────────────────────


def _add_configured_pair(
    dlg: FlimFretDialog,
    *,
    donor_path: Path,
    da_path: Path,
    name: str = "pair_1",
    single_cell: bool = False,
) -> None:
    dlg._on_add_pair()
    row = dlg._pair_table.rowCount() - 1
    dlg._pair_table.item(row, 0).setText(name)
    donor_combo = dlg._pair_table.cellWidget(row, 1)
    da_combo = dlg._pair_table.cellWidget(row, 2)
    donor_idx = next(
        i
        for i in range(donor_combo.count())
        if donor_combo.itemData(i) == donor_path
    )
    da_idx = next(
        i
        for i in range(da_combo.count())
        if da_combo.itemData(i) == da_path
    )
    donor_combo.setCurrentIndex(donor_idx)
    da_combo.setCurrentIndex(da_idx)

    cfg = dlg._pair_configs[row]
    cfg.donor_mask = "cells_mask"
    cfg.donor_phasor = "phasor_ch0_1_phasor"
    cfg.donor_lifetime = "ch0_unfiltered_lifetime"
    cfg.da_mask = "cells_mask"
    cfg.da_phasor = "phasor_ch0_1_phasor"
    cfg.da_lifetime = "ch0_unfiltered_lifetime"
    if single_cell:
        cfg.donor_segmentation = "cellpose_qc"
        cfg.da_segmentation = "cellpose_qc"
    cfg.configured = True


def test_start_enabled_when_config_valid(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._output_folder_edit.setText(str(tmp_path / "out"))

    _add_configured_pair(dlg, donor_path=donor, da_path=da)
    dlg._update_run_button()
    assert dlg._start_btn.isEnabled()


def test_start_disabled_with_duplicate_pair_names(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._output_folder_edit.setText(str(tmp_path / "out"))

    _add_configured_pair(dlg, donor_path=donor, da_path=da, name="dupe")
    _add_configured_pair(dlg, donor_path=donor, da_path=da, name="dupe")
    dlg._update_run_button()
    assert dlg._start_btn.isEnabled() is False


def test_start_disabled_when_donor_equals_da(qtbot, tmp_path):
    same = _good_dataset(tmp_path, "same.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._output_folder_edit.setText(str(tmp_path / "out"))
    _add_configured_pair(dlg, donor_path=same, da_path=same)
    dlg._update_run_button()
    assert dlg._start_btn.isEnabled() is False


def test_start_disabled_when_configure_not_done(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._output_folder_edit.setText(str(tmp_path / "out"))

    dlg._on_add_pair()
    row = 0
    donor_combo = dlg._pair_table.cellWidget(row, 1)
    da_combo = dlg._pair_table.cellWidget(row, 2)
    donor_combo.setCurrentIndex(0)
    # Pick a different DA than donor
    for i in range(da_combo.count()):
        if da_combo.itemData(i) != donor_combo.itemData(0):
            da_combo.setCurrentIndex(i)
            break
    # cfg.configured stays False → Start disabled
    dlg._update_run_button()
    assert dlg._start_btn.isEnabled() is False


def test_start_disabled_when_single_cell_without_segmentations(qtbot, tmp_path):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._output_folder_edit.setText(str(tmp_path / "out"))

    dlg._single_cell_check.setChecked(True)
    _add_configured_pair(
        dlg, donor_path=donor, da_path=da, single_cell=False
    )
    # Configured for whole-field but single-cell is on → incomplete
    dlg._update_run_button()
    assert dlg._start_btn.isEnabled() is False


# ── Source-folder change clears pairs with confirmation ──


def test_source_folder_change_prompts_when_pairs_exist(qtbot, tmp_path, monkeypatch):
    _good_dataset(tmp_path, "ds.h5")
    dlg = FlimFretDialog()
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._on_add_pair()
    assert dlg._pair_table.rowCount() == 1

    new_folder = tmp_path / "other"
    new_folder.mkdir()
    _good_dataset(new_folder, "ds2.h5")

    # User confirms the clear.
    monkeypatch.setattr(
        QFileDialog := __import__(
            "qtpy.QtWidgets", fromlist=["QFileDialog"]
        ).QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(new_folder)),
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes)
    )

    dlg._on_browse_source()
    assert dlg._pair_table.rowCount() == 0


# ── Start click → orchestrator + CSV ──────────────────────


def test_start_writes_csv_and_calls_orchestrator(qtbot, tmp_path, monkeypatch):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")

    def fake_orchestrator(config, *, progress_callback, cancel_check, run_log):
        pair = config.pairs[0]
        result = FlimFretPairResult(
            pair=pair,
            status=FlimFretStatus.SUCCEEDED,
            reason=None,
            rows=[
                {
                    "pair_name": pair.name,
                    "donor_dataset": pair.donor_h5.name,
                    "da_dataset": pair.da_h5.name,
                    "cell_id": "",
                    "donor_mean_lifetime": 2.5,
                    "da_mean_lifetime": 2.0,
                    "fret_efficiency": 0.2,
                    "n_pixels_donor": 16,
                    "n_pixels_da": 16,
                    "n_cells_donor_reference": "",
                    "n_da_cells_skipped": "",
                }
            ],
            n_pixels_donor=16,
            n_cells_donor_reference=0,
            n_da_cells_skipped=0,
        )
        progress_callback(pair, result)
        run_log.log(phase="flim_fret", event="pair_done")
        return FlimFretReport(results=[result])

    dlg = FlimFretDialog(orchestrator=fake_orchestrator)
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    out_parent = tmp_path / "out"
    dlg._output_folder_edit.setText(str(out_parent))
    _add_configured_pair(dlg, donor_path=donor, da_path=da)

    # Suppress the summary QMessageBox in tests.
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *args, **kwargs: None)
    )

    dlg._on_start_clicked()

    assert dlg.workflow_config is not None
    assert dlg.last_run_folder is not None
    assert dlg.last_run_folder.parent == out_parent
    assert dlg.last_run_folder.name.startswith("flim_fret_run_")
    csv = dlg.last_run_folder / "flim_fret_results.csv"
    assert csv.exists()
    text = csv.read_text()
    # Identity columns first, then data columns.
    assert text.splitlines()[0].startswith(
        "pair_name,donor_dataset,da_dataset,cell_id,"
    )
    assert "0.2" in text


def test_start_blocks_when_create_run_folder_fails(qtbot, tmp_path, monkeypatch):
    donor = _good_dataset(tmp_path, "donor.h5")
    da = _good_dataset(tmp_path, "da.h5")
    dlg = FlimFretDialog(orchestrator=MagicMock())
    qtbot.addWidget(dlg)
    dlg._set_source_folder(tmp_path)
    dlg._output_folder_edit.setText(str(tmp_path / "out"))
    _add_configured_pair(dlg, donor_path=donor, da_path=da)

    # Simulate read-only output folder by making create_run_folder raise.
    from percell4.gui import flim_fret_dialog as mod

    def _raise(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(mod, "create_run_folder", _raise)
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, msg: warned.append(msg)),
    )
    dlg._on_start_clicked()

    assert any("read-only filesystem" in m for m in warned)
    assert dlg.workflow_config is None  # not accepted


# ── Static checks on dialog independence ──────────────────


def test_dialog_does_not_mutate_session_state():
    """Action contract: this dialog must not touch session.set_* anywhere."""
    import inspect

    import percell4.gui.flim_fret_dialog as mod

    src = inspect.getsource(mod)
    assert "session.set_active" not in src
    assert "session.active_channel =" not in src
    assert "viewer_win.add_" not in src
