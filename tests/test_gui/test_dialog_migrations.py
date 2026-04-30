"""Smoke tests verifying each migrated dialog instantiates and contains
exactly one ``QScrollArea`` whose widget is the dialog's primary content.

Each test is enabled by the corresponding U_n migration in
``docs/plans/2026-04-30-refactor-dialog-scroll-helper-rollout-plan.md``.
"""

from __future__ import annotations

from qtpy.QtWidgets import QGroupBox, QScrollArea


def _scroll_areas(widget) -> list[QScrollArea]:
    return widget.findChildren(QScrollArea)


# ── U2: import_dialog ────────────────────────────────────────────────


def test_import_dialog_wraps_content_in_one_scroll_area(qtbot):
    from percell4.gui.import_dialog import ImportDialog

    dlg = ImportDialog()
    qtbot.addWidget(dlg)

    scrolls = _scroll_areas(dlg)
    assert len(scrolls) == 1
    assert isinstance(scrolls[0].widget(), type(scrolls[0].widget()))
    assert scrolls[0].widget().findChildren(QGroupBox)
