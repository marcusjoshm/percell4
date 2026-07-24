"""File-system navigator for the Batch Tools console.

A compact single-column browser: the list shows one directory's entries;
double-clicking a folder descends into it, double-clicking a file emits its
path, and Up walks to the parent. An editable path bar (type/paste a path, press
Enter) jumps anywhere directly — including mounted drives under ``/Volumes`` —
and Home / Volumes quick buttons jump to the common roots. It exists so batch
commands can be composed without leaving PerCell to look up file paths in a
terminal or Finder.

The widget emits ``path_chosen(str)`` with an absolute path; the host decides
what to do with it (the console inserts it, shell-quoted, into the command
input and copies it to the clipboard).
"""

from __future__ import annotations

import os

from qtpy.QtCore import QDir, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:  # QFileSystemModel moved QtWidgets (Qt5) -> QtGui (Qt6); qtpy normalizes.
    from qtpy.QtWidgets import QFileSystemModel
except ImportError:  # pragma: no cover - binding-dependent fallback
    from qtpy.QtGui import QFileSystemModel

_VOLUMES = "/Volumes"


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
        self._current = ""
        self._build_ui()
        self._set_root(start_dir or QDir.homePath())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Nav row: Up + an editable path bar (type/paste a path, Enter to jump).
        nav = QHBoxLayout()
        self._up_btn = QPushButton("↑ Up")
        self._up_btn.setToolTip("Go to the parent directory.")
        self._up_btn.clicked.connect(self._on_up)
        nav.addWidget(self._up_btn)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Type or paste a path, Enter to go…")
        self._path_edit.setToolTip(
            "Jump to any directory (e.g. /Volumes/MyDrive/data), then Enter."
        )
        self._path_edit.returnPressed.connect(self._on_path_entered)
        nav.addWidget(self._path_edit, stretch=1)
        layout.addLayout(nav)

        # Quick jumps: Home and (on macOS) mounted drives under /Volumes.
        quick = QHBoxLayout()
        home_btn = QPushButton("Home")
        home_btn.setToolTip("Jump to your home folder.")
        home_btn.clicked.connect(lambda: self._set_root(QDir.homePath()))
        quick.addWidget(home_btn)
        if os.path.isdir(_VOLUMES):
            vol_btn = QPushButton("Volumes")
            vol_btn.setToolTip("Jump to mounted drives (/Volumes).")
            vol_btn.clicked.connect(lambda: self._set_root(_VOLUMES))
            quick.addWidget(vol_btn)
        quick.addStretch()
        layout.addLayout(quick)

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
        path = os.path.abspath(os.path.expanduser(path))
        self._current = path
        self._view.setRootIndex(self._model.setRootPath(path))
        self._path_edit.setText(path)

    def _on_up(self) -> None:
        parent = os.path.dirname(self._current.rstrip(os.sep)) or self._current
        if parent and parent != self._current:
            self._set_root(parent)

    def _on_path_entered(self) -> None:
        text = self._path_edit.text().strip()
        if not text:
            return
        path = os.path.abspath(os.path.expanduser(text))
        if os.path.isdir(path):
            self._set_root(path)
        elif os.path.isfile(path):
            self._set_root(os.path.dirname(path))
        else:
            self._path_edit.setText(self._current)  # unknown path — restore

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
