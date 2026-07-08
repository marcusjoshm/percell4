"""Tests for the Phasor (.npz) tab in AddLayerDialog.

The tab is a thin orchestration layer over ImportPhasorNpz (U6) and
the AddLayerDialog scaffolding. Tests cover:
- Tab construction (label, scroll wrapping for CI compliance)
- Empty state visibility + Import button enable/disable
- Multi-file additive behavior
- Per-row probe (channel inference, validation chip)
- Per-row remove
- Conflict re-evaluation at Import time
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QFileDialog

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.add_layer_dialog import AddLayerDialog
from percell4.model import CellDataModel


class FakeRepo:
    def __init__(self):
        self.arrays: dict[str, np.ndarray] = {}
        self.disk_metadata: dict = {}

    def write_array(self, handle, path, data, attrs=None):
        self.arrays[path] = data

    def read_array(self, handle, path, view_bin=1):
        if path not in self.arrays:
            raise KeyError(f"Array not found: {path}")
        return self.arrays[path]

    def read_metadata(self, handle):
        return dict(self.disk_metadata)

    def delete_path(self, handle, path):
        prefix = path.rstrip("/") + "/"
        keys = [k for k in list(self.arrays) if k.startswith(prefix) or k == path]
        for k in keys:
            del self.arrays[k]
        return bool(keys)


class FakeStore:
    """Minimal stand-in for DatasetStore — the dialog only uses .path."""

    def __init__(self, path):
        self.path = Path(path)


@pytest.fixture
def session_with_dataset(tmp_path):
    s = Session()
    handle = DatasetHandle(
        path=tmp_path / "fake.h5",
        metadata={"channel_names": ["mTQ2"], "flim_frequency_mhz": 80.0},
    )
    s._dataset = handle
    return s


def _full_bundle_npz(tmp_path: Path, channel: str = "mTQ2") -> Path:
    import json as _json
    rng = np.random.default_rng(0)
    metadata = {"schema_version": 1, "channel": channel}
    metadata_bytes = _json.dumps(metadata).encode("utf-8")
    path = tmp_path / f"src_{channel}_phasor.npz"
    np.savez(
        path,
        g=rng.uniform(size=(8, 8)).astype(np.float32),
        s=rng.uniform(size=(8, 8)).astype(np.float32),
        g_filtered=rng.uniform(size=(8, 8)).astype(np.float32),
        s_filtered=rng.uniform(size=(8, 8)).astype(np.float32),
        lifetime_filtered=rng.uniform(size=(8, 8)).astype(np.float32),
        metadata=np.frombuffer(metadata_bytes, dtype=np.uint8),
    )
    return path


def _raw_only_npz(tmp_path: Path, channel: str = "mTQ2") -> Path:
    rng = np.random.default_rng(0)
    path = tmp_path / f"raw_{channel}_phasor.npz"
    np.savez(
        path,
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(4, 4)).astype(np.float32),
    )
    return path


@pytest.fixture
def dialog(qtbot, session_with_dataset, tmp_path):
    from qtpy.QtWidgets import QWidget

    repo = FakeRepo()
    store = FakeStore(tmp_path / "fake.h5")
    data_model = CellDataModel(session_with_dataset)

    # AddLayerDialog requires a real QWidget parent and reaches into
    # parent._repo. Build a tiny widget that satisfies both.
    launcher = QWidget()
    launcher._repo = repo
    qtbot.addWidget(launcher)

    dlg = AddLayerDialog(launcher, store, data_model, viewer_win=None)
    qtbot.addWidget(dlg)
    dlg._test_repo = repo
    return dlg


# ── Tab registration ─────────────────────────────────────────


def test_phasor_npz_tab_registered(dialog):
    """Tab is at the expected position with the expected label."""
    labels = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
    assert "Phasor (.npz)" in labels


def test_phasor_npz_tab_uses_wrap_in_scroll(dialog):
    """Compliance test rule: every tab content must be wrapped in a QScrollArea."""
    # Find the phasor tab
    idx = next(
        i for i in range(dialog._tabs.count())
        if dialog._tabs.tabText(i) == "Phasor (.npz)"
    )
    tab = dialog._tabs.widget(idx)
    # Walk the tab's children for a QScrollArea (wrap_in_scroll's output)
    from qtpy.QtWidgets import QScrollArea
    scrolls = tab.findChildren(QScrollArea)
    assert len(scrolls) > 0, "Phasor (.npz) tab content must be wrapped in QScrollArea"


# ── Empty state ──────────────────────────────────────────────


def test_empty_state_shows_placeholder(dialog):
    """Empty state placeholder is not hidden; table is hidden until rows added."""
    assert not dialog._phasor_npz_empty.isHidden()
    assert dialog._phasor_npz_table.isHidden()


def test_empty_state_disables_import_button(dialog):
    assert not dialog._phasor_npz_import_btn.isEnabled()


# ── Add files (additive + dedup) ─────────────────────────────


def test_add_one_file_populates_table(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )

    dialog._on_phasor_npz_add_files()

    assert dialog._phasor_npz_table.rowCount() == 1
    assert not dialog._phasor_npz_table.isHidden()
    assert dialog._phasor_npz_empty.isHidden()


def test_add_files_twice_appends_rows(dialog, tmp_path, monkeypatch):
    npz1 = _full_bundle_npz(tmp_path, channel="mTQ2")
    npz2 = _full_bundle_npz(tmp_path, channel="CA-SiR")

    # First call: one file
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz1)], "")),
    )
    dialog._on_phasor_npz_add_files()
    assert dialog._phasor_npz_table.rowCount() == 1

    # Second call: another file → table grows
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz2)], "")),
    )
    dialog._on_phasor_npz_add_files()
    assert dialog._phasor_npz_table.rowCount() == 2


def test_add_same_file_twice_dedups(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )

    dialog._on_phasor_npz_add_files()
    dialog._on_phasor_npz_add_files()

    assert dialog._phasor_npz_table.rowCount() == 1


def test_clear_all_empties_table(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()

    dialog._on_phasor_npz_clear()
    assert dialog._phasor_npz_table.rowCount() == 0
    assert not dialog._phasor_npz_empty.isHidden()


# ── Per-row probe ─────────────────────────────────────────────


def test_channel_inferred_from_metadata(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path, channel="mTQ2")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()

    target_edit = dialog._phasor_npz_table.cellWidget(
        0, dialog._PHASOR_COL_TARGET,
    )
    assert target_edit.text() == "mTQ2"


def test_channel_inferred_from_filename_when_no_metadata(dialog, tmp_path, monkeypatch):
    """Filename pattern <stem>_<channel>_phasor.npz works when metadata absent."""
    rng = np.random.default_rng(0)
    path = tmp_path / "src_CA-SiR_phasor.npz"
    np.savez(
        path,
        g=rng.uniform(size=(4, 4)).astype(np.float32),
        s=rng.uniform(size=(4, 4)).astype(np.float32),
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(path)], "")),
    )
    dialog._on_phasor_npz_add_files()

    target_edit = dialog._phasor_npz_table.cellWidget(
        0, dialog._PHASOR_COL_TARGET,
    )
    assert target_edit.text() == "CA-SiR"


def test_invalid_npz_marked_with_error_chip(dialog, tmp_path, monkeypatch):
    """Invalid file → error in detected-channel cell, action defaults to Skip."""
    rng = np.random.default_rng(0)
    bad = tmp_path / "bad.npz"
    np.savez(bad, only_g=rng.uniform(size=(4, 4)))  # missing 'g' and 's'

    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(bad)], "")),
    )
    dialog._on_phasor_npz_add_files()

    detected_item = dialog._phasor_npz_table.item(0, dialog._PHASOR_COL_DETECTED)
    assert detected_item.foreground().color() == Qt.red

    action_combo = dialog._phasor_npz_table.cellWidget(0, dialog._PHASOR_COL_ACTION)
    assert action_combo.currentText() == "Skip"
    assert not action_combo.isEnabled()


# ── Conflict probe ───────────────────────────────────────────


def test_conflict_detected_when_target_exists(dialog, tmp_path, monkeypatch):
    """Pre-populate /phasor/mTQ2 → conflict flag set; default action is Skip."""
    rng = np.random.default_rng(0)
    dialog._test_repo.arrays["phasor/mTQ2/g"] = rng.uniform(size=(4, 4)).astype(np.float32)

    npz = _full_bundle_npz(tmp_path, channel="mTQ2")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()

    conflict_item = dialog._phasor_npz_table.item(0, dialog._PHASOR_COL_CONFLICT)
    assert conflict_item.text() == "Yes"
    action_combo = dialog._phasor_npz_table.cellWidget(0, dialog._PHASOR_COL_ACTION)
    assert "Overwrite" in [
        action_combo.itemText(i) for i in range(action_combo.count())
    ]
    # Defaults to Skip for safety
    assert action_combo.currentText() == "Skip"


# ── Import button state ──────────────────────────────────────


def test_import_button_enabled_when_actionable_row_exists(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()

    assert dialog._phasor_npz_import_btn.isEnabled()


def test_import_button_disabled_when_all_rows_skip(dialog, tmp_path, monkeypatch):
    """Add a valid file, then set its action to Skip → button disables."""
    npz = _full_bundle_npz(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()
    assert dialog._phasor_npz_import_btn.isEnabled()

    action_combo = dialog._phasor_npz_table.cellWidget(0, dialog._PHASOR_COL_ACTION)
    action_combo.setCurrentText("Skip")
    assert not dialog._phasor_npz_import_btn.isEnabled()


# ── Import action ────────────────────────────────────────────


def test_import_writes_to_repo(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path, channel="mTQ2")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()

    dialog._on_phasor_npz_import()

    assert "phasor/mTQ2/g" in dialog._test_repo.arrays
    assert "phasor/mTQ2/g_filtered" in dialog._test_repo.arrays


def test_remove_row_drops_it_from_table(dialog, tmp_path, monkeypatch):
    npz = _full_bundle_npz(tmp_path)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(npz)], "")),
    )
    dialog._on_phasor_npz_add_files()
    assert dialog._phasor_npz_table.rowCount() == 1

    dialog._on_phasor_npz_remove_row(str(npz))
    assert dialog._phasor_npz_table.rowCount() == 0
