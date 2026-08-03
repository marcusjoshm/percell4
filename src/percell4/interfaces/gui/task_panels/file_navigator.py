"""File-system navigator for the Batch Tools console.

A compact, Spyder-style browser: a toolbar of navigation actions above a file
tree. The toolbar has Back / Forward (directory history), Up (parent), Open
(native folder picker — the OS dialog handles typing/pasting any path, mounted
drives, and network shares on every platform), and Go to dataset (☞ — jump to
the folder holding the currently-loaded ``.h5``). An editable path field below
the toolbar shows the current folder and lets you type or paste any path to jump
there. Double-clicking a folder navigates into it (re-roots); its expand arrow
opens the subtree in place. Double-clicking a file — or selecting one and
pressing Insert — emits its path.

It exists so batch commands can be composed without leaving PerCell to look up
file paths in a terminal or Finder. The widget emits ``path_chosen(str)`` with an
absolute path; the host inserts it (shell-quoted) into the command input and
copies it to the clipboard.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from qtpy.QtCore import QDir, Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStyle,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from percell4.gui._dialog_utils import existing_directory

try:  # QFileSystemModel moved QtWidgets (Qt5) -> QtGui (Qt6); qtpy normalizes.
    from qtpy.QtWidgets import QFileSystemModel
except ImportError:  # pragma: no cover - binding-dependent fallback
    from qtpy.QtGui import QFileSystemModel


class FileNavigator(QWidget):
    """Toolbar-driven file-tree browser that emits chosen paths."""

    path_chosen = Signal(str)

    def __init__(
        self,
        start_dir: str | None = None,
        *,
        get_dataset_dir: Callable[[], str | None] = lambda: None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_dataset_dir = get_dataset_dir
        self._model = QFileSystemModel(self)
        self._model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot
        )
        self._history: list[str] = []
        self._hist_idx = -1
        self._current = ""

        self._build_ui()

        start = start_dir or QDir.homePath()
        if not os.path.isdir(start):
            start = QDir.homePath()
        self._navigate(start)

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        sp = QStyle.StandardPixmap
        self._back_btn = self._tool_button(
            sp.SP_ArrowBack, "Back", self._back
        )
        self._fwd_btn = self._tool_button(
            sp.SP_ArrowForward, "Forward", self._forward
        )
        self._up_btn = self._tool_button(
            sp.SP_ArrowUp, "Up to parent directory", self._up
        )
        self._browse_btn = self._tool_button(
            sp.SP_DirOpenIcon, "Open a folder…", self._browse
        )
        for btn in (self._back_btn, self._fwd_btn, self._up_btn, self._browse_btn):
            bar.addWidget(btn)
        self._dataset_btn = QToolButton()
        self._dataset_btn.setText("☞")  # pointing finger → jump to dataset
        self._dataset_btn.setToolTip(
            "Go to the folder of the currently loaded dataset."
        )
        self._dataset_btn.clicked.connect(self._goto_dataset)
        bar.addWidget(self._dataset_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Type or paste a path, Enter to go…")
        self._path_edit.setToolTip(
            "Current folder — edit and press Enter to jump anywhere "
            "(e.g. /Volumes/Drive/data or C:\\data)."
        )
        self._path_edit.returnPressed.connect(self._on_path_entered)
        layout.addWidget(self._path_edit)

        self._view = QTreeView()
        self._view.setModel(self._model)
        self._view.setHeaderHidden(True)
        for col in range(1, self._model.columnCount()):
            self._view.hideColumn(col)  # Name only — hide Size/Type/Date
        # Double-click navigates into a folder; the expand arrow (not the
        # double-click) toggles the subtree.
        self._view.setExpandsOnDoubleClick(False)
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

    def _tool_button(
        self, pixmap: QStyle.StandardPixmap, tip: str, slot: Callable[[], None]
    ) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(self.style().standardIcon(pixmap))
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        return btn

    # ── navigation ──────────────────────────────────────────────────────

    def _navigate(self, path: str) -> None:
        """User-initiated navigation: record it in history, then show it."""
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return
        del self._history[self._hist_idx + 1 :]  # drop forward history
        self._history.append(path)
        self._hist_idx = len(self._history) - 1
        self._apply(path)

    def _apply(self, path: str) -> None:
        """Show ``path`` in the view/path-field without touching history."""
        self._current = path
        self._view.setRootIndex(self._model.setRootPath(path))
        self._path_edit.setText(path)
        self._update_nav_buttons()

    def _on_path_entered(self) -> None:
        text = self._path_edit.text().strip()
        if not text:
            return
        path = os.path.abspath(os.path.expanduser(text))
        if os.path.isdir(path):
            self._navigate(path)
        elif os.path.isfile(path):
            self._navigate(os.path.dirname(path))
        else:
            self._path_edit.setText(self._current)  # unknown path — restore

    def _back(self) -> None:
        if self._hist_idx > 0:
            self._hist_idx -= 1
            self._apply(self._history[self._hist_idx])

    def _forward(self) -> None:
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self._apply(self._history[self._hist_idx])

    def _up(self) -> None:
        parent = os.path.dirname(self._current.rstrip(os.sep)) or self._current
        if parent and parent != self._current:
            self._navigate(parent)

    def _browse(self) -> None:
        path = existing_directory(
            self, "Open folder", self._current
        )
        if path:
            self._navigate(path)

    def _goto_dataset(self) -> None:
        dataset_dir = self._get_dataset_dir()
        if dataset_dir and os.path.isdir(dataset_dir):
            self._navigate(dataset_dir)

    def _update_nav_buttons(self) -> None:
        self._back_btn.setEnabled(self._hist_idx > 0)
        self._fwd_btn.setEnabled(self._hist_idx < len(self._history) - 1)
        dataset_dir = self._get_dataset_dir()
        self._dataset_btn.setEnabled(
            bool(dataset_dir and os.path.isdir(dataset_dir))
        )

    def showEvent(self, event) -> None:
        # A dataset may have loaded/unloaded while hidden — refresh on show.
        self._update_nav_buttons()
        super().showEvent(event)

    # ── selection → path ────────────────────────────────────────────────

    def _on_double_click(self, index) -> None:
        # Double-click navigates into a folder (re-roots); a file inserts its
        # path. The expand arrow still opens a folder's subtree in place.
        path = self._model.filePath(index)
        if self._model.isDir(index):
            self._navigate(path)
        else:
            self.path_chosen.emit(path)

    def _on_insert(self) -> None:
        index = self._view.currentIndex()
        if index.isValid():
            self.path_chosen.emit(self._model.filePath(index))
