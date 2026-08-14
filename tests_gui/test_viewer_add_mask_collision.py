"""Regression tests for ``ViewerWindow.add_mask`` name-collision behavior.

The original idempotent in-place refresh path assumed any pre-existing
same-name layer was a Labels layer. When that assumption broke — e.g.,
a user-named phasor ROI matched a channel name — assigning a
``DirectLabelColormap`` to the existing Image layer raised
``TypeError: DirectLabelColormap can only be used with int`` deep inside
napari's thumbnail update path. ``add_mask`` now hard-blocks any
same-name collision and surfaces it via ``QMessageBox.warning``.
"""

from __future__ import annotations

import numpy as np
import pytest
from napari.layers import Image, Labels

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel


@pytest.fixture
def viewer_harness(qtbot):
    session = Session()
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()
    yield viewer_win
    try:
        viewer_win.viewer.close()
    except Exception:
        pass


@pytest.fixture
def small_image() -> np.ndarray:
    return (np.arange(64, dtype=np.float32).reshape(8, 8) / 64.0)


@pytest.fixture
def small_mask() -> np.ndarray:
    arr = np.zeros((8, 8), dtype=np.uint8)
    arr[2:5, 2:5] = 1
    return arr


def _patch_warning(monkeypatch) -> list[tuple]:
    """Capture the collision popup instead of opening a real dialog.

    Patched on the viewer module's own ``message_box`` name, not the Qt
    static: ``add_mask`` routes through ``_dialog_utils.message_box`` so the
    popup can be made freestanding, and patching ``QMessageBox.warning``
    would no longer intercept -- leaving a real modal to block this tier.
    """
    from qtpy.QtWidgets import QMessageBox

    from percell4.gui import viewer as viewer_mod

    captured: list[tuple] = []
    monkeypatch.setattr(
        viewer_mod,
        "message_box",
        lambda parent, title, text, *a, **kw: captured.append((title, text))
        or QMessageBox.StandardButton.Ok,
    )
    return captured


def test_add_mask_blocks_when_image_layer_with_same_name_exists(
    monkeypatch, viewer_harness, small_image, small_mask
):
    """The CA-SiR bug: channel-name layer must not be clobbered by add_mask."""
    captured = _patch_warning(monkeypatch)
    viewer_harness.add_image(small_image, "CA-SiR")
    viewer_harness.add_mask(small_mask, name="CA-SiR")

    # The existing layer is still the Image — not replaced, not crashed.
    assert isinstance(viewer_harness.viewer.layers["CA-SiR"], Image)
    # Only one layer carries that name; no auto-renamed "CA-SiR [1]" got added.
    assert [layer.name for layer in viewer_harness.viewer.layers] == ["CA-SiR"]
    # User saw a popup naming the conflict.
    assert len(captured) == 1
    title, text = captured[0]
    assert title == "Mask name conflict"
    assert "CA-SiR" in text and "Image" in text


def test_add_mask_blocks_when_labels_layer_with_same_name_exists(
    monkeypatch, viewer_harness, small_mask
):
    """Even a Labels layer with the same name blocks — surfaces the duplicate."""
    captured = _patch_warning(monkeypatch)
    viewer_harness.add_labels(np.ones((8, 8), dtype=np.int32), "manual")
    viewer_harness.add_mask(small_mask, name="manual")

    assert [layer.name for layer in viewer_harness.viewer.layers] == ["manual"]
    assert len(captured) == 1
    assert "Labels" in captured[0][1]


def test_add_mask_creates_labels_when_no_collision(
    monkeypatch, viewer_harness, small_mask
):
    """Happy path: no same-name layer → a new Labels layer is added."""
    captured = _patch_warning(monkeypatch)
    viewer_harness.add_mask(small_mask, name="binary_mask")

    assert isinstance(viewer_harness.viewer.layers["binary_mask"], Labels)
    assert captured == []  # no popup on the happy path
