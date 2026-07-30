"""Tests for ``percell4.gui._dialog_utils`` helpers."""

from __future__ import annotations

import sys

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog, QLabel, QScrollArea, QWidget

from percell4.gui._dialog_utils import (
    cap_to_screen,
    center_on_screen,
    detach_window,
    wrap_in_scroll,
)


def _force_linux(monkeypatch) -> None:
    """Take the Linux branch regardless of the host running the suite.

    Both helpers read ``sys.platform`` at call time precisely so this
    works: CI is Linux-only, so an import-time gate would leave every
    ``_force_other_platform`` case below silently unexecuted.
    """
    monkeypatch.setattr(sys, "platform", "linux")


def _force_other_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

# ── wrap_in_scroll ───────────────────────────────────────────────────


def test_wrap_in_scroll_returns_scroll_area_holding_content(qtbot):
    content = QWidget()
    qtbot.addWidget(content)
    label = QLabel("hello", parent=content)

    scroll = wrap_in_scroll(content)

    assert isinstance(scroll, QScrollArea)
    assert scroll.widget() is content
    assert label.parent() is content


def test_wrap_in_scroll_sets_widget_resizable(qtbot):
    content = QWidget()
    qtbot.addWidget(content)

    scroll = wrap_in_scroll(content)

    assert scroll.widgetResizable() is True


def test_wrap_in_scroll_uses_no_frame(qtbot):
    content = QWidget()
    qtbot.addWidget(content)

    scroll = wrap_in_scroll(content)

    assert scroll.frameShape() == QScrollArea.NoFrame


# ── cap_to_screen ────────────────────────────────────────────────────


def test_cap_to_screen_default_fraction_caps_to_90_percent(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)

    cap_to_screen(dialog)

    screen_geom = parent.screen().availableGeometry()
    assert dialog.maximumHeight() == int(screen_geom.height() * 0.9)
    assert dialog.maximumWidth() == int(screen_geom.width() * 0.9)


def test_cap_to_screen_custom_fraction(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)

    cap_to_screen(dialog, fraction=0.5)

    screen_geom = parent.screen().availableGeometry()
    assert dialog.maximumHeight() == int(screen_geom.height() * 0.5)
    assert dialog.maximumWidth() == int(screen_geom.width() * 0.5)


def test_cap_to_screen_no_parent_does_not_raise(qtbot):
    dialog = QDialog()
    qtbot.addWidget(dialog)
    default_max_height = dialog.maximumHeight()

    cap_to_screen(dialog)

    assert dialog.maximumHeight() == default_max_height


def test_cap_to_screen_parent_without_screen_attr_does_not_raise(qtbot):
    class _Bare:
        pass

    dialog = QDialog()
    qtbot.addWidget(dialog)
    default_max_height = dialog.maximumHeight()
    dialog.setParent(None)

    cap_to_screen(dialog)

    assert dialog.maximumHeight() == default_max_height


def test_cap_to_screen_swallows_screen_exception(qtbot, monkeypatch):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    default_max_height = dialog.maximumHeight()

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated screen failure")

    monkeypatch.setattr(parent, "screen", _raise)

    cap_to_screen(dialog)

    assert dialog.maximumHeight() == default_max_height


# ── detach_window ────────────────────────────────────────────────────


def test_detach_window_sets_tool_window_type(qtbot, monkeypatch):
    """The whole point: UTILITY type fails mutter's attach gate.

    See ``docs/solutions/ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md``.
    """
    _force_linux(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    assert dialog.windowType() == Qt.Dialog

    detach_window(dialog)

    assert dialog.windowType() == Qt.Tool


def test_detach_window_preserves_decoration_hints(qtbot, monkeypatch):
    """R7 -- the popup must keep its title bar and close button."""
    _force_linux(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)

    detach_window(dialog)

    flags = dialog.windowFlags()
    assert flags & Qt.WindowTitleHint
    assert flags & Qt.WindowSystemMenuHint
    assert flags & Qt.WindowCloseButtonHint


def test_detach_window_preserves_unrelated_flags(qtbot, monkeypatch):
    """Only the type nibble changes; other hints survive."""
    _force_linux(monkeypatch)
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)

    detach_window(dialog)

    assert dialog.windowFlags() & Qt.WindowStaysOnTopHint
    assert dialog.windowType() == Qt.Tool


def test_setwindowflag_window_is_a_noop_on_qdialog_but_detach_is_not(
    qtbot, monkeypatch
):
    """Regression guard for the trap that makes the naive fix look correct.

    ``Qt.Dialog`` already contains the ``Qt.Window`` bit, so
    ``setWindowFlag(Qt.Window, True)`` is byte-identical -- a test that
    only checked "some flag changed" would pass against a broken
    implementation.
    """
    _force_linux(monkeypatch)
    naive = QDialog()
    qtbot.addWidget(naive)
    before = naive.windowFlags()
    naive.setWindowFlag(Qt.Window, True)
    assert naive.windowFlags() == before, "expected the naive call to be a no-op"

    real = QDialog()
    qtbot.addWidget(real)
    real_before = real.windowFlags()
    detach_window(real)
    assert real.windowFlags() != real_before


def test_detach_window_is_noop_off_linux(qtbot, monkeypatch):
    """R6 -- Windows and macOS keep the behaviour the researcher reports fine.

    Qt.Tool would also change appearance there (macOS hides tool windows
    when the app deactivates), so the gate is load-bearing, not cosmetic.
    """
    _force_other_platform(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    before = dialog.windowFlags()

    detach_window(dialog)

    assert dialog.windowFlags() == before
    assert dialog.windowType() == Qt.Dialog


# ── center_on_screen ─────────────────────────────────────────────────


def test_center_on_screen_centers_in_available_geometry(qtbot, monkeypatch):
    _force_linux(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    dialog.resize(200, 150)

    center_on_screen(dialog)

    avail = parent.screen().availableGeometry()
    got = dialog.frameGeometry().center()
    # Offscreen synthesises a 2px frame per side; macOS ~28px.
    assert abs(got.x() - avail.center().x()) <= 5
    assert abs(got.y() - avail.center().y()) <= 5


def test_center_on_screen_keeps_popup_inside_work_area(qtbot, monkeypatch):
    """AE1 says "fully on screen" -- a popup taller than the work area
    must not get a title bar above the top edge, which would reproduce
    the very symptom this change fixes."""
    _force_linux(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    avail = parent.screen().availableGeometry()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    dialog.resize(avail.width() + 400, avail.height() + 400)

    center_on_screen(dialog)

    top_left = dialog.frameGeometry().topLeft()
    assert top_left.x() >= avail.x()
    assert top_left.y() >= avail.y()


def test_center_on_screen_position_survives_show(qtbot, monkeypatch):
    """move() before show() sets WA_Moved, which suppresses
    QDialog.adjustPosition -- that is what makes deliberate placement
    stick instead of Qt re-centring on the parent."""
    _force_linux(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    dialog.resize(200, 150)

    center_on_screen(dialog)
    placed = dialog.pos()
    dialog.show()

    assert abs(dialog.pos().x() - placed.x()) <= 5
    assert abs(dialog.pos().y() - placed.y()) <= 5


def test_center_on_screen_no_parent_does_not_raise(qtbot, monkeypatch):
    _force_linux(monkeypatch)
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.resize(200, 150)

    center_on_screen(dialog)

    assert dialog.screen() is not None


def test_center_on_screen_parent_without_screen_attr_does_not_raise(
    qtbot, monkeypatch
):
    _force_linux(monkeypatch)
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.setParent(None)

    center_on_screen(dialog)


def test_center_on_screen_swallows_screen_exception(qtbot, monkeypatch):
    _force_linux(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    before = dialog.pos()

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated screen failure")

    monkeypatch.setattr(parent, "screen", _raise)
    monkeypatch.setattr(dialog, "screen", _raise)

    center_on_screen(dialog)

    assert dialog.pos() == before


def test_center_on_screen_is_noop_off_linux(qtbot, monkeypatch):
    _force_other_platform(monkeypatch)
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    dialog = QDialog(parent)
    qtbot.addWidget(dialog)
    dialog.move(11, 22)

    center_on_screen(dialog)

    assert dialog.pos().x() == 11
    assert dialog.pos().y() == 22
