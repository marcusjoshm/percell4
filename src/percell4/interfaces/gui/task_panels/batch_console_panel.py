"""Batch Tools Console panel.

Assembles the catalog dropdown, ``Show --help``, the command input, the
streaming console view, and Run/Cancel/Clear into a decoupled, Action-class
panel: it reads session state via injected getters but never writes the five
session fields. A completed run that referenced the currently-open dataset
triggers a reload through an injected callback (never by poking session).

Because HDF5 file locking makes a batch write *fail* while the GUI holds the
target file open, the panel renders that lock failure legibly and only
reloads after a successful (exit 0) run.
"""

from __future__ import annotations

import os
import shlex
import time
from collections.abc import Callable

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from percell4.gui import theme
from percell4.interfaces.cli.catalog import (
    CommandParseError,
    UnknownCommand,
    list_batch_tools,
    resolve_command,
)
from percell4.interfaces.gui.task_panels.ansi_console_view import AnsiConsoleView
from percell4.interfaces.gui.task_panels.batch_command_runner import (
    BatchCommandRunner,
)
from percell4.interfaces.gui.task_panels.command_line_edit import CommandLineEdit

_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_RESET = "\x1b[0m"

_LOCK_SIGNATURES = ("unable to lock file", "errno 35", "blockingioerror")
_OUTPUT_TAIL_CAP = 8192


class BatchConsolePanel(QWidget):
    """Command console for running the ``percell4-*`` batch CLI catalog."""

    # The streaming console manages its own scroll — the launcher adds it to
    # the content stack directly rather than wrapping it in a QScrollArea.
    manages_own_scroll = True

    def __init__(
        self,
        *,
        get_open_h5_path: Callable[[], str | None] = lambda: None,
        reload_open_dataset: Callable[[], None] = lambda: None,
        show_status: Callable[[str], None] = lambda _: None,
        runner: BatchCommandRunner | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_open_h5_path = get_open_h5_path
        self._reload_open_dataset = reload_open_dataset
        self._show_status = show_status
        self._runner = runner if runner is not None else BatchCommandRunner(self)
        self._cwd = os.getcwd()

        # per-run state
        self._current_line = ""
        self._referenced_open = False
        self._cancelled = False
        self._run_output = ""
        self._t0 = 0.0

        self._build_ui()
        self._wire_runner()
        self._populate_catalog()

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Deliberate deviation from the standard panel convention: the console
        # view must expand (stretch=1) and manage its own scroll, so there is
        # no Qt.AlignTop and no trailing addStretch().
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(theme.section_label("Batch Tools"))

        top = QHBoxLayout()
        top.addWidget(QLabel("Tool:"))
        self._combo = QComboBox()
        self._combo.setToolTip("Pick a percell4-* batch tool to insert its name.")
        self._combo.activated.connect(self._on_catalog_selected)
        top.addWidget(self._combo, stretch=1)
        self._help_btn = QPushButton("Show --help")
        self._help_btn.setToolTip("Run the selected tool with --help.")
        self._help_btn.clicked.connect(self._on_show_help)
        top.addWidget(self._help_btn)
        layout.addLayout(top)

        self._view = AnsiConsoleView()
        self._view.setPlaceholderText(
            "No output yet — pick a tool or type a percell4-* command."
        )
        layout.addWidget(self._view, stretch=1)

        bottom = QHBoxLayout()
        self._input = CommandLineEdit()
        self._input.command_submitted.connect(self._run_line)
        bottom.addWidget(self._input, stretch=1)
        self._run_btn = QPushButton("Run")
        self._run_btn.clicked.connect(self._input.submit)
        bottom.addWidget(self._run_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._runner.cancel)
        bottom.addWidget(self._cancel_btn)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._view.clear_output)
        bottom.addWidget(self._clear_btn)
        layout.addLayout(bottom)

    def _wire_runner(self) -> None:
        self._runner.output.connect(self._on_output)
        self._runner.started.connect(lambda: None)
        self._runner.cancelled.connect(self._on_cancelled)
        self._runner.finished.connect(self._on_finished)

    def _populate_catalog(self) -> None:
        tools = list_batch_tools()
        self._combo.clear()
        for tool in tools:
            label = f"{tool.name}  —  {tool.summary}" if tool.summary else tool.name
            self._combo.addItem(label, tool.name)
        self._input.set_completions([t.name for t in tools])
        if tools:
            self._input.insert_command(tools[0].name)

    # ── catalog / help ──────────────────────────────────────────────────

    def _on_catalog_selected(self, index: int) -> None:
        name = self._combo.itemData(index)
        if name:
            self._input.insert_command(name)

    def _on_show_help(self) -> None:
        name = self._combo.currentData()
        if name:
            self._run_line(f"{name} --help")

    # ── run lifecycle ───────────────────────────────────────────────────

    def _run_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if self._runner.is_running:
            self._emit("A command is already running — cancel it first.", _YELLOW)
            return
        try:
            argv = resolve_command(line)
        except CommandParseError as exc:
            self._emit(f"[Error] {exc}", _RED)
            return
        except UnknownCommand as exc:
            self._emit(
                f"[Error] {exc.name!r} is not a percell4-* batch tool — "
                "pick from the catalog or type a percell4-* command.",
                _RED,
            )
            return

        self._current_line = line
        self._referenced_open = self._references_open_dataset(line)
        self._cancelled = False
        self._run_output = ""
        self._t0 = time.monotonic()

        self._view.append_output(f"$ {line}\n")
        if self._referenced_open:
            self._emit(
                "[note] This references the open dataset. If it writes that "
                "file, the run may fail while the GUI holds it open (close the "
                "dataset first); the view refreshes on success.",
                _YELLOW,
            )
        self._set_running(True)
        self._show_status(f"Running {line.split()[0]}…")
        self._runner.run(argv, self._cwd)

    def _on_output(self, text: str) -> None:
        self._view.append_output(text)
        self._run_output = (self._run_output + text)[-_OUTPUT_TAIL_CAP:]

    def _on_cancelled(self) -> None:
        self._cancelled = True
        self._emit("Cancelled", _YELLOW)

    def _on_finished(self, code: int) -> None:
        self._set_running(False)
        self._show_status("")
        elapsed = self._format_elapsed(time.monotonic() - self._t0)

        if self._cancelled:
            self._cancelled = False
            return
        if self._looks_like_lock_error():
            open_h5 = self._get_open_h5_path() or "the dataset"
            self._emit(
                f"[Error] {open_h5} is locked — the GUI has this dataset open. "
                "Close it and retry.",
                _RED,
            )
            return
        if code == 0:
            self._emit(f"[Done] Exit 0 ({elapsed})", _GREEN)
            if self._referenced_open:
                self._reload_open_dataset()
        else:
            self._emit(f"[Error] Exit {code} ({elapsed})", _RED)

    # ── helpers ─────────────────────────────────────────────────────────

    def _set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        self._help_btn.setEnabled(not running)
        self._combo.setEnabled(not running)
        # Clear stays enabled at all times.

    def _emit(self, msg: str, color: str) -> None:
        self._view.append_output(f"{color}{msg}{_RESET}\n")

    def _looks_like_lock_error(self) -> bool:
        low = self._run_output.lower()
        return any(sig in low for sig in _LOCK_SIGNATURES)

    def _references_open_dataset(self, line: str) -> bool:
        open_h5 = self._get_open_h5_path()
        if not open_h5:
            return False
        try:
            open_real = os.path.realpath(open_h5)
            tokens = shlex.split(line, posix=True)[1:]
        except ValueError:
            return False
        for tok in tokens:
            if tok.startswith("-"):
                continue
            base = tok if os.path.isabs(tok) else os.path.join(self._cwd, tok)
            tok_real = os.path.realpath(base)
            if tok_real == open_real:
                return True
            # a directory argument that contains the open .h5
            if open_real.startswith(tok_real.rstrip("/") + os.sep):
                return True
        return False

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60:d}:{seconds % 60:02d}"
