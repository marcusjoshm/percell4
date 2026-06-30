"""Viewer task panel — open / hide the viewer window and scope the cell selection.

Receives the shared ``CellDataModel`` plus window callbacks at construction —
no launcher reference. The viewer is a persistent singleton: ``Open Viewer``
shows/raises it, ``Hide Viewer`` hides the window without destroying the
viewer (it stays active and subscribed to the session).

Hosts the **Cell Filter** Selector — the canonical writer of the session
``filter_ids`` field. Selection originates here (and in the viewer canvas,
cell table, and data plot, which are co-writers of ``selection``); filtering
to that selection scopes every window. Writes go straight to the session via
``CellDataModel`` — never through napari layer-list events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme
from percell4.model import CellDataModel


class ViewerPanel(QWidget):
    """Panel hosting viewer-window controls and the Cell Filter Selector.

    All collaborators are injected — the panel has no knowledge of the
    launcher internals beyond the callbacks it is given.
    """

    def __init__(
        self,
        data_model: CellDataModel,
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

        # Subscribe to filter changes so the Clear-Filter button enabled-state
        # and the count label stay in sync (relocated with the Selector).
        self._data_model.state_changed.connect(self._on_state_changed)

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

        # ── Cell Filter group ──
        filter_group = QGroupBox("Cell Filter")
        filter_layout = QVBoxLayout(filter_group)

        sel_btn_row = QHBoxLayout()
        btn_clear_sel = QPushButton("Clear Selection")
        btn_clear_sel.setToolTip("Deselect all cells and restore viewer to normal")
        btn_clear_sel.clicked.connect(self._on_clear_selection)
        sel_btn_row.addWidget(btn_clear_sel)
        filter_layout.addLayout(sel_btn_row)

        filter_btn_row = QHBoxLayout()
        btn_filter = QPushButton("Filter to Selection")
        btn_filter.setToolTip("Show only the currently selected cells in all windows")
        btn_filter.clicked.connect(self._on_filter_to_selection)
        filter_btn_row.addWidget(btn_filter)

        self._clear_filter_btn = QPushButton("Clear Filter")
        self._clear_filter_btn.setEnabled(False)
        self._clear_filter_btn.clicked.connect(self._on_clear_filter)
        filter_btn_row.addWidget(self._clear_filter_btn)
        filter_layout.addLayout(filter_btn_row)

        self._filter_status_label = QLabel("No filter active")
        self._filter_status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        filter_layout.addWidget(self._filter_status_label)

        layout.addWidget(filter_group)

        layout.addStretch()

    # ── Viewer actions ───────────────────────────────────────

    def _on_hide_viewer(self) -> None:
        """Hide the viewer window if it exists. No-op when never opened."""
        win = self._get_viewer_window()
        if win is not None:
            win.hide()

    # ── State change routing ─────────────────────────────────

    def _on_state_changed(self, change) -> None:
        if change.filter:
            self._on_filter_state_changed()

    # ── Cell Filter (Selector: writes session filter_ids / selection) ──

    def _on_clear_selection(self) -> None:
        self._data_model.set_selection([])

    def _on_filter_to_selection(self) -> None:
        selected = self._data_model.selected_ids
        if not selected:
            self._show_status("No cells selected to filter")
            return
        self._data_model.set_filter(list(selected))

    def _on_clear_filter(self) -> None:
        self._data_model.set_filter(None)

    def _on_filter_state_changed(self) -> None:
        if self._data_model.is_filtered:
            n_filtered = len(self._data_model.filtered_df)
            n_total = len(self._data_model.df)
            self._filter_status_label.setText(
                f"Showing {n_filtered} of {n_total} cells"
            )
            self._filter_status_label.setStyleSheet(
                f"color: {theme.ACCENT}; font-weight: bold;"
            )
            self._clear_filter_btn.setEnabled(True)
        else:
            self._filter_status_label.setText("No filter active")
            self._filter_status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self._clear_filter_btn.setEnabled(False)
