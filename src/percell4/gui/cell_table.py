"""Cell table window — tabular view of per-cell measurements.

QTableView backed by a PandasTableModel with sorting via
QSortFilterProxyModel. Row clicks sync to CellDataModel selection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from qtpy.QtCore import QAbstractTableModel, QModelIndex, QSettings, QSortFilterProxyModel, Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from percell4.model import CellDataModel, StateChange


class PandasTableModel(QAbstractTableModel):
    """QAbstractTableModel backed by a pandas DataFrame.

    Uses df.iat for fast single-element access. Formats floats compactly.
    Returns raw values via Qt.UserRole for correct sort ordering.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._df = pd.DataFrame()
        self._columns: list[str] = []
        self._label_to_row: dict[int, int] = {}

    def set_dataframe(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df
        self._columns = list(df.columns)
        self._label_to_row = (
            {int(v): i for i, v in enumerate(df["label"])}
            if "label" in df.columns else {}
        )
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._df)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        value = self._df.iat[index.row(), index.column()]

        if role == Qt.DisplayRole:
            if isinstance(value, float):
                if np.isnan(value):
                    return ""
                return f"{value:.4g}"
            return str(value)

        if role == Qt.TextAlignmentRole:
            if isinstance(value, (int, float, np.integer, np.floating)):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        # Raw value for sorting — must be native Python types, not numpy scalars
        if role == Qt.UserRole:
            if isinstance(value, np.floating):
                return float("inf") if np.isnan(value) else float(value)
            if isinstance(value, np.integer):
                return int(value)
            if isinstance(value, float) and np.isnan(value):
                return float("inf")
            return value

        return None

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self._columns[section] if section < len(self._columns) else None
        return str(section + 1)

    def get_label_for_row(self, row: int) -> int | None:
        """Get the cell label ID for a given row index."""
        if "label" not in self._columns or row >= len(self._df):
            return None
        return int(self._df.iat[row, self._columns.index("label")])

    def find_row_for_label(self, label_id: int) -> int | None:
        """Find the row index for a given cell label ID. O(1) via dict index."""
        return self._label_to_row.get(label_id)


class FilterableProxyModel(QSortFilterProxyModel):
    """Proxy that filters rows by label ID set while preserving sort state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._visible_labels: set[int] | None = None  # None = show all

    def set_filter_labels(self, label_ids: set[int] | None) -> None:
        """Set the filter. None = show all rows."""
        self._visible_labels = label_ids
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if self._visible_labels is None:
            return True
        source_model = self.sourceModel()
        label = source_model.get_label_for_row(source_row)
        return label in self._visible_labels if label is not None else False


class CellTableWindow(QMainWindow):
    """Table window showing per-cell measurements with sort and selection sync.

    Features:
    - QTableView backed by PandasTableModel
    - Column header click → sort (via FilterableProxyModel)
    - Row click → CellDataModel.set_selection
    - state_changed → highlight and scroll to row, show/hide rows via proxy filter
    - Right-click context menu: Export Selection, Export All
    - Export CSV button in toolbar
    """

    def __init__(self, data_model: CellDataModel) -> None:
        super().__init__()
        self.data_model = data_model
        self.setWindowTitle("PerCell4 — Cell Table")
        self.resize(850, 450)

        self._build_ui()
        self._connect_signals()
        self._restore_geometry()

        # Bootstrap from existing model state (late-joining window)
        if not self.data_model.df.empty:
            self._on_state_changed(StateChange(data=True, filter=True, selection=True))

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        toolbar = QHBoxLayout()
        btn_export = QPushButton("Export CSV...")
        btn_export.clicked.connect(self._export_all)
        toolbar.addWidget(btn_export)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Table model + proxy for sorting + filtering
        self._model = PandasTableModel()
        self._proxy = FilterableProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(Qt.UserRole)

        # Table view
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        # Table styling inherited from global theme
        layout.addWidget(self._table)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("No data")

        # Guard flag: True when this window initiated a state change.
        # Prevents feedback loops from Qt's internal selectionChanged signals
        # (fired by invalidateFilter and programmatic row selection).
        self._is_originator = False

    def _connect_signals(self) -> None:
        self.data_model.state_changed.connect(self._on_state_changed)
        self._table.selectionModel().selectionChanged.connect(
            self._on_table_selection_changed
        )

    # ── Unified state change handler ─────────────────────────

    def _on_state_changed(self, change) -> None:
        """Handle all model state changes in one atomic pass."""
        if self._is_originator:
            return
        if change.data:
            self._reload_table_data()
            # Data changed under an active filter/selection — re-apply them
            # (set_measurements no longer emits filter=True/selection=True)
            if self.data_model.is_filtered:
                self._apply_filter()
            if self.data_model.selected_ids:
                self._highlight_selected_rows()
        if change.filter:
            self._apply_filter()
        if change.selection:
            self._highlight_selected_rows()

    def _reload_table_data(self) -> None:
        """Replace the table's DataFrame and update status."""
        df = self.data_model.df
        self._model.set_dataframe(df)

        if df.empty:
            self._status.showMessage("No data")
        else:
            self._status.showMessage(f"Showing {len(df)} cells")

        self._table.resizeColumnsToContents()

    def _apply_filter(self) -> None:
        """Update proxy filter. Guards against Qt's internal selectionChanged.

        invalidateFilter() changes row visibility, which can trigger
        QItemSelectionModel::selectionChanged. Without the guard,
        _on_table_selection_changed would fire and call set_selection()
        with potentially wrong IDs.
        """
        self._is_originator = True
        try:
            self._proxy.set_filter_labels(self.data_model.filtered_ids)
            n_visible = self._proxy.rowCount()
            n_total = self._model.rowCount()
            if self.data_model.is_filtered:
                self._status.showMessage(
                    f"Showing {n_visible} of {n_total} cells (filtered)"
                )
            else:
                self._status.showMessage(f"Showing {n_total} cells")
        finally:
            self._is_originator = False

    def _highlight_selected_rows(self) -> None:
        """Highlight and scroll to rows matching the model's selected IDs."""
        self._is_originator = True
        try:
            label_ids = self.data_model.selected_ids
            selection_model = self._table.selectionModel()
            selection_model.clearSelection()

            if not label_ids:
                n_visible = self._proxy.rowCount()
                n_total = self._model.rowCount()
                if self.data_model.is_filtered:
                    self._status.showMessage(
                        f"Showing {n_visible} of {n_total} cells (filtered)"
                    )
                else:
                    self._status.showMessage(f"Showing {n_total} cells")
                return

            first_row = None
            for label_id in label_ids:
                source_row = self._model.find_row_for_label(label_id)
                if source_row is None:
                    continue
                proxy_index = self._proxy.mapFromSource(
                    self._model.index(source_row, 0)
                )
                if proxy_index.isValid():
                    self._table.selectRow(proxy_index.row())
                    if first_row is None:
                        first_row = proxy_index.row()

            if first_row is not None:
                self._table.scrollTo(
                    self._proxy.index(first_row, 0),
                    QAbstractItemView.PositionAtCenter,
                )

            n_selected = len(label_ids)
            n_total = self._model.rowCount()
            self._status.showMessage(f"Selected: {n_selected} | Total: {n_total} cells")
        finally:
            self._is_originator = False

    # ── Table → model selection ───────────────────────────────

    def _on_table_selection_changed(self, selected, deselected) -> None:
        """Forward table row selection to CellDataModel."""
        if self._is_originator:
            return  # Avoid feedback loop

        self._is_originator = True
        try:
            rows = set()
            for index in self._table.selectionModel().selectedRows():
                source_index = self._proxy.mapToSource(index)
                rows.add(source_index.row())

            label_ids = []
            for row in sorted(rows):
                lid = self._model.get_label_for_row(row)
                if lid is not None:
                    label_ids.append(lid)

            if label_ids:
                self.data_model.set_selection(label_ids)
        finally:
            self._is_originator = False

    # ── Context menu ──────────────────────────────────────────

    def _show_context_menu(self, position) -> None:
        menu = QMenu(self._table)

        export_sel = menu.addAction("Export Selection to CSV...")
        export_sel.triggered.connect(self._export_selection)

        export_all = menu.addAction("Export All to CSV...")
        export_all.triggered.connect(self._export_all)

        menu.exec_(self._table.viewport().mapToGlobal(position))

    def _export_all(self) -> None:
        """Export visible (filtered) DataFrame to CSV."""
        df = self.data_model.filtered_df
        if df.empty:
            self._status.showMessage("No data to export")
            return
        label = "Export Filtered Measurements" if self.data_model.is_filtered else "Export All Measurements"
        path, _ = QFileDialog.getSaveFileName(
            self, label, "measurements.csv", "CSV (*.csv)"
        )
        if path:
            df.to_csv(path, index=False)
            self._status.showMessage(f"Exported {len(df)} rows to {path}")

    def _export_selection(self) -> None:
        """Export selected rows to CSV."""
        df = self.data_model.df
        selected_ids = self.data_model.selected_ids
        if not selected_ids or df.empty:
            self._status.showMessage("No selection to export")
            return

        selected_df = df[df["label"].isin(selected_ids)]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Selection", "selection.csv", "CSV (*.csv)"
        )
        if path:
            selected_df.to_csv(path, index=False)
            self._status.showMessage(
                f"Exported {len(selected_df)} rows to {path}"
            )

    # ── Lifecycle ─────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_geometry()
        self.hide()
        event.ignore()

    def _save_geometry(self) -> None:
        QSettings("LeeLabPerCell4", "PerCell4").setValue(
            "cell_table/geometry", self.saveGeometry()
        )

    def _restore_geometry(self) -> None:
        geom = QSettings("LeeLabPerCell4", "PerCell4").value("cell_table/geometry")
        if geom:
            self.restoreGeometry(geom)
