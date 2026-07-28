"""Tests for :class:`WholeFieldIntensityDialog` (U8)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.analysis import (
    BatchAnalysisItemResult,
    BatchAnalysisReport,
)
from percell4.application.analysis import registry as registry_mod
from percell4.application.analysis.modules.whole_field_intensity import (  # noqa: F401
    WholeFieldIntensity,
)
from percell4.gui.settings import app_settings
from percell4.gui.whole_field_intensity_dialog import (
    _NO_PRESET,
    _QSETTINGS_OUTPUT_KEY,
    WholeFieldIntensityDialog,
)
from percell4.store import DatasetStore


@pytest.fixture(autouse=True)
def _reregister() -> Iterator[None]:
    if "whole_field_intensity" not in registry_mod._REGISTRY:
        import percell4.application.analysis.modules.whole_field_intensity as mod
        importlib.reload(mod)
    yield


@pytest.fixture(autouse=True)
def _clear_qsettings() -> Iterator[None]:
    qs = app_settings()
    qs.remove(_QSETTINGS_OUTPUT_KEY)
    yield
    qs.remove(_QSETTINGS_OUTPUT_KEY)


def _build_h5(path: Path) -> None:
    h = w = 24
    halo = np.full((h, w), 30.0, np.float32)
    mng = np.full((h, w), 50.0, np.float32)
    intensity = np.stack([halo, mng], axis=0)
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": ["Halo", "mNG"]})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    for m in ("pbody", "dilute", "dcp2", "dcp2_2", "interaction",
              "interaction_2", "sir"):
        arr = np.zeros((h, w), np.uint8)
        arr[4:12, 4:12] = 1
        store.write_array(f"masks/{m}", arr)
    cp = np.zeros((h, w), np.int32)
    cp[:12, :] = 1
    cp[12:, :] = 2
    store.write_array("labels/cells", cp)


def test_dialog_constructs(qtbot):
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == WholeFieldIntensity.display_name


def test_dialog_class_registered_on_analysis_cls():
    assert WholeFieldIntensity.dialog_class is WholeFieldIntensityDialog


def test_start_disabled_until_required_roles_and_output(qtbot, tmp_path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    assert dlg._start_btn.isEnabled() is False
    dlg._add_paths([h5])
    for role, layer in [("condensate_mask", "pbody"), ("dilute_mask", "dilute"),
                        ("halo", "Halo"), ("mng", "mNG")]:
        dlg._role_combos[role].setCurrentText(layer)
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is True


def test_preset_lock_disables_params(qtbot):
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    idx = dlg._preset_combo.findText("decapping-sensor-v2")
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)
    assert dlg._param_widgets["min_size"].isEnabled() is False


def test_bg_value_spin_enabled_only_on_manual(qtbot):
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    # Default mng_bg_mode is 'mean' -> manual value disabled.
    dlg._param_setters["mng_bg_mode"]("mean")
    dlg._refresh_state()
    assert dlg._param_widgets["mng_bg_value"].isEnabled() is False
    dlg._param_setters["mng_bg_mode"]("manual")
    dlg._refresh_state()
    assert dlg._param_widgets["mng_bg_value"].isEnabled() is True


def test_intermediate_assemblies_gated_on_four_masks(qtbot, tmp_path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._refresh_state()
    inter = dlg._param_widgets["intermediate_assemblies"]
    assert inter.isEnabled() is False  # masks not mapped
    for role, layer in [("mng_mask", "dcp2"),
                        ("interaction_mask", "interaction"),
                        ("dcp2_mask_2", "dcp2_2"),
                        ("interaction_mask_2", "interaction_2")]:
        dlg._role_combos[role].setCurrentText(layer)
    dlg._refresh_state()
    assert inter.isEnabled() is True


def test_single_cell_gated_on_cp_mask(qtbot, tmp_path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._refresh_state()
    sc = dlg._param_widgets["single_cell"]
    assert sc.isEnabled() is False
    dlg._role_combos["cp_mask"].setCurrentText("cells")
    dlg._refresh_state()
    assert sc.isEnabled() is True


def test_channel_cell_mean_greyed_unless_single_cell(qtbot, tmp_path):
    """U5: the channel_cell_mean checkbox appears and is greyed unless
    single_cell is on (which itself needs a cp_mask)."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._refresh_state()
    channel_chk = dlg._param_widgets["channel_cell_mean"]
    # single_cell off (no cp_mask) -> the checkbox is disabled.
    assert channel_chk.isEnabled() is False
    # Assign cp_mask, then turn single_cell on -> the checkbox enables.
    dlg._role_combos["cp_mask"].setCurrentText("cells")
    dlg._refresh_state()
    dlg._param_setters["single_cell"](True)
    dlg._refresh_state()
    assert dlg._param_widgets["single_cell"].isEnabled() is True
    assert channel_chk.isEnabled() is True


def test_export_particles_checkbox_and_output_panel(qtbot, tmp_path):
    """The export_particles checkbox auto-renders (plain, no requires gating),
    and toggling it on marks condensate_particle_table produced (✔) in the
    outputs panel; off, the output is greyed."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._refresh_state()

    assert "export_particles" in dlg._param_widgets
    chk = dlg._param_widgets["export_particles"]
    assert chk.isEnabled() is True  # no requires → plain, enabled checkbox

    label = dlg._output_labels["condensate_particle_table"]
    assert "✔" not in label.text()  # off → greyed / struck through

    dlg._param_setters["export_particles"](True)
    dlg._refresh_state()
    assert label.text() == "✔ condensate_particle_table"


def test_export_particles_stays_editable_under_preset(qtbot, tmp_path):
    """export_particles is a preset-editable mode toggle, so it stays clickable
    even when a preset locks the science params (e.g. min_size)."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    _select_preset(dlg, "decapping-sensor-v2")
    dlg._refresh_state()
    assert dlg._param_widgets["min_size"].isEnabled() is False  # locked
    assert dlg._param_widgets["export_particles"].isEnabled() is True


def test_start_dispatches_with_preset(qtbot, tmp_path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stub = MagicMock(
        return_value=BatchAnalysisReport(
            items=(BatchAnalysisItemResult(
                h5_path=h5, status="succeeded",
                produced_outputs=("whole_field_table",)),),
            run_folder=out_dir, cancelled=False,
        )
    )
    dlg = WholeFieldIntensityDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    for role, layer in [("condensate_mask", "pbody"), ("dilute_mask", "dilute"),
                        ("halo", "Halo"), ("mng", "mNG"), ("mng_mask", "dcp2"),
                        ("sir_mask", "sir")]:
        dlg._role_combos[role].setCurrentText(layer)
    idx = dlg._preset_combo.findText("decapping-sensor-v2")
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()

    from qtpy.QtWidgets import QMessageBox
    orig = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    try:
        dlg._on_start_clicked()
    finally:
        QMessageBox.information = orig

    assert stub.call_count == 1
    args, kwargs = stub.call_args
    assert args[0] == "whole_field_intensity"
    assert kwargs["preset"] == "decapping-sensor-v2"
    # Under a preset the dialog now passes ONLY the editable "mode" params as
    # an overlay (here at their gated values — cp_mask unassigned, so
    # single_cell is gated off, which in turn unchecks channel_cell_mean;
    # export_particles is an independent mode toggle, default off);
    # resolve_params merges them onto the preset.
    assert kwargs["params"] == {
        "single_cell": False,
        "channel_cell_mean": False,
        "export_particles": False,
    }


def test_v6_single_cell_clickable_and_dispatched(qtbot, tmp_path):
    """A preset locks its science params but leaves the editable "mode" params
    (single_cell, channel_cell_mean) clickable: under v6 with cp_mask assigned,
    single_cell is enabled (a science param like min_size stays locked),
    toggling it enables channel_cell_mean, and Start dispatches preset=v6 with
    the editable overlay (single_cell=True)."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stub = MagicMock(
        return_value=BatchAnalysisReport(
            items=(BatchAnalysisItemResult(
                h5_path=h5, status="succeeded",
                produced_outputs=("whole_field_table",)),),
            run_folder=out_dir, cancelled=False,
        )
    )
    dlg = WholeFieldIntensityDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    for role, layer in [("condensate_mask", "pbody"), ("dilute_mask", "dilute"),
                        ("halo", "Halo"), ("mng", "mNG"), ("mng_mask", "dcp2"),
                        ("interaction_mask", "interaction"), ("cp_mask", "cells")]:
        dlg._role_combos[role].setCurrentText(layer)
    _select_preset(dlg, "decapping-sensor-v6")
    dlg._refresh_state()

    # Science param locked; single_cell editable (cp_mask is assigned).
    assert dlg._param_widgets["min_size"].isEnabled() is False
    assert dlg._param_widgets["single_cell"].isEnabled() is True
    # Toggle single_cell on → channel_cell_mean becomes clickable.
    dlg._param_setters["single_cell"](True)
    dlg._refresh_state()
    assert dlg._param_widgets["channel_cell_mean"].isEnabled() is True

    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    from qtpy.QtWidgets import QMessageBox
    orig = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    try:
        dlg._on_start_clicked()
    finally:
        QMessageBox.information = orig

    assert stub.call_count == 1
    _, kwargs = stub.call_args
    assert kwargs["preset"] == "decapping-sensor-v6"
    assert kwargs["params"]["single_cell"] is True


# ── U3: preset-aware required / hidden roles (dormant capability) ──────
#
# Whole-field declares NO preset_required/hidden_inputs yet (that's U4),
# so these exercise the generic capability by monkeypatching the schema
# fields for an existing preset.


def _assign_base_roles(dlg: WholeFieldIntensityDialog) -> None:
    for role, layer in [
        ("condensate_mask", "pbody"),
        ("dilute_mask", "dilute"),
        ("halo", "Halo"),
        ("mng", "mNG"),
    ]:
        dlg._role_combos[role].setCurrentText(layer)


def _select_preset(dlg: WholeFieldIntensityDialog, name: str) -> None:
    idx = dlg._preset_combo.findText(name)
    dlg._preset_combo.setCurrentIndex(idx)
    dlg._on_preset_changed(idx)


def test_preset_required_role_blocks_start(qtbot, tmp_path, monkeypatch):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(
        WholeFieldIntensity,
        "preset_required_inputs",
        {"decapping-sensor-v2": ("mng_mask",)},
        raising=False,
    )
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    _assign_base_roles(dlg)
    dlg._output_parent_line.setText(str(out_dir))
    _select_preset(dlg, "decapping-sensor-v2")

    # mng_mask unassigned -> Start disabled, reason names the role.
    assert dlg._start_btn.isEnabled() is False
    reason = dlg._start_disabled_reason()
    assert reason is not None and "mng_mask" in reason

    # Assigning the required role enables Start.
    dlg._role_combos["mng_mask"].setCurrentText("dcp2")
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is True


def test_preset_hidden_role_hides_and_restores(qtbot, tmp_path, monkeypatch):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    monkeypatch.setattr(
        WholeFieldIntensity,
        "preset_hidden_inputs",
        {"decapping-sensor-v2": ("sir_mask",)},
        raising=False,
    )
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])

    # Assign sir_mask BEFORE selecting the hiding preset.
    dlg._role_combos["sir_mask"].setCurrentText("sir")
    dlg._refresh_state()
    assert dlg._role_combos["sir_mask"].isHidden() is False
    assert dlg._resolve_layer_map().get("sir_mask") == "sir"

    # Selecting the hiding preset hides the row + excludes it from the map.
    _select_preset(dlg, "decapping-sensor-v2")
    assert dlg._role_combos["sir_mask"].isHidden() is True
    assert dlg._role_labels["sir_mask"].isHidden() is True
    assert "sir_mask" not in dlg._resolve_layer_map()
    # The combo VALUE is preserved (no destructive clear).
    assert dlg._role_combos["sir_mask"].currentText() == "sir"

    # Switching back to No preset restores the row + the prior selection.
    _select_preset(dlg, _NO_PRESET)
    assert dlg._role_combos["sir_mask"].isHidden() is False
    assert dlg._role_combos["sir_mask"].currentText() == "sir"
    assert dlg._resolve_layer_map().get("sir_mask") == "sir"


def test_no_preset_no_required_or_hidden_roles(qtbot, tmp_path):
    """With no preset, nothing is required/hidden beyond the base schema."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    dlg = WholeFieldIntensityDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._role_combos["sir_mask"].setCurrentText("sir")
    dlg._refresh_state()
    assert dlg._preset_required_roles() == ()
    assert dlg._preset_hidden_roles() == ()
    assert dlg._role_combos["sir_mask"].isHidden() is False
    assert dlg._resolve_layer_map().get("sir_mask") == "sir"
