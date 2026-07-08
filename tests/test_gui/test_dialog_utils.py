"""Tests for ``percell4.gui._dialog_utils`` helpers."""

from __future__ import annotations

from qtpy.QtWidgets import QDialog, QLabel, QScrollArea, QWidget

from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll

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
