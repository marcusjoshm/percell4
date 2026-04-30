"""Shared helpers for Qt dialogs.

Convention defined in ``docs/solutions/ui-bugs/dialog-scroll-when-tall.md``:
any ``QDialog`` that can grow taller than the user's screen wraps its
primary content in a borderless ``QScrollArea`` and caps its maximum size
to a fraction of the available screen geometry.
"""

from __future__ import annotations

from qtpy.QtWidgets import QDialog, QScrollArea, QWidget


def wrap_in_scroll(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(content)
    return scroll


def cap_to_screen(dialog: QDialog, fraction: float = 0.9) -> None:
    parent = dialog.parent()
    if parent is None or not hasattr(parent, "screen"):
        return
    try:
        screen_geom = parent.screen().availableGeometry()
        dialog.setMaximumHeight(int(screen_geom.height() * fraction))
        dialog.setMaximumWidth(int(screen_geom.width() * fraction))
    except Exception:  # noqa: BLE001
        pass
