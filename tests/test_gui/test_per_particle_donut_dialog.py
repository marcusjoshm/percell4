"""Tests for :class:`PerParticleDonutDialog` (U7)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from qtpy.QtCore import QSettings

from percell4.application.analysis import (
    BatchAnalysisItemResult,
    BatchAnalysisReport,
)
from percell4.application.analysis import registry as registry_mod
from percell4.application.analysis.modules.per_particle_donut import (  # noqa: F401
    PerParticleDonut,
)
from percell4.gui.analysis_widgets import LAYER_SENTINEL
from percell4.gui.per_particle_donut_dialog import (
    _NO_PRESET,
    _QSETTINGS_OUTPUT_KEY,
    PerParticleDonutDialog,
)
from percell4.store import DatasetStore

# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reregister_analysis() -> Iterator[None]:
    if "per_particle_donut" not in registry_mod._REGISTRY:
        import importlib

        import percell4.application.analysis.modules.per_particle_donut as mod

        importlib.reload(mod)
    yield


@pytest.fixture(autouse=True)
def _clear_qsettings() -> Iterator[None]:
    qs = QSettings("LeeLabPerCell4", "PerCell4")
    qs.remove(_QSETTINGS_OUTPUT_KEY)
    yield
    qs.remove(_QSETTINGS_OUTPUT_KEY)


def _build_full_h5(path: Path) -> None:
    """Synthetic h5 with cap + pnorm channels and a pbody mask."""
    from skimage.draw import disk

    h, w = 32, 32
    cap = np.full((h, w), 1000.0, dtype=np.float32)
    pnorm = np.full((h, w), 1200.0, dtype=np.float32)
    mask = np.zeros((h, w), dtype=np.uint8)
    for r, c in [(8, 8), (24, 24)]:
        rr, cc = disk((r, c), 3, shape=(h, w))
        cap[rr, cc] += 6000.0
        pnorm[rr, cc] += 4500.0
        mask[rr, cc] = 1
    intensity = np.stack([cap, pnorm], axis=0)
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": ["Cap", "pnorm"]})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_array("masks/pbody", mask)


# ── Construction ──────────────────────────────────────────────


def test_dialog_constructs(qtbot):
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    assert dlg._dataset_list is not None
    assert dlg._cap_combo is not None
    assert dlg._preset_combo is not None
    assert dlg._start_btn is not None
    assert dlg._cancel_btn is not None
    # Both branches present.
    assert "pbody" in dlg._branch_skip
    assert "sg" in dlg._branch_skip
    # Param widgets exist for every declared parameter.
    assert set(dlg._param_widgets) == set(PerParticleDonut.parameters)
    # Output panel has one label per declared output.
    assert set(dlg._output_labels) == set(PerParticleDonut.outputs)


def test_dialog_class_registered_on_analysis_cls():
    """Importing the dialog module attaches the dialog to the analysis class."""
    assert PerParticleDonut.dialog_class is PerParticleDonutDialog


# ── Start enabled state ───────────────────────────────────────


def test_start_disabled_when_no_datasets(qtbot):
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    assert dlg._start_btn is not None
    assert dlg._start_btn.isEnabled() is False
    assert "Add at least one dataset" in dlg._start_btn.toolTip()


def test_start_disabled_reason_after_datasets_added(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)

    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    assert dlg._start_btn is not None
    assert dlg._start_btn.isEnabled() is False
    # No Cap selected yet.
    assert "Cap" in dlg._start_btn.toolTip()


def test_start_disabled_when_no_branch_satisfied(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)

    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    # Assign cap but neither branch fully.
    dlg._cap_combo.setCurrentText("Cap")
    dlg._refresh_state()
    assert dlg._start_btn is not None
    assert dlg._start_btn.isEnabled() is False
    assert "branch" in dlg._start_btn.toolTip().lower()


def test_start_enabled_when_branch_and_output_parent_set(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cap_combo.setCurrentText("Cap")
    dlg._branch_combos["pbody"]["pbody_mask"].setCurrentText("pbody")
    dlg._branch_combos["pbody"]["pnorm"].setCurrentText("pnorm")
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is True


# ── Layer combos populate from selected datasets ──────────────


def test_layer_combos_populate_from_intersection(qtbot, tmp_path):
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    _build_full_h5(a)
    _build_full_h5(b)

    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([a, b])
    # Cap combo includes Cap + pnorm + sentinel.
    cap_items = [dlg._cap_combo.itemText(i) for i in range(dlg._cap_combo.count())]
    assert LAYER_SENTINEL in cap_items
    assert "Cap" in cap_items
    assert "pnorm" in cap_items


# ── Preset lock affordance ────────────────────────────────────


def test_preset_lock_disables_param_widgets(qtbot):
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    # Pick a preset programmatically. Use _on_preset_changed directly to
    # fire the cascade (mimicking the combo's activated signal).
    idx = dlg._preset_combo.findText("m7g-cap-v1")
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)

    assert dlg._preset_lock_label is not None
    assert "Preset locked" in dlg._preset_lock_label.text()
    # Every param widget is disabled.
    for widget in dlg._param_widgets.values():
        assert widget.isEnabled() is False


def test_no_preset_restores_param_widget_enabled(qtbot):
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    idx = dlg._preset_combo.findText("m7g-cap-v1")
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)
    # Switch back to "No preset"
    idx = dlg._preset_combo.findText(_NO_PRESET)
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)
    assert dlg._preset_lock_label.text() == ""
    # Most widgets re-enable (single_cell may still be gated by requires).
    assert dlg._param_widgets["buffer"].isEnabled() is True


# ── BoolParam.requires gating ─────────────────────────────────


def test_single_cell_disabled_without_cp_mask(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    # No preset → requires gating active.
    sc_widget = dlg._param_widgets["single_cell"]
    assert sc_widget.isEnabled() is False
    assert "cp_mask" in sc_widget.toolTip()


# ── Outputs panel reflects produced_when ──────────────────────


def test_outputs_panel_dims_when_branch_not_satisfied(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    # Without selecting a branch's layers, pbody_table should be greyed.
    label = dlg._output_labels["pbody_table"]
    assert "line-through" in label.styleSheet()


def test_outputs_panel_lights_up_when_branch_satisfied(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cap_combo.setCurrentText("Cap")
    dlg._branch_combos["pbody"]["pbody_mask"].setCurrentText("pbody")
    dlg._branch_combos["pbody"]["pnorm"].setCurrentText("pnorm")
    dlg._refresh_state()
    label = dlg._output_labels["pbody_table"]
    assert "line-through" not in label.styleSheet()
    assert label.text().startswith("✔")
    # sg_table still struck through (no SG layers in the dataset).
    sg_label = dlg._output_labels["sg_table"]
    assert "line-through" in sg_label.styleSheet()


# ── Skip-branch semantics ─────────────────────────────────────


def test_skip_branch_disables_role_combos(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleDonutDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._branch_skip["pbody"].setChecked(True)
    dlg._refresh_state()
    assert dlg._branch_combos["pbody"]["pbody_mask"].isEnabled() is False
    assert dlg._branch_combos["pbody"]["pnorm"].isEnabled() is False
    # Skipping a branch drops its roles from the resolved layer_map.
    assert "pbody_mask" not in dlg._resolve_layer_map()


# ── Start dispatches to the orchestrator ──────────────────────


def test_start_dispatches_to_orchestrator(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    stub = MagicMock(
        return_value=BatchAnalysisReport(
            items=(
                BatchAnalysisItemResult(
                    h5_path=h5,
                    status="succeeded",
                    produced_outputs=("pbody_table",),
                ),
            ),
            run_folder=out_dir,
            cancelled=False,
        )
    )

    dlg = PerParticleDonutDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cap_combo.setCurrentText("Cap")
    dlg._branch_combos["pbody"]["pbody_mask"].setCurrentText("pbody")
    dlg._branch_combos["pbody"]["pnorm"].setCurrentText("pnorm")
    dlg._output_parent_line.setText(str(out_dir))
    # Pick preset m7g-cap-v1
    idx = dlg._preset_combo.findText("m7g-cap-v1")
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)
    dlg._refresh_state()

    # Click Start (the dialog opens a QMessageBox which we need to dismiss).
    # Patch QMessageBox.exec_ so the test doesn't hang.
    from qtpy.QtWidgets import QMessageBox

    orig_exec = QMessageBox.exec_
    QMessageBox.exec_ = lambda self_: 0
    try:
        dlg._on_start_clicked()
    finally:
        QMessageBox.exec_ = orig_exec

    assert stub.call_count == 1
    args, kwargs = stub.call_args
    assert args[0] == "per_particle_donut"
    assert args[1] == [h5]
    # layer_map_resolver is a callable returning the dialog's resolved map.
    resolver = args[2]
    layer_map = resolver(h5)
    assert layer_map["cap"] == "Cap"
    assert layer_map["pbody_mask"] == "pbody"
    assert layer_map["pnorm"] == "pnorm"
    assert kwargs["preset"] == "m7g-cap-v1"
    assert kwargs["params"] is None


def test_start_persists_output_parent(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stub = MagicMock(
        return_value=BatchAnalysisReport(
            items=(
                BatchAnalysisItemResult(
                    h5_path=h5, status="succeeded", produced_outputs=()
                ),
            ),
            run_folder=out_dir,
        )
    )
    dlg = PerParticleDonutDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cap_combo.setCurrentText("Cap")
    dlg._branch_combos["pbody"]["pbody_mask"].setCurrentText("pbody")
    dlg._branch_combos["pbody"]["pnorm"].setCurrentText("pnorm")
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    from qtpy.QtWidgets import QMessageBox

    QMessageBox.exec_ = lambda self_: 0
    dlg._on_start_clicked()
    qs = QSettings("LeeLabPerCell4", "PerCell4")
    assert qs.value(_QSETTINGS_OUTPUT_KEY, "", type=str) == str(out_dir)
