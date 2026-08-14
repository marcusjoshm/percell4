"""Compliance test: every dialog under ``percell4.gui`` either uses the
shared ``_dialog_utils.wrap_in_scroll`` helper or carries an explicit
exemption with a one-line reason.

See ``docs/solutions/ui-bugs/dialog-scroll-when-tall.md`` for the
convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import percell4

GUI_ROOT = Path(percell4.__file__).parent / "gui"

EXEMPT_DIALOGS: dict[str, str] = {
    # path-relative-to-gui-root: one-line reason
    # Empty until a real exemption is justified.
}


def _iter_dialog_files() -> list[Path]:
    files: list[Path] = []
    files.extend(GUI_ROOT.rglob("*Dialog.py"))
    files.extend(GUI_ROOT.rglob("*_dialog.py"))
    return sorted(set(files))


def _calls_helper(source: str, helper: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == helper:
                return True
            if isinstance(func, ast.Attribute) and func.attr == helper:
                return True
    return False


def test_every_dialog_uses_wrap_in_scroll_or_is_exempt():
    failures: list[str] = []
    for path in _iter_dialog_files():
        rel = path.relative_to(GUI_ROOT).as_posix()
        if rel in EXEMPT_DIALOGS:
            continue
        source = path.read_text()
        if not _calls_helper(source, "wrap_in_scroll"):
            failures.append(rel)
    assert not failures, (
        "Dialog files missing wrap_in_scroll() and not in EXEMPT_DIALOGS:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_compliance_detection_logic():
    # Sanity: AST walker should detect both Name and Attribute calls.
    src_name = "wrap_in_scroll(content)"
    src_attr = "utils.wrap_in_scroll(content)"
    src_none = "QVBoxLayout(self).addWidget(content)"

    assert _calls_helper(src_name, "wrap_in_scroll") is True
    assert _calls_helper(src_attr, "wrap_in_scroll") is True
    assert _calls_helper(src_none, "wrap_in_scroll") is False
