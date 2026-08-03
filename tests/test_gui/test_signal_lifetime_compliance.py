"""Codebase invariants for Qt signal connections that outlive their receiver.

Two connection shapes crashed CI, both silently and both only under PyQt5 —
the binding CI runs — while PySide either raised a catchable RuntimeError or
swallowed the problem entirely. Neither had a natural unit-test home, so they
are asserted here by inspection, the same way ``test_qt_free_imports.py``
asserts the hexagonal import boundary.

Mirrors the style of ``tests/test_gui/test_dialog_helper_compliance.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "percell4"

#: ``destroyed`` connected to a lambda. Qt breaks a connection automatically
#: when the receiver QObject dies, but only when it can identify a receiver —
#: a lambda has none. The connection then outlives the object that owns the
#: widgets the callback touches, and firing it reads freed C++ memory:
#: RuntimeError under PySide, a hard segfault under PyQt5. Because
#: ``destroyed`` fires during garbage collection, the crash lands on whatever
#: test happens to be running, which is why CI died at a different test each
#: run. Connect a bound method instead.
_DESTROYED_LAMBDA = re.compile(r"\.destroyed\.connect\(\s*lambda")

#: A payload-carrying signal wired straight into a zero-argument signal's
#: ``.emit``. ``currentIndexChanged`` passes an int and ``valueChanged`` an int
#: or float, so PyQt5 raises "only accepts 0 argument(s), 1 given" at emit
#: time while PySide silently discards the extra argument. Route through a
#: small ``*_args``-absorbing slot instead.
_ARGFUL_INTO_EMIT = re.compile(
    r"\.(currentIndexChanged|valueChanged|currentTextChanged|textChanged"
    r"|toggled|stateChanged)\.connect\(\s*self\.[A-Za-z_]+\.emit\s*\)"
)


def _py_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for path in _py_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.search(line):
                hits.append(f"{path.relative_to(SRC.parents[1])}:{lineno}: {line.strip()}")
    return hits


def test_destroyed_is_never_connected_to_a_lambda() -> None:
    """A ``destroyed`` connection must be lifetime-tied to its receiver."""
    offenders = _offenders(_DESTROYED_LAMBDA)
    assert not offenders, (
        "destroyed.connect(lambda ...) cannot be auto-disconnected by Qt when "
        "the receiver is destroyed, so it fires into freed widgets and "
        "segfaults under PyQt5. Connect a bound method of the receiver "
        "instead.\n  " + "\n  ".join(offenders)
    )


def test_argful_signals_are_not_wired_into_zero_arg_emit() -> None:
    """A payload-carrying signal must not drive a zero-arg signal's emit."""
    offenders = _offenders(_ARGFUL_INTO_EMIT)
    assert not offenders, (
        "This raises 'only accepts 0 argument(s), 1 given' under PyQt5 (the "
        "binding CI runs) while PySide drops the argument silently. Route "
        "through a slot that absorbs *_args and re-emits.\n  "
        + "\n  ".join(offenders)
    )


def test_patterns_actually_match_their_target_shapes() -> None:
    """Guard the guards — a regex typo would make both tests vacuous."""
    assert _DESTROYED_LAMBDA.search(
        "self._win.destroyed.connect(lambda *_: self._refresh())"
    )
    assert not _DESTROYED_LAMBDA.search(
        "self._win.destroyed.connect(self._on_destroyed)"
    )
    assert _ARGFUL_INTO_EMIT.search(
        "self._combo.currentIndexChanged.connect(self.config_changed.emit)"
    )
    assert not _ARGFUL_INTO_EMIT.search(
        "self._combo.currentIndexChanged.connect(self._emit_config_changed)"
    )
