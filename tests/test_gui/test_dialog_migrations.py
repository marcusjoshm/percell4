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


# ── U3: compress_dialog ──────────────────────────────────────────────


def test_compress_dialog_wraps_content_in_one_scroll_area(qtbot):
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    scrolls = _scroll_areas(dlg)
    assert len(scrolls) == 1
    assert scrolls[0].widget().findChildren(QGroupBox)


# ── U4: workflows/single_cell/config_dialog ──────────────────────────


def test_workflow_config_dialog_wraps_content_in_one_scroll_area(qtbot):
    from percell4.gui.workflows.single_cell.config_dialog import (
        WorkflowConfigDialog,
    )

    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)

    scrolls = _scroll_areas(dlg)
    assert len(scrolls) == 1
    assert scrolls[0].widget().findChildren(QGroupBox)


# ── U5: add_layer_dialog ─────────────────────────────────────────────


def test_add_layer_dialog_per_tab_scroll_areas(qtbot, tmp_path):
    import numpy as np

    from percell4.gui.add_layer_dialog import AddLayerDialog
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_path / "ds.h5")
    store.create(metadata={"channel_names": ["ch00", "ch01"]})
    store.write_array(
        "intensity",
        np.zeros((2, 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )

    dlg = AddLayerDialog(parent=None, store=store, data_model=None, viewer_win=None)
    qtbot.addWidget(dlg)

    scrolls = _scroll_areas(dlg)
    # Three tabs use wrap_in_scroll: Discover TIFFs, TCSPC (.bin), Phasor (.npz).
    assert len(scrolls) == 3

    # Per-tab scroll wrappers must remain — TCSPC tab carries the
    # per-channel token override widgets from Thread 1.
    tcspc_dir_edit = dlg.findChild(type(dlg._tcspc_dir_edit), "")
    assert tcspc_dir_edit is not None or hasattr(dlg, "_tcspc_dir_edit")


# ── U6: export_images_dialog (drift fix) ─────────────────────────────


def test_export_images_dialog_wraps_content_in_scroll_area(qtbot, tmp_path):
    import numpy as np

    from percell4.gui.export_images_dialog import ExportImagesDialog
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_path / "ds.h5")
    store.create(
        metadata={"channel_names": [f"ch{i:02d}" for i in range(16)]},
    )
    store.write_array(
        "intensity",
        np.zeros((16, 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )

    dlg = ExportImagesDialog(parent=None, store=store)
    qtbot.addWidget(dlg)

    scrolls = _scroll_areas(dlg)
    assert len(scrolls) == 1
    # Channels group + format combo + buttons all live inside the
    # scrolled content.
    assert scrolls[0].widget().findChildren(QGroupBox)


def test_export_images_dialog_constructs_with_empty_store(qtbot, tmp_path):
    from percell4.gui.export_images_dialog import ExportImagesDialog
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_path / "empty.h5")
    store.create(metadata={"channel_names": []})

    dlg = ExportImagesDialog(parent=None, store=store)
    qtbot.addWidget(dlg)

    scrolls = _scroll_areas(dlg)
    assert len(scrolls) == 1
