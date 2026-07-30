"""Codebase invariant: every popup goes through the window helpers.

GNOME's mutter glues a popup to its parent when three conditions hold at
once: ``attach-modal-dialogs`` is on, the window is a DIALOG type carrying
``_NET_WM_STATE_MODAL``, and its ``WM_TRANSIENT_FOR`` parent is itself
NORMAL. An attached popup cannot be repositioned -- mutter rewrites its
position on every constraint pass -- so a launcher docked to a screen edge
pushed dialogs off-screen with no way to drag them back.

``percell4.gui._dialog_utils.detach_window`` breaks the second condition
and ``center_on_screen`` places the popup deliberately. A popup that
bypasses them silently reintroduces the bug, and no automated tier in this
repo can catch that at runtime: the offscreen platform has no window
manager, so nothing observes the attachment. This inspection test is the
only mechanised guard.

Because mutter also checks the *parent's* type, converting a dialog frees
every popup parented to it. That is why rule 3 below deliberately does not
apply inside the converted dialog classes -- roughly forty call sites in
those files are compliant precisely by being left alone.

Asserted by inspection, the same way ``test_settings_isolation_compliance.py``
asserts the single-QSettings invariant and ``test_dialog_helper_compliance.py``
asserts the scroll-wrapping convention.

Convention: ``docs/solutions/ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md``
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

#: Both GUI trees. The dialog-filename globs used by
#: ``test_dialog_helper_compliance.py`` see only twelve files and would miss
#: the launcher, the peer views and the workflow panels entirely.
_SCANNED = [
    _REPO / "src" / "percell4" / "gui",
    _REPO / "src" / "percell4" / "interfaces" / "gui",
]

#: The helpers' own home, plus this file, whose prose and self-check
#: literals necessarily spell out the forbidden shapes.
_HELPERS = _REPO / "src" / "percell4" / "gui" / "_dialog_utils.py"
_EXEMPT_FILES = {_HELPERS, Path(__file__).resolve()}

#: Path -> one-line reason. Empty by design, following ``EXEMPT_DIALOGS``'s
#: own comment: add an entry only when a detector below reports a site that
#: was deliberately left alone, never pre-emptively.
EXEMPT_SITES: dict[str, str] = {}

#: Either spelling satisfies the rules. ``make_freestanding`` is the
#: module's own documented pre-show triplet and calls ``detach_window``
#: internally, so requiring the inner name would report the recommended
#: helper as non-compliant.
_DETACH_NAMES = ("detach_window", "make_freestanding")

#: A popup static that has no handle to set window flags on before showing.
_POPUP_STATIC = re.compile(
    r"\bQ(?:MessageBox|InputDialog)\.(?:warning|information|critical|question"
    r"|about|getText|getItem|getInt|getDouble)\s*\("
)

#: File pickers. Assumed out of scope at first because they were thought to
#: resolve to the desktop portal; measured on GNOME/XWayland they fall back
#: to a Qt widget dialog that meets all three attach conditions and lands
#: off-screen. Same rule, same exemption for converted dialog modules.
_FILE_PICKER_STATIC = re.compile(
    r"\bQFileDialog\.(?:getOpenFileName|getOpenFileNames|getSaveFileName"
    r"|getExistingDirectory)\s*\("
)

def _py_files() -> list[Path]:
    files: list[Path] = []
    for root in _SCANNED:
        if root.is_dir():
            files.extend(
                p for p in root.rglob("*.py") if "__pycache__" not in p.parts
            )
    return sorted(files)


def _rel(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _scannable() -> list[Path]:
    return [
        p
        for p in _py_files()
        if p not in _EXEMPT_FILES and _rel(p) not in EXEMPT_SITES
    ]


def _calls_name(node: ast.AST, name: str) -> bool:
    """Whether ``name`` is called anywhere inside ``node``."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


def _calls_any(node: ast.AST, names) -> bool:
    return any(_calls_name(node, n) for n in names)


def _dialog_subclasses_missing_detach(source: str, rel: str) -> list[str]:
    """Rule 1: a ``QDialog`` subclass whose ``__init__`` forgets the helper."""
    hits: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {
            b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
            for b in node.bases
        }
        if "QDialog" not in bases:
            continue
        init = next(
            (
                f
                for f in node.body
                if isinstance(f, ast.FunctionDef) and f.name == "__init__"
            ),
            None,
        )
        if init is None or not _calls_any(init, _DETACH_NAMES):
            line = init.lineno if init is not None else node.lineno
            hits.append(f"{rel}:{line}: class {node.name}(QDialog) __init__")
    return hits


def _constructions_missing_detach(
    source: str, rel: str, *, nested_exempt: bool = False
) -> list[str]:
    """Rule 2: a directly built popup whose enclosing function skips the helper.

    Scoped to the enclosing function rather than the whole module: a module
    may legitimately build one popup through the helpers and another not.

    ``nested_exempt`` covers the transient popup classes inside a converted
    dialog module. Every one is parented to the converted dialog -- verified:
    all four ``QMessageBox(self)`` sites and all seven
    ``QProgressDialog(..., self)`` sites -- so their parent is UTILITY and
    mutter never attaches them. A nested ``QDialog`` is *never* exempt: it is
    a substantial form a user may want placed deliberately, so it still has
    to go through the helper.
    """
    built_types = {"QDialog"}
    if not nested_exempt:
        built_types.update(
            {"QProgressDialog", "QMessageBox", "QInputDialog", "QFileDialog"}
        )
    hits: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _calls_any(node, _DETACH_NAMES):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            built = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if built in built_types:
                hits.append(f"{rel}:{child.lineno}: {built}(...) in {node.name}()")
    return hits


def _converted_dialog_modules() -> set[str]:
    """Modules defining a converted ``QDialog`` subclass.

    Their nested popups are freed by the parent-type gate and must stay
    unedited, so rule 3 does not apply to them.
    """
    converted: set[str] = set()
    for path in _py_files():
        if path in _EXEMPT_FILES:
            continue
        source = path.read_text(encoding="utf-8")
        if not any(n in source for n in _DETACH_NAMES):
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                (b.id if isinstance(b, ast.Name) else getattr(b, "attr", ""))
                == "QDialog"
                for b in node.bases
            ):
                converted.add(_rel(path))
                break
    return converted


# ── Rule 1 ───────────────────────────────────────────────────────────


def test_every_dialog_subclass_detaches_its_window():
    offenders: list[str] = []
    for path in _scannable():
        offenders.extend(
            _dialog_subclasses_missing_detach(
                path.read_text(encoding="utf-8"), _rel(path)
            )
        )
    assert not offenders, (
        "Every QDialog subclass must call detach_window(self) in __init__ "
        "(and normally center_on_screen(self) too), before any show(). "
        "Without it GNOME glues the dialog to its parent and the user cannot "
        "drag it back on screen.\n" + "\n".join(f"  - {o}" for o in offenders)
    )


# ── Rule 2 ───────────────────────────────────────────────────────────


def test_directly_built_popups_detach_their_window():
    """Inside a converted dialog module the transient popups are exempt.

    Every nested ``QMessageBox`` and ``QProgressDialog`` in those files is
    parented to ``self`` -- a UTILITY window mutter never attaches. A nested
    ``QDialog`` stays checked, so an inline form surface cannot slip through
    just because its module happens to define a dialog subclass.
    """
    converted = _converted_dialog_modules()
    offenders: list[str] = []
    for path in _scannable():
        rel = _rel(path)
        offenders.extend(
            _constructions_missing_detach(
                path.read_text(encoding="utf-8"),
                rel,
                nested_exempt=rel in converted,
            )
        )
    assert not offenders, (
        "A directly constructed QDialog or QProgressDialog must be passed "
        "through detach_window() before it is shown -- or built via "
        "percell4.gui._dialog_utils.progress_dialog().\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


# ── Rule 3 ───────────────────────────────────────────────────────────


def test_no_popup_statics_outside_converted_dialogs():
    """Statics give no handle on which to set flags before the first show.

    Skipped inside the converted dialog classes: those popups inherit a
    UTILITY parent, so mutter never attaches them and editing them would
    contradict the design.
    """
    converted = _converted_dialog_modules()
    offenders: list[str] = []
    for path in _scannable():
        rel = _rel(path)
        if rel in converted:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _POPUP_STATIC.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Call percell4.gui._dialog_utils.message_box() or text_input() "
        "instead of a QMessageBox/QInputDialog static. A static builds, "
        "shows and destroys the popup in one call, leaving no moment at "
        "which its window type can be changed.\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


# ── Rule 4 ───────────────────────────────────────────────────────────


def test_no_file_picker_statics_outside_converted_dialogs():
    """File pickers are popups too, and Qt's widget fallback attaches.

    Measured: ``QFileDialog.getOpenFileName`` on a launcher docked to the
    right edge produced a DIALOG + MODAL + transient-for window whose right
    edge sat at 2045 on a 1920 screen. Use the ``_dialog_utils`` wrappers,
    which leave ``DontUseNativeDialog`` alone so a real portal still wins.
    """
    converted = _converted_dialog_modules()
    offenders: list[str] = []
    for path in _scannable():
        rel = _rel(path)
        if rel in converted:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _FILE_PICKER_STATIC.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Call percell4.gui._dialog_utils.open_file_name() / open_file_names() "
        "/ save_file_name() / existing_directory() instead of the QFileDialog "
        "static.\n" + "\n".join(f"  - {o}" for o in offenders)
    )


# ── Coverage and self-check ──────────────────────────────────────────


def test_scan_reaches_both_gui_trees():
    """The filename-glob approach this replaces missed an entire tree."""
    scanned = {_rel(p) for p in _py_files()}
    assert any(p.startswith("src/percell4/gui/") for p in scanned)
    assert any(p.startswith("src/percell4/interfaces/gui/") for p in scanned)
    assert "src/percell4/interfaces/gui/main_window.py" in scanned


def test_exemptions_are_real_paths():
    """An exemption for a moved or deleted file silently weakens the guard."""
    missing = [p for p in EXEMPT_SITES if not (_REPO / p).exists()]
    assert not missing, f"EXEMPT_SITES names paths that no longer exist: {missing}"


def test_detectors_fire_on_synthetic_offenders():
    """Self-check: each rule catches the bad shape and spares the good one.

    Without this, a typo in a pattern would pass the whole tree and the
    invariant would be unenforced while reading green.
    """
    bad_class = (
        "class Thing(QDialog):\n"
        "    def __init__(self, parent=None):\n"
        "        super().__init__(parent)\n"
    )
    good_class = (
        "class Thing(QDialog):\n"
        "    def __init__(self, parent=None):\n"
        "        super().__init__(parent)\n"
        "        detach_window(self)\n"
        "        center_on_screen(self)\n"
    )
    assert _dialog_subclasses_missing_detach(bad_class, "x.py")
    assert not _dialog_subclasses_missing_detach(good_class, "x.py")
    freestanding_class = (
        "class Thing(QDialog):\n"
        "    def __init__(self, parent=None):\n"
        "        super().__init__(parent)\n"
        "        make_freestanding(self)\n"
    )
    assert not _dialog_subclasses_missing_detach(freestanding_class, "x.py")

    bad_build = "def f(self):\n    d = QDialog(self)\n    d.exec_()\n"
    good_build = "def f(self):\n    d = QDialog(self)\n    detach_window(d)\n    d.exec_()\n"
    assert _constructions_missing_detach(bad_build, "x.py")
    assert not _constructions_missing_detach(good_build, "x.py")

    bad_progress = "def f(self):\n    p = QProgressDialog('x', None, 0, 1, self)\n"
    assert _constructions_missing_detach(bad_progress, "x.py")
    # Exempt inside a converted dialog module, but only for the transient
    # classes -- a nested QDialog form is still reported.
    assert not _constructions_missing_detach(
        bad_progress, "x.py", nested_exempt=True
    )
    bad_msgbox = "def f(self):\n    m = QMessageBox(self)\n    m.exec_()\n"
    assert _constructions_missing_detach(bad_msgbox, "x.py")
    assert not _constructions_missing_detach(bad_msgbox, "x.py", nested_exempt=True)
    assert _constructions_missing_detach(bad_build, "x.py", nested_exempt=True)

    assert _POPUP_STATIC.search('QMessageBox.warning(self, "t", "b")')
    assert _POPUP_STATIC.search("QMessageBox.question(self, t, b, btns)")
    assert _POPUP_STATIC.search('QInputDialog.getText(self, "t", "l")')
    # Shapes that must NOT trip the guard.
    assert not _POPUP_STATIC.search("from qtpy.QtWidgets import QMessageBox")
    assert not _POPUP_STATIC.search("buttons=QMessageBox.Yes | QMessageBox.No")
    assert not _POPUP_STATIC.search("if reply == QMessageBox.Cancel:")
    assert not _constructions_missing_detach(
        "def f() -> QDialog:\n    return None\n", "x.py"
    )

    assert _FILE_PICKER_STATIC.search('QFileDialog.getOpenFileName(self, "t")')
    assert _FILE_PICKER_STATIC.search("QFileDialog.getExistingDirectory(self)")
    assert _FILE_PICKER_STATIC.search("QFileDialog.getSaveFileName(self, c, d, f)")
    assert not _FILE_PICKER_STATIC.search("from qtpy.QtWidgets import QFileDialog")
    assert not _FILE_PICKER_STATIC.search("dialog.setOption(QFileDialog.ShowDirsOnly)")
