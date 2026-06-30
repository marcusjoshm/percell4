"""Viewer task panel — open / hide the viewer window and scope the cell selection.

Receives the shared ``CellDataModel`` plus window callbacks at construction —
no launcher reference. The viewer is a persistent singleton: ``Open Viewer``
shows/raises it, ``Hide Viewer`` hides the window without destroying the
viewer (it stays active and subscribed to the session).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme


class ViewerPanel(QWidget):
    """Panel hosting viewer-window controls.

    All collaborators are injected — the panel has no knowledge of the
    launcher internals beyond the callbacks it is given.
    """

    def __init__(
        self,
        data_model: Any,
        *,
        show_window: Callable[[str], None],
        get_viewer_window: Callable[[], Any],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data_model = data_model
        self._show_window = show_window
        self._get_viewer_window = get_viewer_window
        self._show_status = show_status
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Viewer"))

        btn_open = QPushButton("Open Viewer")
        btn_open.setToolTip("Open or re-show the viewer window.")
        btn_open.clicked.connect(lambda: self._show_window("viewer"))
        layout.addWidget(btn_open)

        btn_hide = QPushButton("Hide Viewer")
        btn_hide.setToolTip("Hide the viewer window; the viewer stays active.")
        btn_hide.clicked.connect(self._on_hide_viewer)
        layout.addWidget(btn_hide)

        layout.addStretch()

    # ── Actions ──────────────────────────────────────────────

    def _on_hide_viewer(self) -> None:
        """Hide the viewer window if it exists. No-op when never opened."""
        win = self._get_viewer_window()
        if win is not None:
            win.hide()
