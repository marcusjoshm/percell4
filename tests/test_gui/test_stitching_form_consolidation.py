"""One canonical stitching widget, enforced (U6).

Goal 3 of this refactor: every GUI surface that stitches tiles uses the same
component. That is easy to state and easy to erode — the previous shared widget
was documented as canonical and still ended up with four divergent copies,
because nothing failed when someone rebuilt the controls inline.

These tests fail when that starts happening again.

Every check here is STATIC (filesystem + AST). Nothing constructs a Qt widget:
adding more dialog instances to the suite destabilizes Qt teardown in this venv
and aborted the full-suite run. The behavioural counterparts live in each
surface's own test module, one dialog per test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import percell4

GUI_ROOT = Path(percell4.__file__).parent / "gui"

# The file that is ALLOWED to construct stitching grid controls.
CANONICAL = "_stitching_form.py"

# Vocabulary that only appears where the combos are built.
GRID_TYPE_VALUES = {"row_by_row", "column_by_column", "snake_by_row", "snake_by_column"}

# Surfaces not yet migrated, with the reason. Mirrors the EXEMPT_DIALOGS
# convention in test_dialog_helper_compliance.py. Empty, and it must stay that
# way: a new entry here means someone hand-rolled stitching controls again.
PENDING_MIGRATION: dict[str, str] = {}


def _gui_sources() -> list[Path]:
    return sorted(p for p in GUI_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_retired_composite_is_gone_and_unimported() -> None:
    """StitchingFlimForm was the transitional composite. Nothing may import it.

    A stray re-import would resurrect the two-construction-sites problem.
    """
    assert not (GUI_ROOT / "_stitching_flim_form.py").exists()

    offenders = [
        p.relative_to(GUI_ROOT).as_posix()
        for p in _gui_sources()
        if "_stitching_flim_form" in p.read_text() and p.name != "_flim_bin_form.py"
    ]
    assert not offenders, f"still referencing the retired composite: {offenders}"


def test_only_the_canonical_form_builds_grid_type_controls() -> None:
    """No dialog may rebuild the Type/Order vocabulary inline.

    Matching on the grid-type string set rather than on widget calls, because
    the drift always shows up as a hand-rolled item list.
    """
    offenders = []
    for path in _gui_sources():
        if path.name in (CANONICAL, "_stitch_order.py"):
            continue
        if path.name in PENDING_MIGRATION:
            continue
        literals = {
            node.value
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if len(GRID_TYPE_VALUES & literals) >= 2:
            offenders.append(path.relative_to(GUI_ROOT).as_posix())

    assert not offenders, (
        "these files build stitching controls instead of using StitchingForm: "
        f"{offenders}"
    )


def test_canonical_form_is_imported_by_every_migrated_surface() -> None:
    """Each surface must import StitchingForm, checked statically.

    Deliberately NOT by constructing the dialogs here. Adding dialog instances
    to the suite from this module destabilized Qt teardown in this venv and
    aborted the full-suite run (the "contain Qt cleanup within each test"
    precedent). The per-dialog isinstance assertions live in each surface's own
    test module instead: test_compress_dialog_stitching_form.py,
    test_add_layer_stitching_form.py, and test_batch_tcspc_dialog.py.
    """
    expected = {
        "compress_dialog.py",
        "add_layer_dialog.py",
        "batch_tcspc_dialog.py",
        "import_dialog.py",
    } - set(PENDING_MIGRATION)

    importers = {
        p.name
        for p in _gui_sources()
        if "from percell4.gui._stitching_form import StitchingForm" in p.read_text()
    }
    missing = expected - importers
    assert not missing, f"these surfaces do not use the canonical form: {missing}"


def test_no_surface_is_pending_migration() -> None:
    """Consolidation is complete. This is not a formality: the exemption list
    is the escape hatch, so an entry appearing here is the signal that the
    single-construction-site invariant has been given up on."""
    assert PENDING_MIGRATION == {}, (
        f"stitching controls still hand-rolled in: {sorted(PENDING_MIGRATION)}"
    )
