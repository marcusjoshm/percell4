"""U6 tests: the dataset description editor dialog.

The dialog performs no file I/O -- it returns the user's decision and the
caller owns the write. Clearing a non-empty description confirms first,
matching how the Data tab guards its layer deletes.
"""

from __future__ import annotations

import pytest
from qtpy.QtWidgets import QMessageBox, QScrollArea

from percell4.gui import description_dialog as mod
from percell4.gui.description_dialog import DescriptionDialog, DescriptionResult


@pytest.fixture
def dialog(qtbot):
    def _make(description=None):
        dlg = DescriptionDialog(description=description)
        qtbot.addWidget(dlg)
        return dlg
    return _make


# ── Prefill ───────────────────────────────────────────────────


def test_opens_prefilled_with_the_current_description(dialog):
    dlg = dialog("HeLa p14, fixed 4% PFA 15min")
    assert dlg._editor.toPlainText() == "HeLa p14, fixed 4% PFA 15min"


def test_opens_empty_when_there_is_no_description(dialog):
    assert dialog(None)._editor.toPlainText() == ""


# ── Outcomes ──────────────────────────────────────────────────


def test_saving_returns_the_edited_text_with_line_breaks(dialog):
    dlg = dialog("old")
    dlg._editor.setPlainText("line one\nline two\n\nline four")
    dlg._on_save()
    result = dlg.result_value()
    assert result.accepted is True
    assert result.clear is False
    assert result.text == "line one\nline two\n\nline four"


def test_cancelled_by_default_until_a_choice_is_made(dialog):
    result = dialog("something").result_value()
    assert result.accepted is False
    assert result.clear is False
    assert result.text is None


def test_clear_returns_an_explicit_clear_not_empty_text(dialog, monkeypatch):
    """A clear and a save-of-empty-text mean different things to the caller."""
    monkeypatch.setattr(mod, "message_box", lambda *a, **k: QMessageBox.Yes)
    dlg = dialog("HeLa p14")
    dlg._on_clear()
    result = dlg.result_value()
    assert result.accepted is True
    assert result.clear is True
    assert result.text is None


# ── Confirm before clearing ───────────────────────────────────


def test_clear_asks_before_destroying_a_non_empty_description(dialog, monkeypatch):
    asked: list[str] = []

    def _spy(_parent, title, text, **kwargs):
        asked.append(text)
        return QMessageBox.Yes

    monkeypatch.setattr(mod, "message_box", _spy)
    dlg = dialog("notes worth keeping")
    dlg._on_clear()
    assert len(asked) == 1
    assert "cannot be undone" in asked[0]


def test_declining_the_clear_confirmation_keeps_the_text(dialog, monkeypatch):
    monkeypatch.setattr(mod, "message_box", lambda *a, **k: QMessageBox.No)
    dlg = dialog("notes worth keeping")
    dlg._on_clear()
    assert dlg._editor.toPlainText() == "notes worth keeping"
    # Still cancelled: declining the confirmation must not record a clear.
    assert dlg.result_value() == DescriptionResult.cancelled()


def test_clear_on_an_empty_description_does_not_ask(dialog, monkeypatch):
    """Nothing to lose, so the prompt would be friction with no risk."""
    asked: list[str] = []
    monkeypatch.setattr(
        mod, "message_box",
        lambda *a, **k: asked.append("asked") or QMessageBox.Yes,
    )
    dlg = dialog("   \n  ")
    dlg._on_clear()
    assert asked == []
    assert dlg.result_value().clear is True


# ── Conventions ───────────────────────────────────────────────


def test_content_is_wrapped_in_exactly_one_scroll_area(dialog):
    """docs/solutions/ui-bugs/dialog-scroll-when-tall.md convention."""
    dlg = dialog("text")
    scrolls = dlg.findChildren(QScrollArea)
    assert len(scrolls) == 1
    assert scrolls[0].widget() is not None


def test_dialog_performs_no_file_io(dialog):
    """No store, no path, no h5py -- the caller owns the write."""
    source = mod.__file__
    with open(source) as f:
        text = f.read()
    assert "h5py" not in text
    assert "DatasetStore" not in text


# ── Result helpers ────────────────────────────────────────────


def test_result_constructors_are_distinct():
    assert DescriptionResult.saved("x") == DescriptionResult(True, False, "x")
    assert DescriptionResult.cleared() == DescriptionResult(True, True, None)
    assert DescriptionResult.cancelled() == DescriptionResult(False, False, None)
