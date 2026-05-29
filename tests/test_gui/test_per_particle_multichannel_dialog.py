"""Tests for :class:`PerParticleMultichannelDialog` (U5)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from qtpy.QtCore import QSettings, Qt

from percell4.application.analysis import (
    BatchAnalysisItemResult,
    BatchAnalysisReport,
)
from percell4.application.analysis import registry as registry_mod
from percell4.application.analysis.modules.per_particle_multichannel import (  # noqa: F401
    PerParticleMultichannel,
)
from percell4.gui.analysis_widgets import LAYER_SENTINEL
from percell4.gui.per_particle_multichannel_dialog import (
    _MAX_CHANNELS,
    _QSETTINGS_OUTPUT_KEY,
    PerParticleMultichannelDialog,
)
from percell4.store import DatasetStore


@pytest.fixture(autouse=True)
def _reregister_analysis() -> Iterator[None]:
    if "per_particle_multichannel" not in registry_mod._REGISTRY:
        import percell4.application.analysis.modules.per_particle_multichannel as mod
        importlib.reload(mod)
    yield


@pytest.fixture(autouse=True)
def _clear_qsettings() -> Iterator[None]:
    qs = QSettings("LeeLabPerCell4", "PerCell4")
    qs.remove(_QSETTINGS_OUTPUT_KEY)
    yield
    qs.remove(_QSETTINGS_OUTPUT_KEY)


def _build_full_h5(path: Path) -> None:
    """Synthetic h5 with 3 intensity channels + a mask + a cp_mask."""
    h = w = 32
    chans = ["mNG", "mTQ2", "CA-SiR"]
    intensity = np.stack(
        [np.full((h, w), 100.0 * (i + 1), dtype=np.float32)
         for i in range(len(chans))],
        axis=0,
    )
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[8:14, 8:14] = 1
    cp = np.zeros((h, w), dtype=np.int32)
    cp[:16, :] = 1
    cp[16:, :] = 2
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": chans})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_array("masks/particles", mask)
    store.write_array("labels/cells", cp)


# ── Construction ──────────────────────────────────────────────


def test_dialog_constructs(qtbot):
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == PerParticleMultichannel.display_name
    assert len(dlg._channel_rows) == 1  # starts with channel_1


def test_dialog_class_registered_on_analysis_cls():
    assert PerParticleMultichannel.dialog_class is PerParticleMultichannelDialog


# ── Dynamic channel rows ──────────────────────────────────────


def test_add_channel_up_to_max(qtbot):
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    for _ in range(_MAX_CHANNELS + 3):
        dlg._add_channel_row()
    assert len(dlg._channel_rows) == _MAX_CHANNELS
    assert dlg._add_channel_btn.isEnabled() is False
    assert f"{_MAX_CHANNELS}/{_MAX_CHANNELS}" in dlg._channel_status.text()


def test_remove_channel_row_keeps_at_least_one(qtbot):
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_channel_row()
    dlg._add_channel_row()
    assert len(dlg._channel_rows) == 3
    # Remove the middle row.
    middle_combo = dlg._channel_rows[1][1]
    dlg._remove_channel_row(middle_combo)
    assert len(dlg._channel_rows) == 2
    # Removing down to one, then attempting again, is a no-op.
    dlg._remove_channel_row(dlg._channel_rows[1][1])
    assert len(dlg._channel_rows) == 1
    dlg._remove_channel_row(dlg._channel_rows[0][1])
    assert len(dlg._channel_rows) == 1


# ── Start gating ──────────────────────────────────────────────


def test_start_disabled_when_no_datasets(qtbot):
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    assert dlg._start_btn.isEnabled() is False


def test_start_enabled_when_mask_channel_and_output_set(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._mask_combo.setCurrentText("particles")
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is True


def test_start_disabled_without_channel(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._mask_combo.setCurrentText("particles")
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is False  # no channel mapped


# ── Combo population ──────────────────────────────────────────


def test_channel_combo_populates_from_intersection(qtbot, tmp_path):
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    _build_full_h5(a)
    _build_full_h5(b)

    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([a, b])
    combo = dlg._channel_rows[0][1]
    items = [combo.itemText(i) for i in range(combo.count())]
    assert LAYER_SENTINEL in items
    assert "mNG" in items and "mTQ2" in items and "CA-SiR" in items
    # mask combo has the mask name.
    mask_items = [dlg._mask_combo.itemText(i)
                  for i in range(dlg._mask_combo.count())]
    assert "particles" in mask_items


# ── requires gating ───────────────────────────────────────────


def test_single_cell_gated_on_cp_mask(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._refresh_state()
    sc = dlg._param_widgets["single_cell"]
    assert sc.isEnabled() is False  # cp_mask not assigned yet
    dlg._cp_mask_combo.setCurrentText("cells")
    dlg._refresh_state()
    assert sc.isEnabled() is True


# ── Start dispatches to the orchestrator with layer_map ───────


def test_start_dispatches_with_channel_layer_map(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    stub = MagicMock(
        return_value=BatchAnalysisReport(
            items=(
                BatchAnalysisItemResult(
                    h5_path=h5, status="succeeded",
                    produced_outputs=("particle_table",),
                ),
            ),
            run_folder=out_dir,
            cancelled=False,
        )
    )

    dlg = PerParticleMultichannelDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._mask_combo.setCurrentText("particles")
    dlg._add_channel_row()  # now two channel rows
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._channel_rows[1][1].setCurrentText("mTQ2")
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
    assert args[0] == "per_particle_multichannel"
    layer_map = args[2](h5)
    assert layer_map["mask"] == "particles"
    assert layer_map["channel_1"] == "mNG"
    assert layer_map["channel_2"] == "mTQ2"
    assert kwargs["preset"] is None


# ── per-channel cell-mean checkbox (U4) ───────────────────────────


def _cell_mean_layers(dlg: PerParticleMultichannelDialog) -> set[str]:
    """The set of layer names currently selected for a whole-cell mean,
    derived the same way run() does — pairing each row's combo with that
    row's cell-mean flag by position."""
    lm = dlg._resolve_layer_map()
    params = dlg._collect_params()
    return {
        lm[f"channel_{i + 1}"]
        for i in range(len(dlg._channel_rows))
        if f"channel_{i + 1}" in lm and params[f"channel_{i + 1}_cell_mean"]
    }


def test_cell_mean_checkbox_excluded_from_generic_params_form(qtbot):
    """The 8 channel_i_cell_mean bools render as per-row checkboxes, not in
    the generic Parameters form."""
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    for key in dlg._param_widgets:
        assert not key.endswith("_cell_mean")
    # But they are still collected (defaulting False) so run() always sees them.
    params = dlg._collect_params()
    for i in range(1, _MAX_CHANNELS + 1):
        assert params[f"channel_{i}_cell_mean"] is False


def test_cell_mean_checkbox_collected_when_checked(qtbot, tmp_path):
    """R4: checking a row's box sets that row's channel_i_cell_mean True."""
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cp_mask_combo.setCurrentText("cells")  # enables the checkboxes
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._refresh_state()
    dlg._channel_rows[0][3].setChecked(True)
    params = dlg._collect_params()
    assert params["channel_1_cell_mean"] is True
    assert all(
        params[f"channel_{i}_cell_mean"] is False
        for i in range(2, _MAX_CHANNELS + 1)
    )


def test_cell_mean_checkbox_signal_path_via_set_check_state(qtbot, tmp_path):
    """qt-wire-user-edit-signals: toggling via setCheckState (the signal path
    a real click takes) flows through to collected params — guards against an
    unwired widget that only works for programmatic reads."""
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cp_mask_combo.setCurrentText("cells")
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._refresh_state()
    dlg._channel_rows[0][3].setCheckState(Qt.Checked)
    assert dlg._collect_params()["channel_1_cell_mean"] is True


def test_cell_mean_checkbox_gated_on_cp_mask(qtbot, tmp_path):
    """Without a cp_mask the checkboxes are disabled and report False."""
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._refresh_state()  # no cp_mask assigned
    chk = dlg._channel_rows[0][3]
    assert chk.isEnabled() is False
    # Even if forced checked, the gating clears it on the next refresh.
    chk.setChecked(True)
    dlg._refresh_state()
    assert chk.isChecked() is False
    assert dlg._collect_params()["channel_1_cell_mean"] is False


def test_cell_mean_checkbox_re_enabled_when_cp_mask_assigned(qtbot, tmp_path):
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._refresh_state()
    assert dlg._channel_rows[0][3].isEnabled() is False
    dlg._cp_mask_combo.setCurrentText("cells")
    dlg._refresh_state()
    assert dlg._channel_rows[0][3].isEnabled() is True


def test_cell_mean_pairing_survives_middle_row_removal(qtbot, tmp_path):
    """Correctness trap: after removing a middle channel row, the cell-mean
    flag must stay paired with its layer (not its old slot index)."""
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._cp_mask_combo.setCurrentText("cells")
    dlg._add_channel_row()
    dlg._add_channel_row()  # three rows now
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._channel_rows[1][1].setCurrentText("mTQ2")
    dlg._channel_rows[2][1].setCurrentText("CA-SiR")
    dlg._refresh_state()
    # Select cell-mean on the CA-SiR (third) row only.
    dlg._channel_rows[2][3].setChecked(True)
    assert _cell_mean_layers(dlg) == {"CA-SiR"}
    # Remove the middle (mTQ2) row.
    dlg._remove_channel_row(dlg._channel_rows[1][1])
    # The cell-mean selection must still point at CA-SiR, now at slot 2.
    assert _cell_mean_layers(dlg) == {"CA-SiR"}


def test_duplicate_layer_disables_start(qtbot, tmp_path):
    """Mapping the same layer to two channel rows disables Start rather than
    silently collapsing to one channel."""
    h5 = tmp_path / "ds.h5"
    _build_full_h5(h5)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dlg = PerParticleMultichannelDialog()
    qtbot.addWidget(dlg)
    dlg._add_paths([h5])
    dlg._mask_combo.setCurrentText("particles")
    dlg._add_channel_row()
    dlg._channel_rows[0][1].setCurrentText("mNG")
    dlg._channel_rows[1][1].setCurrentText("mNG")  # duplicate
    dlg._output_parent_line.setText(str(out_dir))
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is False
    # Fixing the duplicate re-enables Start.
    dlg._channel_rows[1][1].setCurrentText("mTQ2")
    dlg._refresh_state()
    assert dlg._start_btn.isEnabled() is True
