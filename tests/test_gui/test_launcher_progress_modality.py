"""The launcher's two progress dialogs keep their own modality.

``progress_dialog`` takes ``modality`` as a required keyword precisely
because these two differ: compression is window-modal, dataset loading is
application-modal. A single hardcoded default would silently downgrade the
loading dialog and break the promise that this change leaves modality alone
(see the plan's R3 and KTD5).

The helper's own tests prove it *honours* whichever modality it is handed.
These tests pin what each **call site** actually hands it -- the argument
slip those tests cannot see.

Convention: ``docs/solutions/ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md``
"""

from __future__ import annotations

import ast
from pathlib import Path

from qtpy.QtCore import Qt

_LAUNCHER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "percell4"
    / "interfaces"
    / "gui"
    / "main_window.py"
)


def _progress_dialog_calls() -> list[ast.Call]:
    """Every ``progress_dialog(...)`` call in the launcher."""
    tree = ast.parse(_LAUNCHER.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            node.func.id
            if isinstance(node.func, ast.Name)
            else getattr(node.func, "attr", "")
        )
        == "progress_dialog"
    ]


def _modality_arg(call: ast.Call) -> str | None:
    """The dotted source text of the call's ``modality`` keyword."""
    for kw in call.keywords:
        if kw.arg == "modality":
            return ast.unparse(kw.value)
    return None


def test_launcher_has_exactly_two_progress_dialogs():
    """A third one appearing should force a modality decision, not inherit one."""
    calls = _progress_dialog_calls()
    assert len(calls) == 2, (
        f"expected 2 progress_dialog call sites in main_window.py, found "
        f"{len(calls)} at lines {[c.lineno for c in calls]}"
    )


def test_every_launcher_progress_dialog_passes_modality_explicitly():
    """No call site may rely on a default -- the keyword is required."""
    missing = [
        call.lineno for call in _progress_dialog_calls() if _modality_arg(call) is None
    ]
    assert not missing, (
        "progress_dialog must be given an explicit modality= at every call "
        f"site; missing at main_window.py lines {missing}"
    )


def test_the_two_launcher_progress_dialogs_use_different_modalities():
    """The whole reason the keyword is required.

    Compression blocks its parent window; dataset loading blocks the whole
    application. If both call sites ever pass the same value, one of them has
    silently regressed.
    """
    modalities = sorted(
        _modality_arg(call) or "<missing>" for call in _progress_dialog_calls()
    )

    assert modalities == ["Qt.ApplicationModal", "Qt.WindowModal"], (
        "the launcher's compress dialog should be Qt.WindowModal and its "
        f"dataset-load dialog Qt.ApplicationModal; found {modalities}"
    )


def test_modality_names_resolve_to_real_qt_values():
    """Guard the string comparison above against a typo'd attribute.

    The AST checks compare source text, so they would happily accept
    ``Qt.WindowModel``. This pins that both names exist on Qt.
    """
    assert Qt.WindowModal != Qt.ApplicationModal
    assert getattr(Qt, "WindowModal", None) is not None
    assert getattr(Qt, "ApplicationModal", None) is not None
