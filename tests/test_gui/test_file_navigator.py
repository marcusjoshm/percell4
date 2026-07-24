"""Tests for the FileNavigator (U3): toolbar navigation + emit chosen paths."""

from __future__ import annotations

import os

from percell4.interfaces.gui.task_panels import file_navigator as fnav_mod
from percell4.interfaces.gui.task_panels.file_navigator import FileNavigator

# ── Start / Up ──────────────────────────────────────────────


def test_starts_at_given_dir(qtbot, tmp_path):
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    assert os.path.samefile(nav._current, str(tmp_path))
    assert nav._path_edit.text()  # current path shown in the editable field


def test_up_navigates_to_parent(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(str(sub))
    qtbot.addWidget(nav)
    nav._up()
    assert os.path.samefile(nav._current, str(tmp_path))


# ── Back / Forward history ──────────────────────────────────


def test_back_and_forward_history(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    nav._navigate(str(sub))
    assert os.path.samefile(nav._current, str(sub))
    nav._back()
    assert os.path.samefile(nav._current, str(tmp_path))
    nav._forward()
    assert os.path.samefile(nav._current, str(sub))


def test_back_forward_button_states(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    assert not nav._back_btn.isEnabled()  # nothing before the start dir
    assert not nav._fwd_btn.isEnabled()
    nav._navigate(str(sub))
    assert nav._back_btn.isEnabled()
    assert not nav._fwd_btn.isEnabled()
    nav._back()
    assert not nav._back_btn.isEnabled()
    assert nav._fwd_btn.isEnabled()


def test_navigate_truncates_forward_history(qtbot, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    nav._navigate(str(a))
    nav._back()  # now at tmp_path with a forward entry (a)
    nav._navigate(str(b))  # branching drops the forward entry
    assert not nav._fwd_btn.isEnabled()
    nav._back()
    assert os.path.samefile(nav._current, str(tmp_path))


# ── Open (native dialog) ────────────────────────────────────


def test_browse_navigates_to_picked_dir(qtbot, tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.setattr(
        fnav_mod.QFileDialog, "getExistingDirectory", lambda *a, **k: str(sub)
    )
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    nav._browse()
    assert os.path.samefile(nav._current, str(sub))


def test_browse_cancel_is_noop(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        fnav_mod.QFileDialog, "getExistingDirectory", lambda *a, **k: ""
    )
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    nav._browse()
    assert os.path.samefile(nav._current, str(tmp_path))


# ── Go to dataset ───────────────────────────────────────────


def test_goto_dataset_navigates_and_button_enabled(qtbot, tmp_path):
    ds = tmp_path / "dataset_dir"
    ds.mkdir()
    nav = FileNavigator(str(tmp_path), get_dataset_dir=lambda: str(ds))
    qtbot.addWidget(nav)
    assert nav._dataset_btn.isEnabled()
    nav._goto_dataset()
    assert os.path.samefile(nav._current, str(ds))


def test_dataset_button_disabled_without_dataset(qtbot, tmp_path):
    nav = FileNavigator(str(tmp_path), get_dataset_dir=lambda: None)
    qtbot.addWidget(nav)
    assert not nav._dataset_btn.isEnabled()
    nav._goto_dataset()  # no-op, must not raise or move
    assert os.path.samefile(nav._current, str(tmp_path))


# ── Selection → path ────────────────────────────────────────


def test_double_click_file_emits_path(qtbot, tmp_path):
    f = tmp_path / "data.h5"
    f.write_text("x")
    nav = FileNavigator(str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._navigate(str(tmp_path))
    qtbot.waitUntil(lambda: nav._model.index(str(f)).isValid(), timeout=3000)
    idx = nav._model.index(str(f))
    with qtbot.waitSignal(nav.path_chosen, timeout=1000) as blocker:
        nav._on_double_click(idx)
    assert os.path.samefile(blocker.args[0], str(f))


def test_double_click_dir_does_not_reroot_or_emit(qtbot, tmp_path):
    # In the tree a folder expands in place; double-click must NOT change the
    # root or emit a path (root navigation is via the toolbar).
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    qtbot.waitUntil(lambda: nav._model.index(str(sub)).isValid(), timeout=3000)
    idx = nav._model.index(str(sub))
    fired: list[str] = []
    nav.path_chosen.connect(fired.append)
    nav._on_double_click(idx)
    assert os.path.samefile(nav._current, str(tmp_path))  # root unchanged
    assert fired == []  # folders don't emit a path


# ── Editable path field ─────────────────────────────────────


def test_path_field_navigates_to_typed_dir(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    nav._path_edit.setText(str(sub))
    nav._on_path_entered()
    assert os.path.samefile(nav._current, str(sub))


def test_path_field_file_navigates_to_parent(qtbot, tmp_path):
    f = tmp_path / "data.h5"
    f.write_text("x")
    nav = FileNavigator(str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._path_edit.setText(str(f))
    nav._on_path_entered()
    assert os.path.samefile(nav._current, str(tmp_path))


def test_path_field_unknown_restores_current(qtbot, tmp_path):
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    nav._path_edit.setText("/no/such/path/xyz123")
    nav._on_path_entered()
    assert os.path.samefile(nav._current, str(tmp_path))
    assert os.path.samefile(nav._path_edit.text(), str(tmp_path))


def test_dataset_button_uses_finger_glyph(qtbot, tmp_path):
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    assert nav._dataset_btn.text() == "☞"


def test_insert_emits_current_selection(qtbot, tmp_path):
    f = tmp_path / "data.h5"
    f.write_text("x")
    nav = FileNavigator(str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._navigate(str(tmp_path))
    qtbot.waitUntil(lambda: nav._model.index(str(f)).isValid(), timeout=3000)
    idx = nav._model.index(str(f))
    nav._view.setCurrentIndex(idx)
    with qtbot.waitSignal(nav.path_chosen, timeout=1000) as blocker:
        nav._on_insert()
    assert os.path.samefile(blocker.args[0], str(f))


def test_insert_noop_without_selection(qtbot, tmp_path):
    nav = FileNavigator(str(tmp_path))
    qtbot.addWidget(nav)
    fired: list[str] = []
    nav.path_chosen.connect(fired.append)
    nav._on_insert()  # no current selection → no emit, no raise
    assert fired == []
