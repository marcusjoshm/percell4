"""Data tab → Session sync regression tests.

Deleting or renaming a mask/segmentation via the Layer Management section
must propagate to Session so peer views (SessionWindow's combos) update.
Previously the handlers mutated the HDF5 store and the napari viewer but
never called ``session.refresh_resource_lists(...)``, leaving Session's
``mask_names`` / ``segmentation_names`` stale and the corresponding
``MASK_LIST_CHANGED`` / ``SEGMENTATION_LIST_CHANGED`` events unfired.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.task_panels.data_panel import DataPanel
from percell4.model import CellDataModel


class FakeStore:
    """Minimal in-memory store that supports list/delete/rename operations
    the DataPanel handlers call. Tracks two namespaces — labels and masks —
    so we can drive both branches of ``_on_delete_layer`` / ``_on_rename_layer``.
    """

    def __init__(self, *, labels: list[str], masks: list[str]) -> None:
        self._labels = list(labels)
        self._masks = list(masks)

    def list_labels(self) -> list[str]:
        return list(self._labels)

    def list_masks(self) -> list[str]:
        return list(self._masks)

    def delete_item(self, path: str) -> None:
        prefix, name = path.split("/", 1)
        target = self._labels if prefix == "labels" else self._masks
        target.remove(name)

    def rename_item(self, old_path: str, new_path: str) -> None:
        prefix, old = old_path.split("/", 1)
        _, new = new_path.split("/", 1)
        target = self._labels if prefix == "labels" else self._masks
        target[target.index(old)] = new


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    """DataPanel wired to a real Session + CellDataModel and a FakeStore.

    Auto-confirms the QMessageBox deletion prompt so the handler proceeds.
    No real napari viewer — ``get_viewer_window`` returns ``None``.
    """
    session = Session()
    model = CellDataModel(session=session)
    h5 = tmp_path / "exp.h5"
    store = FakeStore(labels=["cp_masks", "manual"], masks=["mask_a", "mask_b"])
    handle = DatasetHandle(
        path=h5,
        metadata={
            "channel_names": ["CA-SiR", "mNG"],
            "segmentation_names": list(store.list_labels()),
            "mask_names": list(store.list_masks()),
        },
    )
    session.set_dataset(handle)

    # Auto-accept the QMessageBox.question confirmation in _on_delete_layer.
    from qtpy.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)

    p = DataPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(h5),
    )
    qtbot.addWidget(p)
    return p, session, store


# ── Delete propagation ────────────────────────────────────────────────


def test_delete_mask_emits_mask_list_changed(panel):
    p, session, store = panel
    fired: list[None] = []
    session.subscribe(Event.MASK_LIST_CHANGED, lambda: fired.append(None))

    p._mgmt_mask_combo.clear()
    p._mgmt_mask_combo.addItems(store.list_masks())
    p._mgmt_mask_combo.setCurrentText("mask_a")
    p._on_delete_layer("masks")

    assert fired, "MASK_LIST_CHANGED should fire after a mask deletion"
    assert "mask_a" not in session.dataset.metadata.get("mask_names", [])
    assert session.dataset.metadata["mask_names"] == ["mask_b"]


def test_delete_segmentation_emits_segmentation_list_changed(panel):
    p, session, store = panel
    fired: list[None] = []
    session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: fired.append(None))

    p._mgmt_seg_combo.clear()
    p._mgmt_seg_combo.addItems(store.list_labels())
    p._mgmt_seg_combo.setCurrentText("cp_masks")
    p._on_delete_layer("labels")

    assert fired, "SEGMENTATION_LIST_CHANGED should fire after a label deletion"
    assert session.dataset.metadata["segmentation_names"] == ["manual"]


def test_delete_active_mask_clears_active_pointer(panel):
    p, session, store = panel
    session.set_active_mask("mask_a")
    assert session.active_mask == "mask_a"

    p._mgmt_mask_combo.clear()
    p._mgmt_mask_combo.addItems(store.list_masks())
    p._mgmt_mask_combo.setCurrentText("mask_a")
    p._on_delete_layer("masks")

    assert session.active_mask is None


def test_delete_active_segmentation_clears_active_pointer(panel):
    p, session, store = panel
    session.set_active_segmentation("cp_masks")

    p._mgmt_seg_combo.clear()
    p._mgmt_seg_combo.addItems(store.list_labels())
    p._mgmt_seg_combo.setCurrentText("cp_masks")
    p._on_delete_layer("labels")

    assert session.active_segmentation is None


# ── Rename propagation ────────────────────────────────────────────────


def test_rename_mask_emits_mask_list_changed_and_follows_active(
    panel, monkeypatch
):
    p, session, store = panel
    session.set_active_mask("mask_a")
    fired: list[None] = []
    session.subscribe(Event.MASK_LIST_CHANGED, lambda: fired.append(None))

    # Bypass the QInputDialog by returning the new name directly.
    from qtpy.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("mask_a_renamed", True))

    p._mgmt_mask_combo.clear()
    p._mgmt_mask_combo.addItems(store.list_masks())
    p._mgmt_mask_combo.setCurrentText("mask_a")
    p._on_rename_layer("masks")

    assert fired, "MASK_LIST_CHANGED should fire after a mask rename"
    assert "mask_a_renamed" in session.dataset.metadata["mask_names"]
    assert "mask_a" not in session.dataset.metadata["mask_names"]
    assert session.active_mask == "mask_a_renamed"


def test_rename_segmentation_emits_segmentation_list_changed_and_follows_active(
    panel, monkeypatch
):
    p, session, store = panel
    session.set_active_segmentation("cp_masks")
    fired: list[None] = []
    session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: fired.append(None))

    from qtpy.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("cellpose_v2", True))

    p._mgmt_seg_combo.clear()
    p._mgmt_seg_combo.addItems(store.list_labels())
    p._mgmt_seg_combo.setCurrentText("cp_masks")
    p._on_rename_layer("labels")

    assert fired
    assert "cellpose_v2" in session.dataset.metadata["segmentation_names"]
    assert session.active_segmentation == "cellpose_v2"
