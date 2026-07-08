"""U2 tests: ExportImagesDialog view-bin checkbox + live label refresh.

The dialog gains a 'Apply current view bin (k=N) to exports' checkbox
whose label tracks ``session.active_bin`` live. When checked, the
caller reads ``selected_view_bin()`` which returns the current
``session.active_bin``; when unchecked or when active_bin==1, it
returns 1. R7 invariant: existing callers that don't pass a session
keep the native default.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from percell4.application.session import Session
from percell4.gui.export_images_dialog import ExportImagesDialog
from percell4.store import DatasetStore


def _make_store(tmp_path: Path) -> DatasetStore:
    """Minimal store with a 2-channel intensity so the channel group
    has something to render."""
    path = tmp_path / "ds.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch0", "ch1"]})
    store.write_array("intensity", np.zeros((2, 8, 8), dtype=np.uint16))
    return store


# ── Checkbox state + selected_view_bin contract ──────────────────────


def test_default_view_bin_one_when_no_session(qtbot, tmp_path):
    """R7 backward compat: callers that don't pass a session still get
    a dialog that reports view_bin=1 (the established default)."""
    store = _make_store(tmp_path)
    dlg = ExportImagesDialog(parent=None, store=store)
    qtbot.addWidget(dlg)

    assert dlg.selected_view_bin() == 1


def test_checkbox_disabled_when_active_bin_is_one(qtbot, tmp_path):
    """When session.active_bin == 1, the checkbox is visibly disabled
    and the label includes 'no effect at native'."""
    store = _make_store(tmp_path)
    session = Session()  # active_bin defaults to 1

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)

    assert not dlg._view_bin_check.isEnabled()
    assert "no effect" in dlg._view_bin_check.text().lower()
    assert dlg.selected_view_bin() == 1


def test_checkbox_enabled_when_active_bin_above_one(qtbot, tmp_path):
    """When active_bin > 1, the checkbox is enabled and the label
    shows the current k."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(4)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)

    assert dlg._view_bin_check.isEnabled()
    assert "k=4" in dlg._view_bin_check.text()


def test_unchecked_returns_one(qtbot, tmp_path):
    """Even when active_bin > 1, an unchecked box still resolves to 1."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(4)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    assert not dlg._view_bin_check.isChecked()
    assert dlg.selected_view_bin() == 1


def test_checked_returns_current_active_bin(qtbot, tmp_path):
    """Checked + active_bin=4 → selected_view_bin() returns 4."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(4)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    dlg._view_bin_check.setChecked(True)

    assert dlg.selected_view_bin() == 4


# ── Live label refresh on ACTIVE_BIN_CHANGED ─────────────────────────


def test_label_updates_live_when_active_bin_changes(qtbot, tmp_path):
    """Changing session.active_bin while the dialog is open updates
    the label without reopening — proves the subscription is wired."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(2)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    assert "k=2" in dlg._view_bin_check.text()

    session.set_active_bin(4)
    assert "k=4" in dlg._view_bin_check.text()


def test_dropping_to_one_disables_and_unchecks_live(qtbot, tmp_path):
    """If the user drops active_bin back to 1 while the box is checked,
    the box auto-unchecks AND becomes disabled. selected_view_bin
    correctly returns 1."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(4)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    dlg._view_bin_check.setChecked(True)

    session.set_active_bin(1)

    assert not dlg._view_bin_check.isEnabled()
    assert not dlg._view_bin_check.isChecked()
    assert dlg.selected_view_bin() == 1


def test_selected_view_bin_reads_live_not_cached(qtbot, tmp_path):
    """Open dialog with active_bin=2, check the box, change to 4 via
    session.set_active_bin(4), call selected_view_bin() → returns 4
    (the live value, not the construction-time snapshot)."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(2)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    dlg._view_bin_check.setChecked(True)
    assert dlg.selected_view_bin() == 2

    session.set_active_bin(4)
    assert dlg.selected_view_bin() == 4


# ── Subscription teardown ────────────────────────────────────────────


def test_close_unsubscribes_from_session(qtbot, tmp_path):
    """closeEvent must unsubscribe so the dialog never leaks a
    callback past its lifetime."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(2)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    assert dlg._bin_unsubscribe is not None

    dlg.close()
    assert dlg._bin_unsubscribe is None


def test_accept_unsubscribes_from_session(qtbot, tmp_path):
    """accept() path also unsubscribes."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(2)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    dlg.accept()
    assert dlg._bin_unsubscribe is None


def test_reject_unsubscribes_from_session(qtbot, tmp_path):
    """reject() path also unsubscribes."""
    store = _make_store(tmp_path)
    session = Session()
    session.set_active_bin(2)

    dlg = ExportImagesDialog(parent=None, store=store, session=session)
    qtbot.addWidget(dlg)
    dlg.reject()
    assert dlg._bin_unsubscribe is None


# ── Backward compat: existing accessors unchanged ────────────────────


def test_existing_accessors_unchanged(qtbot, tmp_path):
    """The new constructor kwarg doesn't break the channel/label/mask
    accessors that the launcher reads."""
    store = _make_store(tmp_path)
    dlg = ExportImagesDialog(parent=None, store=store)
    qtbot.addWidget(dlg)

    # Output-folder default is None until Browse is used.
    assert dlg.output_folder is None
    # Channels default to checked.
    assert len(dlg.selected_channels) == 2
    assert dlg.selected_labels == []
    assert dlg.selected_masks == []
