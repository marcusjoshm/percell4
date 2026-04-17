"""Modal multi-label selection tool for the napari viewer.

User clicks a toolbar action (or presses M) on the launcher; a small
dock window opens parented to the viewer. Each left-click on a label in
the napari labels layer toggles that label in a staged set. Staged
cells render in cyan via a dedicated overlay Labels layer
(`_multi_select_staged`) — the existing `_update_label_display`
colormap builder is untouched. Ctrl+Return (or Accept) commits staging
via `Session.set_selection(frozenset)`; Esc (or Cancel) discards.

Architectural notes:

- Staging state (`StagingBuffer`) is pure Python and has no Qt imports
  so it's trivially testable.
- The Controller depends on two narrow :class:`Protocol`s
  (:class:`StagedRenderer`, :class:`SelectionSink`) so tests can
  substitute minimal fakes.
- Tool-exclusion is handled by the existing
  :meth:`LauncherWindow.set_workflow_locked` coordination primitive.
  No new `_active_tool` flag.
- While the tool is active we suspend
  `ViewerWindow._on_label_selected` forwarding to avoid a click in
  flight wiping the prior selection (which we pre-fill staging from)
  during the `layer.mode = "pan_zoom"` switch.
- Coalesced visual refresh uses a single
  ``QTimer(setSingleShot=True).start(0)`` — Qt coalesces naturally.
- Teardown is strict: set ``_torn_down`` flag, stop timer, remove
  callback, restore mode, remove overlay, resume forwarding, release
  workflow lock.

See ``docs/plans/2026-04-17-feat-napari-multi-label-selection-plan.md``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol

from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme

logger = logging.getLogger(__name__)

type LabelId = int

_PAN_ZOOM: Final = "pan_zoom"
_STAGED_COLOR: Final = (0.0, 0.9, 0.9, 0.6)  # cyan 0.6α
_OVERLAY_LAYER_NAME: Final = "_multi_select_staged"


class StagedRenderer(Protocol):
    """Minimal surface the controller needs from the viewer."""

    def add_staged_overlay(self, staged_ids: frozenset[LabelId]) -> None: ...

    def update_staged_overlay(self, staged_ids: frozenset[LabelId]) -> None: ...

    def remove_staged_overlay(self) -> None: ...

    def suspend_selected_label_forwarding(self) -> None: ...

    def resume_selected_label_forwarding(self) -> None: ...

    def is_viewer_alive(self) -> bool: ...

    def active_labels_layer_or_none(self): ...


class SelectionSink(Protocol):
    """Minimal surface the controller needs from the data model."""

    @property
    def selected_ids(self) -> list[LabelId]: ...

    def set_selection(self, label_ids: list[LabelId]) -> None: ...


class ToolLock(Protocol):
    """Minimal surface for the workflow-lock primitive on LauncherWindow."""

    @property
    def is_workflow_locked(self) -> bool: ...

    def set_workflow_locked(self, locked: bool) -> None: ...


@dataclass
class StagingBuffer:
    """Pure-Python staging state for the multi-select tool.

    The buffer starts pre-filled from the current selection so the tool
    can also be used to refine an existing selection. Toggle behavior is
    symmetric: clicking an already-staged label removes it.
    """

    initial_ids: frozenset[LabelId]
    current: set[LabelId] = field(default_factory=set)

    def __post_init__(self) -> None:
        # Initialize current from initial_ids without sharing state.
        self.current = set(self.initial_ids)

    def toggle(self, label_id: LabelId) -> None:
        if label_id in self.current:
            self.current.remove(label_id)
        else:
            self.current.add(label_id)

    def snapshot(self) -> frozenset[LabelId]:
        return frozenset(self.current)

    def is_dirty(self) -> bool:
        """Whether the current buffer differs from the pre-fill."""
        return frozenset(self.current) != self.initial_ids


class MultiLabelSelectController:
    """Modal multi-label selection tool.

    Owns a staging buffer, a small dock window, and the mouse callback
    installed on the active napari labels layer. The class is designed
    to be torn down exactly once — re-showing the tool constructs a
    fresh controller.
    """

    def __init__(
        self,
        viewer_win: StagedRenderer,
        data_model: SelectionSink,
        launcher: ToolLock,
    ) -> None:
        self._viewer_win = viewer_win
        self._data_model = data_model
        self._launcher = launcher
        self._buffer = StagingBuffer(
            initial_ids=frozenset(data_model.selected_ids)
        )
        self._prior_mode: str | None = None
        self._mouse_cb: Callable | None = None
        self._layer = None  # Retained to support teardown even if viewer dies
        self._torn_down: bool = False
        self._window: QMainWindow | None = None
        self._counter_label: QLabel | None = None
        self._accept_button: QPushButton | None = None
        self._refresh_timer: QTimer | None = None

    # ── Public lifecycle ─────────────────────────────────────────

    def show(self) -> bool:
        """Open the dock window and enter the tool mode.

        Returns False if the tool could not be installed (no labels
        layer, viewer not alive, already locked). The caller should
        surface a status message in that case.
        """
        if self._launcher.is_workflow_locked:
            logger.warning("multi-select: cannot open while workflow is locked")
            return False
        if not self._viewer_win.is_viewer_alive():
            logger.warning("multi-select: viewer is not alive")
            return False
        if self._viewer_win.active_labels_layer_or_none() is None:
            logger.warning("multi-select: no active labels layer")
            return False

        self._build_window()
        self._install()
        assert self._window is not None
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        self._refresh_dock()
        return True

    def toggle(self, label_id: LabelId) -> None:
        """Toggle a label in the staging buffer and schedule a refresh."""
        if self._torn_down:
            return
        self._buffer.toggle(label_id)
        self._schedule_refresh()

    def accept(self) -> None:
        """Commit the staged buffer as the app's selection."""
        if self._torn_down:
            return
        snap = self._buffer.snapshot()
        self._uninstall()
        # Domain state moves here — the one and only place.
        self._data_model.set_selection(list(snap))

    def cancel(self) -> None:
        """Discard the staged buffer."""
        if self._torn_down:
            return
        self._uninstall()

    # ── Install / uninstall ─────────────────────────────────────

    def _install(self) -> None:
        """Enter tool mode. Order matters — see plan critical race notes."""
        layer = self._viewer_win.active_labels_layer_or_none()
        assert layer is not None  # Guarded in show()
        self._layer = layer

        # 1. Silence the viewer's selected_label → CellDataModel path for
        #    the tool's lifetime so a click-in-flight can't wipe the
        #    prior selection we just pre-filled staging from.
        self._viewer_win.suspend_selected_label_forwarding()

        # 2. Save and force mode. pan_zoom makes napari's built-in pick
        #    a no_op (napari/layers/base/base.py _drag_modes) while
        #    appended callbacks still run.
        self._prior_mode = str(layer.mode)
        layer.mode = _PAN_ZOOM

        # 3. Add the staging overlay layer.
        self._viewer_win.add_staged_overlay(self._buffer.snapshot())

        # 4. Append the click callback.
        self._mouse_cb = self._make_click_callback()
        layer.mouse_drag_callbacks.append(self._mouse_cb)

        # 5. Acquire the workflow lock.
        self._launcher.set_workflow_locked(True)

    def _uninstall(self) -> None:
        """Exit tool mode. Idempotent; tolerant of a torn-down viewer."""
        if self._torn_down:
            return
        self._torn_down = True

        # Cancel pending refresh before touching any renderer state.
        if self._refresh_timer is not None:
            self._refresh_timer.stop()

        # Remove the mouse callback if the layer is still alive.
        try:
            layer = self._viewer_win.active_labels_layer_or_none()
        except Exception:  # noqa: BLE001 — viewer can die in surprising ways
            layer = None
        if layer is None:
            layer = self._layer  # Fall back to the layer captured at _install
        if layer is not None and self._mouse_cb is not None:
            with contextlib.suppress(ValueError):
                layer.mouse_drag_callbacks.remove(self._mouse_cb)
            if self._prior_mode is not None:
                with contextlib.suppress(Exception):
                    layer.mode = self._prior_mode

        # Remove the staging overlay. Idempotent on the viewer side.
        with contextlib.suppress(Exception):
            self._viewer_win.remove_staged_overlay()

        # Restore the selected_label forwarding.
        with contextlib.suppress(Exception):
            self._viewer_win.resume_selected_label_forwarding()

        # Release the workflow lock.
        with contextlib.suppress(Exception):
            self._launcher.set_workflow_locked(False)

        # Close the dock.
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.close()

    # ── Refresh coalescing ──────────────────────────────────────

    def _schedule_refresh(self) -> None:
        if self._torn_down or self._refresh_timer is None:
            return
        # Restarting a single-shot timer while it's running is idempotent —
        # one fire per event-loop iteration.
        self._refresh_timer.start(0)

    def _do_refresh(self) -> None:
        if self._torn_down:
            return
        if not self._viewer_win.is_viewer_alive():
            return
        snap = self._buffer.snapshot()
        with contextlib.suppress(Exception):
            self._viewer_win.update_staged_overlay(snap)
        self._refresh_dock()

    def _refresh_dock(self) -> None:
        if self._counter_label is not None:
            n = len(self._buffer.current)
            self._counter_label.setText(f"{n} cell{'s' if n != 1 else ''} staged")
        if self._accept_button is not None:
            self._accept_button.setEnabled(self._buffer.is_dirty())

    # ── Click callback ──────────────────────────────────────────

    def _make_click_callback(self) -> Callable:
        def _on_click(layer_, event) -> None:
            if self._torn_down:
                return
            # Left click only — middle/right/Alt+drag panning is untouched.
            if event.button != 1:
                return
            try:
                value = layer_.get_value(
                    event.position,
                    view_direction=event.view_direction,
                    dims_displayed=event.dims_displayed,
                    world=True,
                )
            except Exception:  # noqa: BLE001
                return
            if value is None:
                return
            try:
                label_id = int(value)
            except (TypeError, ValueError):
                return
            if label_id == 0:  # background
                return
            self.toggle(label_id)

        return _on_click

    # ── Dock window ─────────────────────────────────────────────

    def _build_window(self) -> None:
        window = QMainWindow()
        window.setWindowTitle("Multi-select")
        window.setWindowFlag(Qt.Window)
        window.resize(260, 180)
        window.setStyleSheet(
            f"background-color: {theme.BACKGROUND}; color: {theme.TEXT_BRIGHT};"
        )

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel("<b>Multi-select</b>")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        help_text = QLabel(
            "Click labels in the viewer to toggle. "
            "Ctrl+Return accepts, Esc cancels."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet(f"color: {theme.TEXT};")
        layout.addWidget(help_text)

        self._counter_label = QLabel("0 cells staged")
        self._counter_label.setStyleSheet(
            "color: #4ea8de; font-weight: bold; font-size: 13px;"
        )
        layout.addWidget(self._counter_label)

        layout.addStretch()

        nav = QHBoxLayout()
        self._accept_button = QPushButton("Accept")
        self._accept_button.setToolTip(
            "Commit the staged set as the current selection (Ctrl+Return)"
        )
        self._accept_button.clicked.connect(self.accept)
        nav.addWidget(self._accept_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.setToolTip("Discard the staged set (Esc)")
        cancel_button.clicked.connect(self.cancel)
        nav.addWidget(cancel_button)

        layout.addLayout(nav)
        window.setCentralWidget(central)

        # Keyboard shortcuts on the dock window — matches seg_qc.py:205-210.
        accept_sc = QShortcut(QKeySequence("Ctrl+Return"), window)
        accept_sc.activated.connect(self.accept)
        accept_sc_enter = QShortcut(QKeySequence("Ctrl+Enter"), window)
        accept_sc_enter.activated.connect(self.accept)
        cancel_sc = QShortcut(QKeySequence("Esc"), window)
        cancel_sc.activated.connect(self.cancel)

        # Trap X-button close → cancel.
        window.closeEvent = self._on_close_event  # type: ignore[assignment]

        # Now that the window exists, parent the refresh timer to it so
        # Qt cleans the timer up automatically on close.
        self._refresh_timer = QTimer(window)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)

        self._window = window

    def _on_close_event(self, event) -> None:  # noqa: ARG002
        if not self._torn_down:
            self.cancel()
        event.accept()
