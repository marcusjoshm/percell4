"""Session -> napari one-way push for active mask/segmentation (U10).

When session writes ``active_mask`` or ``active_segmentation``, the matching
napari layer (matched by name + ``metadata["percell_type"]``) becomes the
active layer in ``viewer.layers.selection``. The push is a controlled
PerCell4-side write inside ``ViewerWindow._on_state_changed`` guarded by
``_is_originator``.

Mirrors the ``viewer_harness`` fixture pattern from ``test_multi_select_e2e.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.gui.viewer import (
    LAYER_TYPE_MASK,
    LAYER_TYPE_SEGMENTATION,
    PERCELL_TYPE_KEY,
    ViewerWindow,
)
from percell4.model import CellDataModel

# Builds a real napari viewer, so this module carries the ``napari_viewer``
# marker: skipped by default (see pyproject addopts), run explicitly on CI.
pytestmark = pytest.mark.napari_viewer


@pytest.fixture
def viewer_harness(qtbot, sample_labels: np.ndarray):
    """Real napari.Viewer + ViewerWindow + Session + CellDataModel, with one
    segmentation layer and two mask layers pre-installed.

    Yields ``(session, data_model, viewer_win)``. Teardown closes the viewer.
    """
    session = Session()
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)

    viewer_win._ensure_viewer()
    viewer_win.add_labels(sample_labels, name="seg_a")
    viewer_win.add_labels(sample_labels, name="seg_b")
    viewer_win.add_mask((sample_labels > 0).astype(np.uint8), name="mask_a")
    viewer_win.add_mask((sample_labels > 2).astype(np.uint8), name="mask_b")

    yield session, data_model, viewer_win

    try:
        viewer_win.viewer.close()
    except Exception:
        pass


def test_set_active_mask_selects_matching_napari_layer(viewer_harness) -> None:
    """Happy path: setting ``session.active_mask`` flips napari's active layer
    to the matching mask layer (matched by name + percell_type)."""
    session, data_model, viewer_win = viewer_harness
    viewer = viewer_win.viewer

    data_model.set_active_mask("mask_a")

    active = viewer.layers.selection.active
    assert active is not None
    assert active.name == "mask_a"
    assert active.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK


def test_set_active_segmentation_selects_matching_napari_layer(
    viewer_harness,
) -> None:
    """Variant: setting ``session.active_segmentation`` flips napari's active
    layer to the matching segmentation layer."""
    session, data_model, viewer_win = viewer_harness
    viewer = viewer_win.viewer

    data_model.set_active_segmentation("seg_b")

    active = viewer.layers.selection.active
    assert active is not None
    assert active.name == "seg_b"
    assert active.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_SEGMENTATION


def test_set_active_mask_none_is_noop(viewer_harness) -> None:
    """Edge case: writing ``active_mask = None`` does not crash and does not
    clear napari's active layer (the push only acts on non-empty names —
    matches the no-action-on-empty idiom of other state-change branches)."""
    session, data_model, viewer_win = viewer_harness
    viewer = viewer_win.viewer

    # Establish a known active layer first, then clear the session slot.
    data_model.set_active_mask("mask_a")
    assert viewer.layers.selection.active.name == "mask_a"

    data_model.set_active_mask("")  # CellDataModel maps "" -> None at session

    # Active layer is unchanged (not flipped to None).
    assert viewer.layers.selection.active is not None
    assert viewer.layers.selection.active.name == "mask_a"


def test_creator_writing_new_mask_auto_selects_it(viewer_harness) -> None:
    """A Creator that writes a new mask and sets it as active causes napari to
    highlight the new layer. Simulates the threshold-accept / phasor-apply
    flow: add layer first, then write the session slot."""
    session, data_model, viewer_win = viewer_harness

    new_mask = (np.zeros((100, 100), dtype=np.uint8))
    new_mask[40:60, 40:60] = 1
    viewer_win.add_mask(new_mask, name="mask_new")

    data_model.set_active_mask("mask_new")

    active = viewer_win.viewer.layers.selection.active
    assert active is not None
    assert active.name == "mask_new"
    assert active.metadata.get(PERCELL_TYPE_KEY) == LAYER_TYPE_MASK


def test_set_active_mask_with_no_matching_layer_is_noop(viewer_harness) -> None:
    """Negative: if the named layer doesn't exist in napari, the push is a
    silent no-op (no exception). The previous active layer is untouched."""
    session, data_model, viewer_win = viewer_harness
    viewer = viewer_win.viewer

    # Pin a known active layer.
    data_model.set_active_segmentation("seg_a")
    assert viewer.layers.selection.active.name == "seg_a"

    # Write a name that doesn't correspond to any layer in napari.
    data_model.set_active_mask("nonexistent_mask")

    # No exception, no change to the active layer.
    assert viewer.layers.selection.active is not None
    assert viewer.layers.selection.active.name == "seg_a"


def test_push_ignores_layer_with_wrong_percell_type(viewer_harness) -> None:
    """If the requested name only matches a layer whose ``percell_type``
    differs (e.g. asking for ``active_mask = "seg_a"`` while ``seg_a`` is a
    segmentation), the push is a no-op rather than picking the wrong layer."""
    session, data_model, viewer_win = viewer_harness
    viewer = viewer_win.viewer

    # Pin a known active layer first so we can detect any unintended write.
    data_model.set_active_mask("mask_b")
    assert viewer.layers.selection.active.name == "mask_b"

    # "seg_a" exists, but as a SEGMENTATION layer — not a mask. The mask-push
    # must not pick it up.
    data_model.set_active_mask("seg_a")
    assert viewer.layers.selection.active.name == "mask_b"


def test_push_does_not_re_enter_session(viewer_harness) -> None:
    """Re-entrancy: the napari ``viewer.layers.selection.events.active`` event
    fired by the push must not write back into session (U9 already removed
    that subscription, but ``_is_originator`` still guards against any future
    bidirectional path).

    Asserts the push completes with ``_is_originator`` cleared and that the
    session slot still holds the value the test wrote.
    """
    session, data_model, viewer_win = viewer_harness

    data_model.set_active_mask("mask_a")

    assert viewer_win._is_originator is False
    assert session.active_mask == "mask_a"
