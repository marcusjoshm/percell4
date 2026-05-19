"""Regression tests for AddLayerDialog._write_layer Session sync.

Bug: ``_write_layer`` writes the new layer to the store and refreshes
the resource list, but does NOT call ``session.set_active_segmentation``
(or ``set_active_mask``) afterward. Result: the new layer shows up in
SessionWindow's combo (because the list event fires), but
``session.active_segmentation`` stays at its prior value (typically
``None``).

Downstream consumers that read ``data_model.active_segmentation``
get the empty-string coercion (``self._session.active_segmentation or ""``)
and the panel reports ``Segmentation '' not found in viewer``.

This contract is established for every other Creator in the codebase:
``segment_cells.finalize``, the ROI import tab (line 641), and the
Cellpose ``_seg.npy`` import tab (line 707) all call
``set_active_segmentation(name)`` after writing. Only ``_write_layer``
violated the convention.

The fix adds the missing ``set_active_segmentation`` / ``set_active_mask``
calls in the corresponding branches of ``_write_layer``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from qtpy.QtWidgets import QWidget

from percell4.application.session import Event, Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.add_layer_dialog import AddLayerDialog
from percell4.model import CellDataModel
from percell4.store import DatasetStore


@pytest.fixture
def dialog(qtbot, tmp_path):
    """Fresh .h5 + AddLayerDialog wired to a real Session + CellDataModel."""
    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch0"]})
    intensity = np.zeros((8, 8), dtype=np.float32)
    store.write_array("intensity", intensity, attrs={"dims": ["H", "W"]})

    session = Session()
    session.set_dataset(
        DatasetHandle(path=path, metadata={"channel_names": ["ch0"]})
    )
    data_model = CellDataModel(session)

    launcher = QWidget()
    qtbot.addWidget(launcher)
    dlg = AddLayerDialog(launcher, store, data_model, viewer_win=None)
    qtbot.addWidget(dlg)
    return dlg, session


# ── Segmentation branch ─────────────────────────────────────────────────


def test_write_layer_segmentation_sets_active_segmentation(dialog):
    """After _write_layer for a Segmentation, session.active_segmentation
    is set to the new layer's name -- so downstream panels reading
    session.active_segmentation see the value they expect."""
    dlg, session = dialog
    assert session.active_segmentation is None  # baseline

    labels = np.zeros((8, 8), dtype=np.int32)
    dlg._write_layer("my_seg", "Segmentation", labels)

    assert session.active_segmentation == "my_seg", (
        "session.active_segmentation should be set after Add Layer; "
        "otherwise downstream panels read '' and report "
        "\"Segmentation '' not found in viewer\""
    )


def test_write_layer_segmentation_emits_active_segmentation_changed(dialog):
    """ACTIVE_SEGMENTATION_CHANGED fires so the bridge -> StateChange.segmentation
    chain runs and dependent panels refresh."""
    dlg, session = dialog
    events: list[int] = []
    session.subscribe(Event.ACTIVE_SEGMENTATION_CHANGED, lambda: events.append(1))

    labels = np.zeros((8, 8), dtype=np.int32)
    dlg._write_layer("my_seg", "Segmentation", labels)

    assert events == [1], (
        "Exactly one ACTIVE_SEGMENTATION_CHANGED event should fire when "
        "Add Layer creates a new segmentation"
    )


def test_write_layer_segmentation_still_refreshes_resource_list(dialog):
    """The fix must NOT break the existing resource-list refresh -- both
    list-event AND active-event should fire."""
    dlg, session = dialog
    list_events: list[int] = []
    active_events: list[int] = []
    session.subscribe(Event.SEGMENTATION_LIST_CHANGED, lambda: list_events.append(1))
    session.subscribe(Event.ACTIVE_SEGMENTATION_CHANGED, lambda: active_events.append(1))

    labels = np.zeros((8, 8), dtype=np.int32)
    dlg._write_layer("my_seg", "Segmentation", labels)

    assert list_events, "SEGMENTATION_LIST_CHANGED must still fire"
    assert active_events, "ACTIVE_SEGMENTATION_CHANGED must also fire"


# ── Mask branch ────────────────────────────────────────────────────────


def test_write_layer_mask_sets_active_mask(dialog):
    """Same convention as segmentation: writing a mask via Add Layer
    auto-selects it. Pre-fix this was the latent twin of the
    segmentation bug -- a different downstream panel reading
    session.active_mask would have hit the same empty-string trap."""
    dlg, session = dialog
    assert session.active_mask is None

    mask = np.zeros((8, 8), dtype=np.uint8)
    dlg._write_layer("my_mask", "Mask", mask)

    assert session.active_mask == "my_mask"


def test_write_layer_mask_emits_active_mask_changed(dialog):
    """ACTIVE_MASK_CHANGED fires for the same reason as segmentation."""
    dlg, session = dialog
    events: list[int] = []
    session.subscribe(Event.ACTIVE_MASK_CHANGED, lambda: events.append(1))

    mask = np.zeros((8, 8), dtype=np.uint8)
    dlg._write_layer("my_mask", "Mask", mask)

    assert events == [1]


# ── Channel branch (explicit non-regression) ───────────────────────────


def test_write_layer_channel_does_not_set_active_channel(dialog):
    """Adding a new channel via Add Layer does NOT auto-select it.

    Pins the asymmetric scope: segmentations and masks auto-select on
    add (the user almost always wants to operate on the new one);
    channels do NOT auto-select (the user often has a specific channel
    active for a reason and adding an ancillary channel shouldn't
    yank their focus). This matches the existing behavior of the
    Channel branch at add_layer_dialog.py:720-749 which only updates
    channel_names, never set_active_channel."""
    dlg, session = dialog
    # Seed an existing active channel.
    session.set_active_channel("ch0")

    array = np.zeros((8, 8), dtype=np.float32)
    dlg._write_layer("ch1", "Channel", array)

    assert session.active_channel == "ch0", (
        "Adding a new channel should not change the active channel"
    )
