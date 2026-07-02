"""Terminal-style command input for the Batch Tools Console.

A ``QLineEdit`` with:

* Enter → ``command_submitted(str)`` and history record (field clears).
* Up/Down → recall history.
* Tab → complete the *first token* against an injected list of command
  names (v1 does not do filesystem path completion — drag-drop covers path
  insertion).
* Drag-drop → insert dropped file/folder paths at the cursor (shell-quoted).

The completion list is injected (``set_completions``) so the widget stays
generic and free of any catalog dependency.
"""

from __future__ import annotations

import os
import shlex

from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtWidgets import QCompleter, QLineEdit


class CommandLineEdit(QLineEdit):
    """Single-line command input with history, completion, and drag-drop."""

    command_submitted = Signal(str)

    def __init__(self, parent=None, completions: list[str] | None = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._hist_idx = 0
        self._completions: list[str] = list(completions or [])

        self._completer = QCompleter(self._completions, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        self.setCompleter(self._completer)

        self.setAcceptDrops(True)
        self.setPlaceholderText("Type a percell4-* command, or pick a tool above…")
        self.returnPressed.connect(self._on_return)

    # ── public API ──────────────────────────────────────────────────────

    def set_completions(self, names: list[str]) -> None:
        self._completions = list(names)
        self._completer.model().setStringList(self._completions)

    def insert_command(self, name: str) -> None:
        """Seed the field with a command name (from the catalog dropdown)."""
        self.setText(name + " ")
        self.setFocus()
        self.end(False)

    def insert_paths(self, paths: list[str]) -> None:
        """Insert shell-quoted paths at the cursor, space-separated."""
        usable = [p for p in paths if p]
        if usable:
            self.insert(" ".join(shlex.quote(p) for p in usable) + " ")

    # ── history ─────────────────────────────────────────────────────────

    def _on_return(self) -> None:
        text = self.text().strip()
        if not text:
            return
        self._history.append(text)
        self._hist_idx = len(self._history)
        self.clear()
        self.command_submitted.emit(text)

    def _history_prev(self) -> None:
        if self._history and self._hist_idx > 0:
            self._hist_idx -= 1
            self.setText(self._history[self._hist_idx])
            self.end(False)

    def _history_next(self) -> None:
        if not self._history:
            return
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self.setText(self._history[self._hist_idx])
            self.end(False)
        else:
            self._hist_idx = len(self._history)
            self.clear()

    # ── key handling ────────────────────────────────────────────────────

    def event(self, e) -> bool:
        # Intercept Tab before Qt's focus machinery consumes it.
        if e.type() == QEvent.Type.KeyPress and e.key() in (
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
        ):
            self._complete_first_token()
            return True
        return super().event(e)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Up:
            self._history_prev()
            return
        if key == Qt.Key.Key_Down:
            self._history_next()
            return
        super().keyPressEvent(event)

    def _complete_first_token(self) -> None:
        text = self.text()
        if not text or " " in text.strip():
            return  # only the first token completes
        matches = [c for c in self._completions if c.startswith(text)]
        if not matches:
            return
        if len(matches) == 1:
            self.setText(matches[0] + " ")
            self.end(False)
            return
        prefix = os.path.commonprefix(matches)
        if len(prefix) > len(text):
            self.setText(prefix)
            self.end(False)
        self._completer.setCompletionPrefix(text)
        self._completer.complete()

    # ── drag & drop ─────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        md = event.mimeData()
        if not md.hasUrls():
            super().dropEvent(event)
            return
        self.insert_paths([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
        event.acceptProposedAction()
