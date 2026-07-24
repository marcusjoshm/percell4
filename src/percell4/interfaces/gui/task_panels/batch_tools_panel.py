"""Batch Tools task panel — open / hide the dedicated Batch Tools window.

Modeled on :class:`ViewerPanel`'s Open/Hide controls. The batch console lives in
its own top-level window (see ``peer_views/batch_tools_window.py``); this sidebar
page is an **Action** that only shows or hides that window. Selecting the Batch
Tools tab also auto-opens the window (wired in the launcher's
``_on_sidebar_click``), so ``Open`` here is the manual reopen path after a hide.

Injected collaborators only — no launcher reach-through. Writes none of the five
session selection fields (Action class).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from percell4.gui import theme


class BatchToolsPanel(QWidget):
    """Sidebar page with Open/Hide controls for the Batch Tools window."""

    def __init__(
        self,
        *,
        show_window: Callable[[str], None],
        get_batch_tools_window: Callable[[], Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._show_window = show_window
        self._get_batch_tools_window = get_batch_tools_window
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Batch Tools"))

        hint = QLabel(
            "The Batch Tools console opens in its own window when you select "
            "this tab. Use Open to re-show it after hiding."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(hint)

        btn_open = QPushButton("Open Batch Tools")
        btn_open.setToolTip("Open or re-show the Batch Tools window.")
        btn_open.clicked.connect(lambda: self._show_window("batch_tools"))
        layout.addWidget(btn_open)

        btn_hide = QPushButton("Hide Batch Tools")
        btn_hide.setToolTip("Hide the Batch Tools window; it stays active.")
        btn_hide.clicked.connect(self._on_hide)
        layout.addWidget(btn_hide)

        layout.addStretch()

    # ── Batch Tools window actions ───────────────────────────

    def _on_hide(self) -> None:
        """Hide the Batch Tools window if it exists. No-op when never opened."""
        win = self._get_batch_tools_window()
        if win is not None:
            win.hide()
