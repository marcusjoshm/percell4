"""Tests for CommandLineEdit (history, command-name completion, drag-drop)."""

from __future__ import annotations

from qtpy.QtCore import Qt

from percell4.interfaces.gui.task_panels.command_line_edit import CommandLineEdit


def test_enter_emits_and_records_history(qtbot):
    edit = CommandLineEdit()
    qtbot.addWidget(edit)
    submitted: list[str] = []
    edit.command_submitted.connect(submitted.append)

    edit.setText("percell4-inspect exp.h5")
    qtbot.keyClick(edit, Qt.Key.Key_Return)

    assert submitted == ["percell4-inspect exp.h5"]
    assert edit.text() == ""  # field clears after submit


def test_empty_submit_ignored(qtbot):
    edit = CommandLineEdit()
    qtbot.addWidget(edit)
    submitted: list[str] = []
    edit.command_submitted.connect(submitted.append)

    edit.setText("   ")
    qtbot.keyClick(edit, Qt.Key.Key_Return)

    assert submitted == []


def test_up_down_history_recall(qtbot):
    edit = CommandLineEdit()
    qtbot.addWidget(edit)
    for cmd in ("percell4-inspect a.h5", "percell4-batch-export b"):
        edit.setText(cmd)
        qtbot.keyClick(edit, Qt.Key.Key_Return)

    qtbot.keyClick(edit, Qt.Key.Key_Up)
    assert edit.text() == "percell4-batch-export b"
    qtbot.keyClick(edit, Qt.Key.Key_Up)
    assert edit.text() == "percell4-inspect a.h5"
    qtbot.keyClick(edit, Qt.Key.Key_Down)
    assert edit.text() == "percell4-batch-export b"
    qtbot.keyClick(edit, Qt.Key.Key_Down)
    assert edit.text() == ""  # past the newest → empty prompt


def test_insert_command_seeds_field(qtbot):
    edit = CommandLineEdit()
    qtbot.addWidget(edit)
    edit.insert_command("percell4-batch-export")
    assert edit.text() == "percell4-batch-export "


def test_tab_completes_unique_command_name(qtbot):
    edit = CommandLineEdit(
        completions=["percell4-batch-export", "percell4-inspect"]
    )
    qtbot.addWidget(edit)
    edit.setText("percell4-i")
    qtbot.keyClick(edit, Qt.Key.Key_Tab)
    assert edit.text() == "percell4-inspect "


def test_tab_no_completion_for_second_token(qtbot):
    edit = CommandLineEdit(completions=["percell4-batch-export"])
    qtbot.addWidget(edit)
    edit.setText("percell4-batch-export ./da")
    qtbot.keyClick(edit, Qt.Key.Key_Tab)
    # unchanged — path completion is out of scope in v1
    assert edit.text() == "percell4-batch-export ./da"


def test_insert_paths_quotes_spaces(qtbot):
    edit = CommandLineEdit()
    qtbot.addWidget(edit)
    edit.setText("percell4-batch-export ")
    edit.end(False)
    edit.insert_paths(["/data/a b/exp.h5"])
    assert edit.text() == "percell4-batch-export '/data/a b/exp.h5' "
