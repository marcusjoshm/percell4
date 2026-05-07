"""Regression tests for Manual Editing auto-save (segmentation_panel.py).

Bug: edits made via the Manual Editing buttons (Delete Selected Label,
Add New Label, Clean Up Labels, Relabel Sequential) and via napari's own
brush/erase tools modified ``labels_layer.data`` only — they did not
write to HDF5. Closing and reloading the dataset silently lost every
edit unless the user clicked the separate "Save Labels to HDF5" button.

Fix: every button handler now calls ``_persist_labels_layer`` after its
in-memory write, and napari ``Labels.events.paint`` strokes are wired to
a debounced HDF5 flush. These tests round-trip a delete through the
real ``DatasetStore`` to prove the on-disk file actually reflects the
edit.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.gui.segmentation_panel import SegmentationPanel
from percell4.model import CellDataModel
from percell4.store import DatasetStore


def _make_fake_layer(name: str, data: np.ndarray, selected_label: int = 0):
    """Produce a minimal stand-in for ``napari.layers.Labels``.

    Only the attributes that ``SegmentationPanel`` reaches for are populated.
    We intentionally avoid spinning up a real napari viewer here — these
    tests verify the persistence wiring, not napari semantics.
    """
    layer = SimpleNamespace(
        name=name,
        data=data,
        selected_label=selected_label,
    )
    layer.refresh = lambda: None
    return layer


class _FakeLayerList(list):
    """Minimal stand-in for ``napari.viewer.layers``.

    Inherits list iteration so ``for layer in viewer.layers`` works, and
    exposes a ``selection`` attribute holding the active layer.
    """

    def __init__(self, layers):
        super().__init__(layers)
        self.selection = SimpleNamespace(active=layers[0] if layers else None)
        self.events = SimpleNamespace(inserted=SimpleNamespace(connect=lambda _f: None))


def _make_fake_launcher(store: DatasetStore, layer):
    """Mimic the parts of ``LauncherWindow`` that ``SegmentationPanel`` uses."""
    fake_viewer = SimpleNamespace(layers=_FakeLayerList([layer]))
    viewer_win = SimpleNamespace(viewer=fake_viewer)

    status_messages: list[str] = []
    fake_status_bar = SimpleNamespace(
        showMessage=lambda msg: status_messages.append(msg)
    )

    launcher = SimpleNamespace(
        _current_store=store,
        _windows={"viewer": viewer_win},
        statusBar=lambda: fake_status_bar,
    )
    launcher._status_messages = status_messages
    return launcher


@pytest.fixture
def store_with_labels(tmp_path):
    """Real DatasetStore pre-populated with a 'manual' labels array."""
    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    initial = np.zeros((20, 20), dtype=np.int32)
    initial[2:6, 2:6] = 1
    initial[10:14, 10:14] = 2
    store.write_labels("manual", initial)
    return store


def test_persist_labels_layer_writes_to_hdf5(qtbot, store_with_labels):
    """Direct-helper test: ``_persist_labels_layer`` round-trips through HDF5."""
    session = Session()
    model = CellDataModel(session=session)

    layer_data = np.zeros((20, 20), dtype=np.int32)
    layer_data[5:8, 5:8] = 7  # arbitrary non-empty edit
    layer = _make_fake_layer("manual", layer_data)
    launcher = _make_fake_launcher(store_with_labels, layer)

    panel = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(panel)

    ok = panel._persist_labels_layer(layer)
    assert ok is True

    # Reload from disk to prove persistence.
    on_disk = store_with_labels.read_labels("manual")
    np.testing.assert_array_equal(on_disk, layer_data)


def test_delete_selected_label_persists_round_trip(qtbot, store_with_labels):
    """Click 'Delete Selected Label' → close dataset → reload → edit is gone.

    This is the headline regression: before the fix, the in-memory layer
    would show label 1 deleted, but reopening the .h5 file would still
    show it because no write to HDF5 ever happened.
    """
    session = Session()
    model = CellDataModel(session=session)

    # Start from the on-disk state (mimics a freshly loaded dataset).
    initial = store_with_labels.read_labels("manual")
    layer = _make_fake_layer("manual", initial.copy(), selected_label=1)
    launcher = _make_fake_launcher(store_with_labels, layer)

    panel = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(panel)
    # The panel's resolver uses ``isinstance(layer, napari.layers.Labels)``
    # for safety; our fake layer doesn't pass that check. Stub the resolver
    # since the napari type-matching logic is tested elsewhere — here we
    # only care that mutation + persistence wiring works end-to-end.
    panel._get_active_labels_layer = lambda: layer

    panel._on_delete_selected_label()

    # In-memory layer reflects the delete.
    assert int(layer.data.max()) == 2  # label 1 gone, label 2 still there
    assert not (layer.data == 1).any()

    # On-disk reload reflects the delete — this is the bug we're fixing.
    on_disk = store_with_labels.read_labels("manual")
    assert not (on_disk == 1).any()
    assert (on_disk == 2).any()


def test_relabel_sequential_persists(qtbot, tmp_path):
    """'Clean Up Labels (relabel sequential)' must round-trip through HDF5."""
    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    # Non-sequential labels: 1, 5, 9.
    arr = np.zeros((10, 10), dtype=np.int32)
    arr[0:3, 0:3] = 1
    arr[3:6, 3:6] = 5
    arr[6:9, 6:9] = 9
    store.write_labels("manual", arr)

    session = Session()
    model = CellDataModel(session=session)
    layer = _make_fake_layer("manual", arr.copy())
    launcher = _make_fake_launcher(store, layer)

    panel = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(panel)
    panel._get_active_labels_layer = lambda: layer

    panel._on_relabel_sequential()

    on_disk = store.read_labels("manual")
    # Sequential renumbering: max should drop from 9 to 3.
    assert int(on_disk.max()) == 3
    np.testing.assert_array_equal(on_disk, layer.data)


def test_persist_is_noop_with_no_store(qtbot):
    """If no dataset is loaded, the helper must not raise."""
    session = Session()
    model = CellDataModel(session=session)

    layer = _make_fake_layer("manual", np.zeros((4, 4), dtype=np.int32))
    launcher = SimpleNamespace(
        _current_store=None,
        _windows={},
        statusBar=lambda: SimpleNamespace(showMessage=lambda _msg: None),
    )

    panel = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(panel)

    # Must return False but never raise.
    assert panel._persist_labels_layer(layer) is False
