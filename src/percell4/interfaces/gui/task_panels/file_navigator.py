"""File-system navigator for the Batch Tools console.

A compact single-column browser: the list shows one directory's entries;
double-clicking a folder descends into it, double-clicking a file emits its
path, and Up walks to the parent. It exists so batch commands can be composed
without leaving PerCell to look up file paths in a terminal or Finder.

The widget emits ``path_chosen(str)`` with an absolute path; the host decides
what to do with it (the console inserts it, shell-quoted, into the command
input and copies it to the clipboard).
"""

from __future__ import annotations

import os

from qtpy.QtCore import QDir, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme


class FileNavigator(QWidget):
    """Single-column file browser that emits chosen absolute paths."""

    path_chosen = Signal(str)

    def __init__(
        self, start_dir: str | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._model = QFileSystemModel(self)
        self._model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot
        )
        self._current = os.path.abspath(start_dir or QDir.homePath())
        self._build_ui()
        self._set_root(self._current)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self._up_btn = QPushButton("↑ Up")
        self._up_btn.setToolTip("Go to the parent directory.")
        self._up_btn.clicked.connect(self._on_up)
        top.addWidget(self._up_btn)
        self._path_label = QLabel()
        self._path_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        top.addWidget(self._path_label, stretch=1)
        layout.addLayout(top)

        self._view = QListView()
        self._view.setModel(self._model)
        self._view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._view.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._view, stretch=1)

        self._insert_btn = QPushButton("Insert path into command")
        self._insert_btn.setToolTip(
            "Insert the selected file/folder path into the command input."
        )
        self._insert_btn.clicked.connect(self._on_insert)
        layout.addWidget(self._insert_btn)

    # ── navigation ──────────────────────────────────────────────────────

    def _set_root(self, path: str) -> None:
        self._current = path
        self._view.setRootIndex(self._model.setRootPath(path))
        self._path_label.setText(path)
        self._path_label.setToolTip(path)

    def _on_up(self) -> None:
        parent = os.path.dirname(self._current.rstrip(os.sep)) or self._current
        if parent and parent != self._current:
            self._set_root(parent)

    def _on_double_click(self, index) -> None:
        path = self._model.filePath(index)
        if self._model.isDir(index):
            self._set_root(path)
        else:
            self.path_chosen.emit(path)

    def _on_insert(self) -> None:
        index = self._view.currentIndex()
        if index.isValid():
            self.path_chosen.emit(self._model.filePath(index))
