"""Tests for :class:`WholeFieldIntensityDialog` (U8)."""

from __future__ import annotations

import importlib
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
from percell4.application.analysis.modules.whole_field_intensity import (  # noqa: F401
    WholeFieldIntensity,
)
from percell4.gui.whole_field_intensity_dialog import (
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
    qs = QSettings("LeeLabPerCell4", "PerCell4")
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
    for role, layer in [("pbody_mask", "pbody"), ("dilute_mask", "dilute"),
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
    for role, layer in [("dcp2_mask", "dcp2"),
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
    for role, layer in [("pbody_mask", "pbody"), ("dilute_mask", "dilute"),
                        ("halo", "Halo"), ("mng", "mNG"), ("dcp2_mask", "dcp2"),
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
    assert kwargs["params"] is None
