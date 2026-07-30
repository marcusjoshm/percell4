"""Shared helpers for Qt dialogs.

Two conventions live here, each with an owning doc under
``docs/solutions/ui-bugs/``:

``dialog-scroll-when-tall.md``
    Any ``QDialog`` that can grow taller than the user's screen wraps its
    primary content in a borderless ``QScrollArea`` and caps its maximum
    size to a fraction of the available screen geometry.

``gnome-attaches-parented-modal-dialogs-2026-07-29.md``
    Every parented popup takes a non-attaching window type and opens
    centred on the work area, so GNOME stops gluing it to the launcher.
"""

from __future__ import annotations

import sys

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDialog,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
    QScrollArea,
    QWidget,
)


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


def _on_linux() -> bool:
    """Whether the window-manager workarounds below apply.

    Read at call time rather than captured at import, so a test can
    exercise both branches. CI is Linux-only; an import-time constant
    would leave every off-Linux path here permanently unexecuted.
    """
    return sys.platform.startswith("linux")


def _screen_for(popup: QWidget):
    """The screen to place ``popup`` on, or ``None`` if undeterminable.

    Prefers the parent's screen so a popup lands on the same monitor as
    the window that raised it, and falls back to the popup's own.
    """
    parent = popup.parent()
    if parent is not None and hasattr(parent, "screen"):
        try:
            screen = parent.screen()
        except Exception:  # noqa: BLE001
            screen = None
        if screen is not None:
            return screen
    try:
        return popup.screen()
    except Exception:  # noqa: BLE001
        return None


def detach_window(popup: QWidget) -> None:
    """Give ``popup`` a window type GNOME will not glue to its parent.

    mutter attaches a popup to its parent when all three of these hold:
    the ``attach-modal-dialogs`` preference is on, the window is a DIALOG
    type carrying ``_NET_WM_STATE_MODAL``, and its ``WM_TRANSIENT_FOR``
    parent is itself NORMAL. While attached, mutter rewrites the position
    on every constraint pass, so the application's own ``move()`` is
    silently discarded.

    ``Qt.Tool`` maps to ``_NET_WM_WINDOW_TYPE_UTILITY``, which fails the
    second condition while staying in qtbase's ``isTransient()`` list. The
    popup therefore keeps modality, keeps its stay-above-parent
    relationship, and gains no separate taskbar entry. ``Qt.Window`` also
    detaches, but it deletes ``WM_TRANSIENT_FOR`` and no call to
    ``setTransientParent()`` restores it.

    Because the parent's type is one of the three conditions, converting a
    dialog also frees every popup parented to *it* -- with no edit to
    those call sites.

    Call before the first ``show()``: ``setWindowFlags`` hides an already
    visible widget as a side effect (see
    ``qt-setwindowflag-hides-visible-widget-2026-05-14.md``).

    ``setWindowFlag(Qt.Window, True)`` is not a substitute -- on a
    ``QDialog`` it is a measured no-op, because ``Qt.Dialog`` already
    contains the ``Qt.Window`` bit. The type nibble must be replaced.

    A no-op off Linux: Windows has no compositor-side attachment, and on
    macOS a tool window hides whenever the application deactivates.
    """
    if not _on_linux():
        return
    flags = popup.windowFlags()
    popup.setWindowFlags((flags & ~Qt.WindowType_Mask) | Qt.Tool)


def center_on_screen(popup: QWidget) -> None:
    """Place ``popup`` at the centre of its screen's available work area.

    Placement is deliberate rather than inherited. Qt would otherwise
    centre a dialog over its parent, which puts it half off-screen
    whenever the parent is docked against a screen edge. ``move()`` sets
    ``Qt.WA_Moved``, which suppresses ``QDialog.adjustPosition``'s
    re-centring -- so this must also run before the first ``show()``.

    Clamped to the work area so a popup larger than the screen keeps its
    title bar reachable instead of losing it above the top edge.

    Horizontally exact; vertically biased low by half the title-bar
    height (~18px under GNOME). Before a window is mapped its
    ``frameGeometry`` equals its ``geometry`` -- the frame is the window
    manager's decision and is unknowable until show -- so the centring
    maths cannot account for decorations it cannot yet measure. Correcting
    it would mean re-centring after show, which costs a visible jump for
    under 2% of a 1080px screen. Measured, accepted, not a bug.

    A no-op off Linux, and inert on native Wayland by construction:
    xdg-shell gives clients no absolute-positioning request at all.
    """
    if not _on_linux():
        return
    screen = _screen_for(popup)
    if screen is None:
        return
    available = screen.availableGeometry()
    frame = popup.frameGeometry()
    frame.moveCenter(available.center())
    top_left = frame.topLeft()
    top_left.setX(max(top_left.x(), available.x()))
    top_left.setY(max(top_left.y(), available.y()))
    popup.move(top_left)


def message_box(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    icon=None,
    buttons=None,
    default_button=None,
) -> int:
    """A ``QMessageBox`` that is freestanding and centred, then shown modally.

    Mirrors the return contract of ``QMessageBox.warning`` and friends --
    the clicked ``StandardButton`` -- so a call site that branches on the
    answer keeps working unchanged.

    The static convenience methods build, show, and destroy the box in one
    call, leaving no handle on which to set window flags before the first
    show. That is the whole reason this wrapper exists.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    if icon is not None:
        box.setIcon(icon)
    if buttons is not None:
        box.setStandardButtons(buttons)
    if default_button is not None:
        box.setDefaultButton(default_button)
    # Size before centring: a QMessageBox has no useful geometry until its
    # text and buttons are laid out.
    box.adjustSize()
    detach_window(box)
    center_on_screen(box)
    return box.exec_()


def progress_dialog(
    parent: QWidget | None,
    label: str,
    cancel_text: str | None,
    minimum: int,
    maximum: int,
    *,
    modality,
) -> QProgressDialog:
    """A freestanding, centred ``QProgressDialog``, not yet shown.

    ``modality`` is required rather than defaulted: the launcher's two
    progress dialogs genuinely differ -- compression is window-modal while
    dataset loading is application-modal -- so a single default would
    silently downgrade one of them and break the promise that this change
    leaves modality alone.
    """
    dialog = QProgressDialog(label, cancel_text, minimum, maximum, parent)
    dialog.setWindowModality(modality)
    dialog.adjustSize()
    detach_window(dialog)
    center_on_screen(dialog)
    return dialog


def text_input(
    parent: QWidget | None,
    title: str,
    label: str,
    *,
    text: str = "",
) -> tuple[str, bool]:
    """A freestanding, centred text prompt shown modally.

    Returns the same ``(value, accepted)`` tuple as
    ``QInputDialog.getText`` so call sites unpack unchanged.
    """
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.adjustSize()
    detach_window(dialog)
    center_on_screen(dialog)
    accepted = dialog.exec_() == QDialog.Accepted
    return dialog.textValue(), accepted
