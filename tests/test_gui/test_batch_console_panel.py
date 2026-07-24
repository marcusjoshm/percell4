"""Tests for BatchConsolePanel.

A FakeRunner stands in for the QProcess runner so no real subprocess is
spawned; the panel's catalog/resolve logic is exercised for real. The wide
redesign (U3) adds a dedicated help runner + help pane, a QListWidget catalog,
and a run path that keeps Show --help / the catalog enabled while a command runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from qtpy.QtCore import QObject, Qt, Signal

from percell4.interfaces.gui.task_panels.batch_console_panel import (
    BatchConsolePanel,
)


class FakeRunner(QObject):
    started = Signal()
    output = Signal(str)
    finished = Signal(int)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[str], str | None]] = []
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def run(self, argv, cwd=None) -> None:
        self.calls.append((argv, cwd))
        self._running = True
        self.started.emit()

    def cancel(self) -> None:
        self.cancelled.emit()
        self._running = False
        self.finished.emit(-15)

    def emit_finished(self, code: int) -> None:
        self._running = False
        self.finished.emit(code)


def _panel(qtbot, **kwargs):
    runner = kwargs.pop("runner", None) or FakeRunner()
    panel = BatchConsolePanel(runner=runner, **kwargs)
    qtbot.addWidget(panel)
    return panel, runner


def test_instantiates_headless_with_defaults(qtbot):
    panel, _ = _panel(qtbot)
    # console starts empty; input seeded with the first catalog tool name
    assert panel._view.toPlainText() == ""
    assert panel._input.text().startswith("percell4-")


def test_default_catalog_selection_present(qtbot):
    # setCurrentRow(0) in _populate_catalog means a tool is always current, so
    # Show --help resolves without a prior click.
    panel, _ = _panel(qtbot)
    assert panel._current_tool_name() is not None
    assert panel._current_tool_name().startswith("percell4-")


def test_unknown_command_shows_error_and_does_not_run(qtbot):
    panel, runner = _panel(qtbot)
    panel._run_line("ls -la")
    assert runner.calls == []
    assert "not a percell4-* batch tool" in panel._view.toPlainText()


def test_unbalanced_quote_shows_error_and_does_not_run(qtbot):
    panel, runner = _panel(qtbot)
    panel._run_line('percell4-inspect "unclosed')
    assert runner.calls == []
    assert "[Error]" in panel._view.toPlainText()


def test_known_command_runs_with_module_argv_and_toggles_buttons(qtbot):
    panel, runner = _panel(qtbot)
    panel._run_line("percell4-inspect exp.h5")

    assert len(runner.calls) == 1
    argv = runner.calls[0][0]
    assert argv[:3] == [
        sys.executable,
        "-m",
        "percell4.interfaces.cli.inspect_dataset",
    ]
    assert not panel._run_btn.isEnabled()
    assert panel._cancel_btn.isEnabled()
    assert panel._clear_btn.isEnabled()  # Clear stays enabled during a run

    runner.emit_finished(0)
    assert panel._run_btn.isEnabled()
    assert not panel._cancel_btn.isEnabled()
    assert "[Done] Exit 0" in panel._view.toPlainText()


def test_set_running_keeps_help_and_catalog_enabled(qtbot):
    # The wide window's payoff: --help must stay reachable while a run streams.
    # This also guards against the old _set_running referencing the removed
    # _combo (which would AttributeError on the run path).
    panel, runner = _panel(qtbot)
    panel._run_line("percell4-inspect exp.h5")
    assert not panel._run_btn.isEnabled()
    assert panel._cancel_btn.isEnabled()
    assert panel._help_btn.isEnabled()
    assert panel._catalog.isEnabled()
    runner.emit_finished(0)
    assert panel._run_btn.isEnabled()


# ── Help pane (dedicated runner) ────────────────────────────


def test_show_help_runs_selected_tool_on_help_runner(qtbot):
    help_runner = FakeRunner()
    panel, runner = _panel(qtbot, help_runner=help_runner)
    panel._catalog.setCurrentRow(0)
    panel._on_show_help()
    assert help_runner.calls, "help should invoke the help runner"
    assert help_runner.calls[0][0][-1] == "--help"
    assert runner.calls == []  # main runner untouched by --help


def test_help_output_streams_to_help_pane_only(qtbot):
    help_runner = FakeRunner()
    panel, _ = _panel(qtbot, help_runner=help_runner)
    panel._catalog.setCurrentRow(0)
    panel._on_show_help()
    # Show --help disabled while its own runner streams.
    assert not panel._help_btn.isEnabled()
    help_runner.output.emit("usage: percell4-foo [-h]\n")
    assert "usage:" in panel._help_view.toPlainText()
    assert "usage:" not in panel._view.toPlainText()  # not the run console
    help_runner.emit_finished(0)
    assert panel._help_btn.isEnabled()  # re-enabled on finish


def test_catalog_selection_updates_input_and_clears_help(qtbot):
    help_runner = FakeRunner()
    panel, _ = _panel(qtbot, help_runner=help_runner)
    assert panel._catalog.count() > 1, "catalog should list multiple tools"
    # Show help for the first tool.
    panel._catalog.setCurrentRow(0)
    panel._on_show_help()
    help_runner.output.emit("help for tool 0\n")
    assert panel._help_view.toPlainText() != ""
    # Selecting a different tool inserts its name and clears the stale help.
    panel._catalog.setCurrentRow(1)
    name1 = panel._catalog.item(1).data(Qt.UserRole)
    assert panel._input.text().startswith(name1)
    assert panel._help_view.toPlainText() == ""


def test_cancelled_run_shows_cancelled_and_skips_exit_status(qtbot):
    panel, runner = _panel(qtbot)
    panel._run_line("percell4-inspect exp.h5")
    runner.cancel()
    text = panel._view.toPlainText()
    assert "Cancelled" in text
    assert "[Done]" not in text
    assert "Exit" not in text


def test_lock_error_rendered_and_no_reload(qtbot, tmp_path):
    open_h5 = tmp_path / "open.h5"
    reloaded: list[int] = []
    panel, runner = _panel(
        qtbot,
        get_open_h5_path=lambda: str(open_h5),
        reload_open_dataset=lambda: reloaded.append(1),
    )
    panel._run_line(f"percell4-batch-measure {open_h5}")
    runner.output.emit("OSError: [Errno 35] unable to lock file\n")
    runner.emit_finished(1)
    text = panel._view.toPlainText()
    assert "is locked" in text
    assert reloaded == []  # never reload after a failure


def test_reload_fires_only_on_success_and_reference(qtbot, tmp_path):
    open_h5 = tmp_path / "open.h5"
    other = tmp_path / "other.h5"

    # references the open dataset + exits 0 → reload
    reloaded: list[int] = []
    panel, runner = _panel(
        qtbot,
        get_open_h5_path=lambda: str(open_h5),
        reload_open_dataset=lambda: reloaded.append(1),
    )
    panel._run_line(f"percell4-batch-measure {open_h5}")
    runner.emit_finished(0)
    assert reloaded == [1]

    # references a different file → no reload
    reloaded2: list[int] = []
    panel2, runner2 = _panel(
        qtbot,
        get_open_h5_path=lambda: str(open_h5),
        reload_open_dataset=lambda: reloaded2.append(1),
    )
    panel2._run_line(f"percell4-inspect {other}")
    runner2.emit_finished(0)
    assert reloaded2 == []


def test_panel_never_writes_session_fields():
    # Action-class guard: the panel must not mutate the five session fields.
    src = Path(
        "src/percell4/interfaces/gui/task_panels/batch_console_panel.py"
    ).read_text()
    for forbidden in (
        "set_active_channel",
        "set_active_segmentation",
        "set_active_mask",
        "set_filter",
        "set_selection",
        ".selection =",
    ):
        assert forbidden not in src, f"panel must not call {forbidden}"
