"""Every progress dialog's modality matches what its run loop needs.

``QProgressDialog.setValue()`` pumps the event loop **only when
``isModal()``**. So a run loop that polls ``wasCanceled()`` without calling
``processEvents()`` itself depends on modality for its own cancellation:
make it non-modal and the bar stops repainting and Cancel becomes
unreachable. A loop that pumps events itself, or hands the work to a
Worker thread, has no such dependency and can be non-modal -- which is
what keeps it from becoming a glued ``NSWindow`` sheet on macOS.

That split is easy to get backwards, and getting it backwards fails
silently: the dialog still appears, it just never responds to Cancel. This
file pins it by inspection, because no offscreen test can observe a sheet
or a frozen Cancel button.

Convention: ``docs/solutions/ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md``
"""

from __future__ import annotations

import ast
from pathlib import Path

from qtpy.QtCore import Qt

_SRC = Path(__file__).resolve().parents[2] / "src" / "percell4"

#: Every progress dialog in the GUI, and the function whose loop drives it.
_SITES = {
    "gui/phasor_masks_dialog.py": "_on_start_clicked",
    "gui/per_particle_multichannel_dialog.py": "_on_start_clicked",
    "gui/dilute_from_mask_dialog.py": "_on_start_clicked",
    "gui/per_particle_donut_dialog.py": "_on_start_clicked",
    "gui/whole_field_intensity_dialog.py": "_on_start_clicked",
    "gui/workflows/single_cell/seg_qc.py": "_on_rerun_clicked",
    "gui/flim_fret_dialog.py": "_on_start_clicked",
    "gui/batch_tcspc_dialog.py": "_on_run",
    "interfaces/gui/main_window.py": "_run_batch_compress",
}

#: The helper that resolves modality per platform for pump-dependent loops.
_HELPER = "blocking_progress_modality"


def _function_source(rel: str, fn: str) -> str:
    tree = ast.parse((_SRC / rel).read_text(encoding="utf-8"))
    node = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn),
        None,
    )
    assert node is not None, f"{rel} has no function {fn}()"
    return ast.unparse(node)


def _classify(rel: str, fn: str) -> str:
    """``pump-dependent`` when the loop polls cancel but never pumps."""
    body = _function_source(rel, fn)
    if "wasCanceled" not in body:
        return "independent"
    return "independent" if "processEvents" in body else "pump-dependent"


def _modality_source(rel: str, fn: str) -> str:
    """The modality expression this run function actually sets."""
    body = _function_source(rel, fn)
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", "")
            )
            if name == "setWindowModality" and node.args:
                return ast.unparse(node.args[0])
            if name == "progress_dialog":
                for kw in node.keywords:
                    if kw.arg == "modality":
                        return ast.unparse(kw.value)
    return "<none>"


def test_every_progress_dialog_sets_modality_explicitly():
    missing = [
        rel for rel, fn in _SITES.items() if _modality_source(rel, fn) == "<none>"
    ]
    assert not missing, (
        "every progress dialog must set its modality explicitly; missing in "
        f"{missing}"
    )


def test_pump_dependent_loops_are_never_non_modal():
    """The silent-failure guard.

    These loops poll ``wasCanceled()`` and never pump events themselves, so
    ``Qt.NonModal`` would leave Cancel permanently unresponsive during the
    longest-running operations in the app.
    """
    offenders = []
    for rel, fn in _SITES.items():
        if _classify(rel, fn) != "pump-dependent":
            continue
        modality = _modality_source(rel, fn)
        if "NonModal" in modality or _HELPER not in modality:
            offenders.append(f"{rel}::{fn}() -> {modality}")
    assert not offenders, (
        f"a run loop that polls wasCanceled() without processEvents() must use "
        f"{_HELPER}(), which never returns NonModal:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_self_pumping_loops_are_non_modal():
    """The macOS-sheet guard.

    A loop that pumps events itself, or uses a Worker thread, gains nothing
    from modality -- and on macOS a parented window-modal dialog becomes a
    sheet glued to its parent. These disable their form controls instead.
    """
    offenders = []
    for rel, fn in _SITES.items():
        if _classify(rel, fn) != "independent":
            continue
        modality = _modality_source(rel, fn)
        if "NonModal" not in modality:
            offenders.append(f"{rel}::{fn}() -> {modality}")
    assert not offenders, (
        "a self-pumping or worker-thread run loop should be Qt.NonModal so it "
        "does not become a glued NSWindow sheet on macOS:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_the_split_is_not_degenerate():
    """Both buckets must be non-empty.

    If a refactor accidentally put every dialog in one bucket, the two tests
    above would still pass while asserting nothing about the other.
    """
    kinds = [_classify(rel, fn) for rel, fn in _SITES.items()]

    assert kinds.count("pump-dependent") == 3, (
        f"expected 3 pump-dependent progress loops, found "
        f"{kinds.count('pump-dependent')}"
    )
    assert kinds.count("independent") == 6, (
        f"expected 6 self-pumping/worker progress loops, found "
        f"{kinds.count('independent')}"
    )


def test_dataset_load_dialog_stays_application_modal():
    """The launcher's other progress dialog, which is not in the table above.

    It blocks the whole app during load by design; on macOS
    ApplicationModal routes through beginModalSession, so it is a window
    rather than a sheet already.
    """
    src = (_SRC / "interfaces/gui/main_window.py").read_text(encoding="utf-8")

    assert "modality=Qt.ApplicationModal" in src


def test_modality_names_resolve_to_real_qt_values():
    """Guards the source-text comparisons above against a typo'd attribute."""
    assert Qt.NonModal != Qt.WindowModal != Qt.ApplicationModal
    for name in ("NonModal", "WindowModal", "ApplicationModal"):
        assert getattr(Qt, name, None) is not None
