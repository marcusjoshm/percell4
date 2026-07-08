"""Regression test: Create Empty Labels Layer must refresh the resource list.

Bug: clicking "Create Empty Labels Layer" in the Segment tab persists the new
``manual`` segmentation to HDF5 but never calls
``Session.refresh_resource_lists(segmentation_names=...)``. SessionWindow's
segmentation combo subscribes to ``SEGMENTATION_LIST_CHANGED``, so it never
learns about the new segmentation — the user has to close and reopen the
dataset to see it. The fix mirrors the canonical Creator pattern at
``application/use_cases/segment_cells.py:209``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from percell4.application.session import Event, Session
from percell4.gui.segmentation_panel import SegmentationPanel
from percell4.model import CellDataModel
from percell4.store import DatasetStore


class _FakeLayerList(list):
    def __init__(self, layers):
        super().__init__(layers)
        self.selection = SimpleNamespace(active=layers[0] if layers else None)
        self.events = SimpleNamespace(
            inserted=SimpleNamespace(connect=lambda _f: None)
        )


def _make_fake_layer(name: str, data: np.ndarray):
    layer = SimpleNamespace(name=name, data=data, selected_label=0)
    layer.refresh = lambda: None
    return layer


class _FakeImageLayer:
    """A duck-typed stand-in for napari.layers.Image.

    ``_get_image_shape`` filters via ``layer.__class__.__name__ == "Image"``,
    so the class name must match exactly.
    """

    def __init__(self, name: str, data: np.ndarray) -> None:
        self.name = name
        self.data = data


# Patch the class name to satisfy _get_image_shape's name-based check.
_FakeImageLayer.__name__ = "Image"


def _make_fake_launcher(store: DatasetStore, image_shape=(10, 10)):
    """Build the minimum launcher / viewer surface ``_on_create_empty_labels`` reaches for."""

    image_layer = _FakeImageLayer("ch0", np.zeros(image_shape, dtype=np.float32))

    added_layers: list[Any] = []

    fake_viewer = SimpleNamespace(layers=_FakeLayerList([image_layer]))
    fake_viewer.dims = SimpleNamespace(ndim=2, current_step=())

    def fake_add_labels(arr, name):
        labels_layer = _make_fake_layer(name, arr)
        # When add_labels is called, the new labels layer becomes part of
        # the viewer (just like napari).
        fake_viewer.layers.append(labels_layer)
        added_layers.append(labels_layer)

    viewer_win = SimpleNamespace(viewer=fake_viewer, add_labels=fake_add_labels)
    status_messages: list[str] = []
    fake_status_bar = SimpleNamespace(
        showMessage=lambda msg: status_messages.append(msg)
    )
    launcher = SimpleNamespace(
        _current_store=store,
        _windows={"viewer": viewer_win},
        statusBar=lambda: fake_status_bar,
    )
    launcher._added_layers = added_layers
    return launcher


def test_create_empty_labels_calls_refresh_resource_lists(qtbot, tmp_path):
    """The headline regression: SEGMENTATION_LIST_CHANGED must fire after
    creating an empty labels layer, so SessionWindow's combo refreshes
    without needing a dataset reload.
    """
    from percell4.domain.dataset import DatasetHandle

    path = tmp_path / "exp.h5"
    store = DatasetStore(path)

    session = Session()
    session._dataset = DatasetHandle(
        path=path,
        metadata={
            "channel_names": ["ch0"],
            "mask_names": [],
            "segmentation_names": [],
        },
    )
    model = CellDataModel(session=session)
    launcher = _make_fake_launcher(store)

    panel = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(panel)
    # The resolver uses isinstance(layer, napari.layers.Labels); the test
    # layer is a SimpleNamespace, so stub the resolver to return the most
    # recently added layer.
    panel._get_active_labels_layer = lambda: (
        launcher._added_layers[-1] if launcher._added_layers else None
    )

    # Subscribe before the action so we can prove the event fired.
    events: list[str] = []
    session.subscribe(
        Event.SEGMENTATION_LIST_CHANGED, lambda: events.append("seg_list")
    )

    panel._on_create_empty_labels()

    # Event must have fired — this is the contract SessionWindow relies on.
    assert events == ["seg_list"], (
        f"Expected SEGMENTATION_LIST_CHANGED to fire exactly once; got {events}"
    )


def test_create_empty_labels_updates_dataset_metadata(qtbot, tmp_path):
    """After Create Empty Labels, dataset.metadata['segmentation_names']
    contains 'manual'. SessionWindow reads from this metadata when
    repopulating its combo.
    """
    from percell4.domain.dataset import DatasetHandle

    path = tmp_path / "exp.h5"
    store = DatasetStore(path)

    session = Session()
    # Pre-seed a dataset handle so refresh_resource_lists has something to mutate.
    session._dataset = DatasetHandle(
        path=path,
        metadata={
            "channel_names": ["ch0"],
            "mask_names": [],
            "segmentation_names": [],
        },
    )
    model = CellDataModel(session=session)
    launcher = _make_fake_launcher(store)

    panel = SegmentationPanel(model, launcher=launcher)
    qtbot.addWidget(panel)
    panel._get_active_labels_layer = lambda: (
        launcher._added_layers[-1] if launcher._added_layers else None
    )

    panel._on_create_empty_labels()

    seg_names = session.dataset.metadata.get("segmentation_names", [])
    assert "manual" in seg_names, (
        f"Expected 'manual' in segmentation_names; got {seg_names}"
    )
