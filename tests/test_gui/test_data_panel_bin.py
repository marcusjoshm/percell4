"""Tests for DataPanel bin display + layer-list [k=N] annotation (U10).

DataPanel shows native_shape and the session view bin in the info
label, and annotates each segmentation/mask combo item with [k=N] when
the underlying HDF5 dataset carries a ``created_at_bin`` attr.

DataPanel never writes session.active_bin -- that's the SessionWindow
SpinBox's responsibility (consolidate-canonical-state).
"""

from __future__ import annotations

import h5py
import numpy as np
import pytest
from qtpy.QtCore import Qt

from percell4.application.session import Session
from percell4.interfaces.gui.task_panels.data_panel import DataPanel
from percell4.model import CellDataModel
from percell4.store import DatasetStore


@pytest.fixture
def panel_with_real_store(qtbot, tmp_path):
    """Build a DataPanel backed by a real DatasetStore."""
    session = Session()
    model = CellDataModel(session=session)

    h5_path = tmp_path / "exp.h5"
    store = DatasetStore(h5_path)
    store.create(metadata={"channel_names": ["ch00"]})
    # Plant a real 32x32 intensity so native_shape inference works.
    store.write_array(
        "intensity",
        np.zeros((32, 32), dtype=np.uint16),
        attrs={"dims": ["H", "W"]},
    )

    from percell4.domain.dataset import DatasetHandle
    handle = DatasetHandle(
        path=h5_path,
        metadata={
            "channel_names": ["ch00"],
            "segmentation_names": [],
            "mask_names": [],
            "native_shape": (32, 32),
            "creation_bin": 1,
        },
    )
    session.set_dataset(handle)

    p = DataPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(h5_path),
    )
    qtbot.addWidget(p)
    return p, session, store


# ── Dataset info: bin lines ─────────────────────────────────────────


def test_info_label_shows_native_shape(panel_with_real_store):
    """The info label includes the dataset's native_shape from /metadata."""
    panel, _session, _store = panel_with_real_store
    panel.refresh_dataset_info()
    text = panel._info_label.text()
    assert "Native: (32, 32)" in text


def test_info_label_shows_creation_and_view_bin(panel_with_real_store):
    """Both creation_bin (from /metadata) and active_bin (from Session)
    appear on the info label."""
    panel, _session, _store = panel_with_real_store
    panel.refresh_dataset_info()
    text = panel._info_label.text()
    assert "Creation bin: 1" in text
    assert "View bin: 1" in text


def test_info_label_updates_on_active_bin_change(panel_with_real_store):
    """Toggling session.active_bin refreshes the info label via
    change.bin in _on_state_changed."""
    panel, session, _store = panel_with_real_store
    panel.refresh_dataset_info()
    assert "View bin: 1" in panel._info_label.text()

    session.set_active_bin(3)
    # _on_state_changed should have fired refresh_dataset_info.
    assert "View bin: 3" in panel._info_label.text()


# ── Layer-list annotation: [k=N] ────────────────────────────────────


def test_seg_combo_annotates_layer_with_created_at_bin(panel_with_real_store):
    """A segmentation written with created_at_bin=3 displays as 'name [k=3]'."""
    panel, _session, store = panel_with_real_store
    # Write a label and stamp a created_at_bin attr.
    labels = np.zeros((32, 32), dtype=np.int32)
    store.write_labels("cellpose_bin3", labels)
    with h5py.File(store.path, "a") as f:
        f["labels/cellpose_bin3"].attrs["created_at_bin"] = 3

    panel._refresh_seg_combos()
    items = [
        panel._mgmt_seg_combo.itemText(i)
        for i in range(panel._mgmt_seg_combo.count())
    ]
    assert any("[k=3]" in t for t in items)
    assert any("cellpose_bin3" in t for t in items)


def test_seg_combo_no_annotation_for_native_layer(panel_with_real_store):
    """A label without created_at_bin attr renders the clean name only."""
    panel, _session, store = panel_with_real_store
    labels = np.zeros((32, 32), dtype=np.int32)
    store.write_labels("cellpose", labels)
    # No created_at_bin attr set.

    panel._refresh_seg_combos()
    items = [
        panel._mgmt_seg_combo.itemText(i)
        for i in range(panel._mgmt_seg_combo.count())
    ]
    assert "cellpose" in items
    assert not any("[k=" in t for t in items)


def test_seg_combo_native_bin_one_no_annotation(panel_with_real_store):
    """A label with created_at_bin=1 (defensive case) shows no annotation."""
    panel, _session, store = panel_with_real_store
    labels = np.zeros((32, 32), dtype=np.int32)
    store.write_labels("cellpose", labels)
    with h5py.File(store.path, "a") as f:
        f["labels/cellpose"].attrs["created_at_bin"] = 1

    panel._refresh_seg_combos()
    items = [
        panel._mgmt_seg_combo.itemText(i)
        for i in range(panel._mgmt_seg_combo.count())
    ]
    assert "cellpose" in items
    assert not any("[k=" in t for t in items)


def test_mask_combo_annotates_layer_with_created_at_bin(panel_with_real_store):
    """Masks get the same annotation treatment as labels."""
    panel, _session, store = panel_with_real_store
    mask = np.zeros((32, 32), dtype=np.uint8)
    store.write_mask("phasor_bin2", mask)
    with h5py.File(store.path, "a") as f:
        f["masks/phasor_bin2"].attrs["created_at_bin"] = 2

    panel._refresh_mask_combos()
    items = [
        panel._mgmt_mask_combo.itemText(i)
        for i in range(panel._mgmt_mask_combo.count())
    ]
    assert any("[k=2]" in t for t in items)


def test_combo_user_role_stores_clean_name(panel_with_real_store):
    """The display text carries the [k=N] suffix, but Qt.UserRole carries
    the clean underlying HDF5 name -- so rename/delete handlers don't
    have to parse the annotation off."""
    panel, _session, store = panel_with_real_store
    labels = np.zeros((32, 32), dtype=np.int32)
    store.write_labels("cellpose_bin3", labels)
    with h5py.File(store.path, "a") as f:
        f["labels/cellpose_bin3"].attrs["created_at_bin"] = 3

    panel._refresh_seg_combos()
    for i in range(panel._mgmt_seg_combo.count()):
        if "cellpose_bin3" in panel._mgmt_seg_combo.itemText(i):
            clean = panel._mgmt_seg_combo.itemData(i, Qt.UserRole)
            assert clean == "cellpose_bin3"
            assert "[k=" not in clean
            return
    pytest.fail("cellpose_bin3 not found in combo")


def test_data_panel_never_writes_active_bin(panel_with_real_store):
    """DataPanel mirrors the bin display but never mutates session.active_bin.

    Audit the source: no call to session.set_active_bin should exist.
    """
    import inspect

    from percell4.interfaces.gui.task_panels import data_panel as dp_mod

    source = inspect.getsource(dp_mod)
    assert "set_active_bin" not in source, (
        "DataPanel must not write session.active_bin -- SessionWindow "
        "is the canonical Selector (consolidate-canonical-state)."
    )
