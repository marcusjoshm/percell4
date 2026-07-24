"""Tests for the FileNavigator (U3): browse directories, emit chosen paths."""

from __future__ import annotations

import os

import pytest
from qtpy.QtCore import QDir
from qtpy.QtWidgets import QPushButton

from percell4.interfaces.gui.task_panels.file_navigator import (
    FileNavigator,
    _drive_label,
)


def test_starts_at_given_dir(qtbot, tmp_path):
    nav = FileNavigator(start_dir=str(tmp_path))
    qtbot.addWidget(nav)
    assert os.path.samefile(nav._current, str(tmp_path))


def test_up_navigates_to_parent(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(start_dir=str(sub))
    qtbot.addWidget(nav)
    nav._on_up()
    assert os.path.samefile(nav._current, str(tmp_path))


def test_up_at_root_is_noop(qtbot):
    nav = FileNavigator(start_dir="/")
    qtbot.addWidget(nav)
    nav._on_up()
    assert nav._current == "/"


def test_double_click_file_emits_path(qtbot, tmp_path):
    f = tmp_path / "data.h5"
    f.write_text("x")
    nav = FileNavigator(start_dir=str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._set_root(str(tmp_path))
    qtbot.waitUntil(lambda: nav._model.index(str(f)).isValid(), timeout=3000)
    idx = nav._model.index(str(f))
    with qtbot.waitSignal(nav.path_chosen, timeout=1000) as blocker:
        nav._on_double_click(idx)
    assert os.path.samefile(blocker.args[0], str(f))


def test_double_click_dir_descends(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(start_dir=str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._set_root(str(tmp_path))
    qtbot.waitUntil(lambda: nav._model.index(str(sub)).isValid(), timeout=3000)
    idx = nav._model.index(str(sub))
    nav._on_double_click(idx)
    assert os.path.samefile(nav._current, str(sub))


def test_insert_emits_current_selection(qtbot, tmp_path):
    f = tmp_path / "data.h5"
    f.write_text("x")
    nav = FileNavigator(start_dir=str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._set_root(str(tmp_path))
    qtbot.waitUntil(lambda: nav._model.index(str(f)).isValid(), timeout=3000)
    idx = nav._model.index(str(f))
    nav._view.setCurrentIndex(idx)
    with qtbot.waitSignal(nav.path_chosen, timeout=1000) as blocker:
        nav._on_insert()
    assert os.path.samefile(blocker.args[0], str(f))


def test_insert_noop_without_selection(qtbot, tmp_path):
    nav = FileNavigator(start_dir=str(tmp_path))
    qtbot.addWidget(nav)
    fired: list[str] = []
    nav.path_chosen.connect(fired.append)
    nav._on_insert()  # no current selection → no emit, no raise
    assert fired == []


# ── Path bar (jump anywhere, incl. /Volumes) ────────────────


def test_path_bar_navigates_to_typed_dir(qtbot, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    nav = FileNavigator(start_dir=str(tmp_path))
    qtbot.addWidget(nav)
    nav._path_edit.setText(str(sub))
    nav._on_path_entered()
    assert os.path.samefile(nav._current, str(sub))


def test_path_bar_file_navigates_to_parent(qtbot, tmp_path):
    f = tmp_path / "data.h5"
    f.write_text("x")
    nav = FileNavigator(start_dir=str(tmp_path.parent))
    qtbot.addWidget(nav)
    nav._path_edit.setText(str(f))
    nav._on_path_entered()
    assert os.path.samefile(nav._current, str(tmp_path))


def test_path_bar_unknown_path_restores_current(qtbot, tmp_path):
    nav = FileNavigator(start_dir=str(tmp_path))
    qtbot.addWidget(nav)
    nav._path_edit.setText("/no/such/path/xyz123")
    nav._on_path_entered()
    assert os.path.samefile(nav._current, str(tmp_path))
    assert os.path.samefile(nav._path_edit.text(), str(tmp_path))


# ── Cross-platform quick jumps ──────────────────────────────


@pytest.mark.parametrize(
    ("path", "label"),
    [("C:/", "C:"), ("C:\\", "C:"), ("D:/", "D:"), ("Z:/", "Z:"), ("/", "/")],
)
def test_drive_label(path, label):
    assert _drive_label(path) == label


def test_quick_jumps_include_home_and_every_drive(qtbot, tmp_path):
    nav = FileNavigator(start_dir=str(tmp_path))
    qtbot.addWidget(nav)
    labels = {b.text() for b in nav.findChildren(QPushButton)}
    assert "Home" in labels
    # Every drive root reported by Qt (drive letters on Windows, "/" on Unix)
    # gets its own quick-jump button — the PC equivalent of macOS /Volumes.
    for drive in QDir.drives():
        assert _drive_label(drive.absoluteFilePath()) in labels


def test_home_quick_button_navigates(qtbot, tmp_path):
    nav = FileNavigator(start_dir=str(tmp_path))
    qtbot.addWidget(nav)
    home_btn = next(
        b for b in nav.findChildren(QPushButton) if b.text() == "Home"
    )
    home_btn.click()
    assert os.path.samefile(nav._current, QDir.homePath())
