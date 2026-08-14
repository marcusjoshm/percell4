"""Tests for the ANSI + carriage-return console view.

The pure ``AnsiParser`` (SGR + CR + partial-escape buffering) is tested
without Qt; the widget's line-overwrite, accumulation, and size cap use
qtbot.
"""

from __future__ import annotations

from percell4.interfaces.gui.task_panels.ansi_console_view import (
    AnsiConsoleView,
    AnsiParser,
    Style,
)

# ── Pure parser ─────────────────────────────────────────────────────────


def test_plain_text():
    assert AnsiParser().feed("hello") == [("text", "hello", Style())]


def test_sgr_color_then_reset():
    ops = AnsiParser().feed("\x1b[32mOK\x1b[0m done")
    assert ops == [
        ("text", "OK", Style(fg="green")),
        ("text", " done", Style()),
    ]


def test_bold_sgr():
    ops = AnsiParser().feed("\x1b[1mB\x1b[0m")
    assert ops == [("text", "B", Style(bold=True))]


def test_escape_split_across_chunks():
    p = AnsiParser()
    assert p.feed("\x1b[3") == []
    assert p.feed("2mOK\x1b[0m") == [("text", "OK", Style(fg="green"))]


def test_carriage_return_and_newline_ops():
    ops = AnsiParser().feed("10%\r50%\n")
    assert ops == [
        ("text", "10%", Style()),
        ("cr",),
        ("text", "50%", Style()),
        ("nl",),
    ]


def test_unsupported_256_color_skipped():
    ops = AnsiParser().feed("\x1b[38;5;200mX")
    assert ops == [("text", "X", Style())]
    assert all("\x1b" not in op[1] for op in ops if op[0] == "text")


# ── Widget ──────────────────────────────────────────────────────────────


def test_carriage_return_overwrites_line(qtbot):
    view = AnsiConsoleView()
    qtbot.addWidget(view)
    view.append_output("working... 10%\rworking... 50%\rworking... 100%\n")
    assert view.toPlainText().strip() == "working... 100%"


def test_accumulates_then_clears(qtbot):
    view = AnsiConsoleView()
    qtbot.addWidget(view)
    view.append_output("line one\n")
    view.append_output("line two\n")
    text = view.toPlainText()
    assert "line one" in text and "line two" in text
    view.clear_output()
    assert view.toPlainText() == ""


def test_block_count_is_bounded(qtbot):
    view = AnsiConsoleView(max_blocks=10)
    qtbot.addWidget(view)
    for i in range(50):
        view.append_output(f"line {i}\n")
    assert view.blockCount() <= 11
