"""Tests for BatchCommandRunner (QProcess wrapper).

Uses short ``python -c`` children through qtbot's event loop to exercise
streaming, exit codes, cancellation, and multibyte-split decoding.
"""

from __future__ import annotations

import sys

from percell4.interfaces.gui.task_panels.batch_command_runner import (
    BatchCommandRunner,
)


def test_runs_and_streams_output(qtbot):
    runner = BatchCommandRunner()
    outputs: list[str] = []
    runner.output.connect(outputs.append)
    with qtbot.waitSignal(runner.finished, timeout=10000) as blocker:
        runner.run([sys.executable, "-c", "print('hello-console')"])
    assert blocker.args == [0]
    assert "hello-console" in "".join(outputs)
    assert not runner.is_running


def test_nonzero_exit(qtbot):
    runner = BatchCommandRunner()
    with qtbot.waitSignal(runner.finished, timeout=10000) as blocker:
        runner.run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert blocker.args == [3]


def test_cancel_terminates_running_child(qtbot):
    runner = BatchCommandRunner()
    cancelled: list[bool] = []
    runner.cancelled.connect(lambda: cancelled.append(True))
    with qtbot.waitSignal(runner.started, timeout=10000):
        runner.run([sys.executable, "-c", "import time; time.sleep(30)"])
    with qtbot.waitSignal(runner.finished, timeout=10000):
        runner.cancel()
    assert cancelled == [True]
    assert not runner.is_running


def test_multibyte_glyph_split_across_reads(qtbot):
    # Write a 3-byte block glyph in two flushes so readyRead can fire twice;
    # the incremental decoder must buffer the partial codepoint, not emit
    # replacement characters.
    code = (
        "import sys, time\n"
        "b = '\\u2588'.encode()\n"
        "sys.stdout.buffer.write(b[:1]); sys.stdout.buffer.flush()\n"
        "time.sleep(0.25)\n"
        "sys.stdout.buffer.write(b[1:]); sys.stdout.buffer.flush()\n"
    )
    runner = BatchCommandRunner()
    outputs: list[str] = []
    runner.output.connect(outputs.append)
    with qtbot.waitSignal(runner.finished, timeout=10000):
        runner.run([sys.executable, "-c", code])
    joined = "".join(outputs)
    assert "█" in joined
    assert "�" not in joined
