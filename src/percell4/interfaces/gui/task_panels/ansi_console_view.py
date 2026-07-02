"""Streaming console view with ANSI colour + carriage-return handling.

Two parts:

* :class:`AnsiParser` — a pure, Qt-free stateful parser. Feed it decoded
  text chunks; it returns render ops (``("text", str, Style)``, ``("cr",)``,
  ``("nl",)``), buffering an incomplete escape sequence across ``feed`` calls.
  It maps a basic subset of ANSI SGR (8/16-colour foreground + bold + reset)
  to semantic colour names, leaving theme mapping to the widget.
* :class:`AnsiConsoleView` — a read-only, monospace, size-capped
  ``QPlainTextEdit`` that applies those ops. ``\r`` overwrites the current
  line (so progress bars don't flood the log); the block count is bounded so
  a long or newline-mode run can't grow the document without limit.
"""

from __future__ import annotations

from dataclasses import dataclass

from qtpy.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
)
from qtpy.QtWidgets import QPlainTextEdit

from percell4.gui import theme

_DEFAULT_MAX_BLOCKS = 5000

# ANSI SGR foreground code → semantic colour name.
_SGR_FG = {
    30: "black",
    31: "red",
    32: "green",
    33: "yellow",
    34: "blue",
    35: "magenta",
    36: "cyan",
    37: "white",
    39: None,
    90: "bright_black",
    91: "red",
    92: "green",
    93: "yellow",
    94: "blue",
    95: "magenta",
    96: "cyan",
    97: "white",
}


@dataclass(frozen=True)
class Style:
    """Immutable text style: a semantic foreground name (or None) + bold."""

    fg: str | None = None
    bold: bool = False


def _apply_sgr(style: Style, params: str) -> Style:
    if params == "":
        return Style()  # ESC[m is a reset
    fg, bold = style.fg, style.bold
    nums = []
    for part in params.split(";"):
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    for code in nums:
        if code == 0:
            fg, bold = None, False
        elif code == 1:
            bold = True
        elif code == 22:
            bold = False
        elif code in (38, 48):
            # 256-colour / truecolor — out of scope; ignore the rest.
            break
        elif code in _SGR_FG:
            fg = _SGR_FG[code]
    return Style(fg=fg, bold=bold)


class AnsiParser:
    """Stateful ANSI/CR parser. Feed decoded text; get render ops."""

    def __init__(self) -> None:
        self._pending = ""  # buffered incomplete escape (starts with ESC)
        self._style = Style()

    def feed(self, chunk: str) -> list[tuple]:
        ops: list[tuple] = []
        data = self._pending + chunk
        self._pending = ""
        n = len(data)
        i = 0
        text_start: int | None = None

        def flush(end: int) -> None:
            nonlocal text_start
            if text_start is not None and end > text_start:
                ops.append(("text", data[text_start:end], self._style))
            text_start = None

        while i < n:
            c = data[i]
            if c == "\x1b":
                flush(i)
                if i + 1 >= n:  # lone ESC at end → buffer
                    self._pending = data[i:]
                    return ops
                if data[i + 1] != "[":  # non-CSI escape → skip ESC + next
                    i += 2
                    continue
                j = i + 2
                while j < n and not ("\x40" <= data[j] <= "\x7e"):
                    j += 1
                if j >= n:  # terminator not yet arrived → buffer
                    self._pending = data[i:]
                    return ops
                if data[j] == "m":
                    self._style = _apply_sgr(self._style, data[i + 2 : j])
                # non-SGR CSI sequences are consumed and ignored
                i = j + 1
            elif c == "\r":
                flush(i)
                ops.append(("cr",))
                i += 1
            elif c == "\n":
                flush(i)
                ops.append(("nl",))
                i += 1
            else:
                if text_start is None:
                    text_start = i
                i += 1
        flush(n)
        return ops


# Semantic colour name → theme constant. Unmapped/None → theme.TEXT.
_FG_THEME = {
    "red": theme.ERROR,
    "green": theme.SUCCESS,
    "yellow": theme.WARNING,
    "blue": theme.ACCENT,
    "cyan": theme.ACCENT,
    "magenta": theme.ACCENT,
    "white": theme.TEXT_BRIGHT,
    "black": theme.TEXT_MUTED,
    "bright_black": theme.TEXT_MUTED,
}


class AnsiConsoleView(QPlainTextEdit):
    """Read-only streaming log: ANSI colour, ``\\r`` overwrite, size-capped."""

    def __init__(
        self, parent=None, max_blocks: int = _DEFAULT_MAX_BLOCKS
    ) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_blocks)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Menlo")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        self.setFont(font)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {theme.BACKGROUND_DEEP}; "
            f"color: {theme.TEXT}; border: 1px solid {theme.BORDER}; }}"
        )
        self._parser = AnsiParser()
        self._overwrite = False

    def append_output(self, text: str) -> None:
        ops = self._parser.feed(text)
        if not ops:
            return
        sb = self.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for op in ops:
            tag = op[0]
            if tag == "text":
                if self._overwrite:
                    cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                    cursor.removeSelectedText()
                    self._overwrite = False
                cursor.insertText(op[1], self._char_format(op[2]))
            elif tag == "cr":
                self._overwrite = True
            elif tag == "nl":
                cursor.insertText("\n")
                self._overwrite = False
        if at_bottom:
            sb.setValue(sb.maximum())

    def clear_output(self) -> None:
        self.clear()
        self._parser = AnsiParser()
        self._overwrite = False

    @staticmethod
    def _char_format(style: Style) -> QTextCharFormat:
        fmt = QTextCharFormat()
        color = _FG_THEME.get(style.fg, theme.TEXT) if style.fg else theme.TEXT
        fmt.setForeground(QColor(color))
        if style.bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        return fmt
