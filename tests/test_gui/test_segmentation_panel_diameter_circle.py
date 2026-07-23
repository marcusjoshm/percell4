"""Tests for the Cellpose diameter reference circle (U2/U3).

Two halves:

* ``diameter_circle_bbox`` — the pure geometry helper, tested without Qt or
  napari. The clamping rules are the part most likely to be wrong.
* ``SegmentationPanel`` toggle lifecycle — the checkbox, the live resize, and
  (most importantly) that no ``_diameter_reference`` layer survives an untick
  or a dataset switch. Layer residue is the documented failure mode here; see
  docs/solutions/ui-bugs/phasor-roi-preview-layer-ownership-2026-05-03.md,
  where a regression test passed while the bug shipped because it asserted on
  internal state instead of the viewer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.config import viewer_presets as vp
from percell4.gui.segmentation_panel import diameter_circle_bbox

CIRCLE = vp.DIAMETER_CIRCLE_LAYER_NAME


# ══════════════════════════════════════════════════════════════
# U2 — pure geometry
# ══════════════════════════════════════════════════════════════


def _extents(bbox):
    """(y_extent, x_extent) of a bounding box."""
    ys, xs = bbox[:, 0], bbox[:, 1]
    return ys.max() - ys.min(), xs.max() - xs.min()


def test_bbox_sized_and_placed_bottom_left() -> None:
    """A 100 px circle on a 512² image is 100 px across, lower-left."""
    bbox = diameter_circle_bbox((512, 512), 100.0, 10.0)

    y_extent, x_extent = _extents(bbox)
    assert y_extent == pytest.approx(100.0)
    assert x_extent == pytest.approx(100.0)

    center_y = bbox[:, 0].mean()
    center_x = bbox[:, 1].mean()
    assert center_y > 256, "napari y grows downward — bottom is large y"
    assert center_x < 256


def test_bbox_scales_with_diameter_and_stays_in_frame() -> None:
    """Doubling the diameter doubles both extents; both fit the image."""
    small = diameter_circle_bbox((512, 512), 100.0, 10.0)
    large = diameter_circle_bbox((512, 512), 200.0, 10.0)

    assert _extents(large)[0] == pytest.approx(2 * _extents(small)[0])
    assert _extents(large)[1] == pytest.approx(2 * _extents(small)[1])

    for bbox in (small, large):
        assert bbox.min() >= 0.0
        assert bbox[:, 0].max() <= 512.0
        assert bbox[:, 1].max() <= 512.0


def test_zero_diameter_returns_none() -> None:
    """R6: 0 is the auto-detect sentinel — nothing to draw."""
    assert diameter_circle_bbox((512, 512), 0.0, 10.0) is None


def test_negative_diameter_returns_none() -> None:
    """Unreachable via the spinbox, but the helper is public."""
    assert diameter_circle_bbox((512, 512), -5.0, 10.0) is None


def test_diameter_equal_to_height_clamps_flush() -> None:
    """A full-height circle sits flush with no negative coordinates."""
    bbox = diameter_circle_bbox((512, 512), 512.0, 10.0)

    assert bbox[:, 0].min() == pytest.approx(0.0)
    assert bbox[:, 0].max() == pytest.approx(512.0)
    assert bbox.min() >= 0.0


def test_oversize_diameter_pins_flush_and_overflows() -> None:
    """1000 px on a 512² image overflows, but never off the top/left.

    The overflow is the point: a disc spilling past the image edge tells the
    user their diameter is far too large.
    """
    bbox = diameter_circle_bbox((512, 512), 1000.0, 10.0)

    assert bbox[:, 0].min() == pytest.approx(0.0)
    assert bbox[:, 1].min() == pytest.approx(0.0)
    assert _extents(bbox)[0] == pytest.approx(1000.0)


def test_non_square_image_stays_round() -> None:
    """The circle is round, not stretched to the image aspect ratio."""
    bbox = diameter_circle_bbox((256, 1024), 100.0, 10.0)

    y_extent, x_extent = _extents(bbox)
    assert y_extent == pytest.approx(x_extent)
    assert y_extent == pytest.approx(100.0)


def test_margin_larger_than_image_clamps() -> None:
    """An absurd margin clamps instead of inverting or leaving the image."""
    bbox = diameter_circle_bbox((100, 100), 20.0, 500.0)

    assert bbox.min() >= 0.0
    assert _extents(bbox)[0] == pytest.approx(20.0)
    assert _extents(bbox)[1] == pytest.approx(20.0)


def test_presets_are_opaque_magenta() -> None:
    """R4: the disc is opaque magenta, per the Cellpose GUI."""
    r, g, b, a = vp.DIAMETER_CIRCLE_FACE_COLOR
    assert (r, g, b) == (1.0, 0.0, 1.0)
    assert a == 1.0, "opaque — see the preset comment before changing this"


def test_helper_is_numpy_pure() -> None:
    """Returns a float array of four (y, x) vertices."""
    bbox = diameter_circle_bbox((512, 512), 100.0, 10.0)
    assert isinstance(bbox, np.ndarray)
    assert bbox.shape == (4, 2)
    assert bbox.dtype == float


# ══════════════════════════════════════════════════════════════
# U3 — panel toggle lifecycle
# ══════════════════════════════════════════════════════════════


class _FakeViewer:
    """Minimal napari stand-in that really tracks its layer list.

    A bare ``MagicMock`` would make ``add_shapes`` a no-op and let a
    "layer removed" assertion pass while the layer is still on the canvas —
    exactly how the phasor preview-layer bug shipped green.
    """

    def __init__(self, image_layers):
        # A real list: append/remove/iteration behave like napari's LayerList
        # for the operations this feature performs.
        self.layers = list(image_layers)
        self.add_shapes_calls: list[tuple] = []

    def add_shapes(self, data, **kwargs):
        self.add_shapes_calls.append((data, kwargs))
        layer = SimpleNamespace(
            name=kwargs["name"],
            data=data,
            editable=True,
            kwargs=kwargs,
        )
        self.layers.append(layer)
        return layer

    def layer_named(self, name):
        return [ly for ly in self.layers if getattr(ly, "name", None) == name]


def _image_layer(name="ch0", data=None):
    if data is None:
        data = np.zeros((512, 512), dtype=np.uint16)
    layer = MagicMock()
    layer.name = name
    layer.data = data
    layer.__class__.__name__ = "Image"
    return layer


def _build_panel(qtbot, *, channel="ch0", image_data=None, with_viewer=True):
    """SegmentationPanel wired to a _FakeViewer (or to no viewer at all)."""
    from percell4.gui.segmentation_panel import SegmentationPanel
    from percell4.model import CellDataModel

    model = CellDataModel()
    model.session.set_active_channel(channel)

    store = MagicMock()
    store.list_labels.return_value = []
    store.list_masks.return_value = []

    launcher = MagicMock()
    launcher._current_store = store
    launcher.statusBar.return_value.showMessage = MagicMock()

    if with_viewer:
        layers = [] if image_data is False else [_image_layer(channel, image_data)]
        viewer = _FakeViewer(layers)
        viewer_win = SimpleNamespace(viewer=viewer)
        launcher._windows = {"viewer": viewer_win}
    else:
        viewer = None
        launcher._windows = {}

    panel = SegmentationPanel(data_model=model, launcher=launcher)
    qtbot.addWidget(panel)
    return panel, viewer


def test_checkbox_defaults_off_and_draws_nothing(qtbot) -> None:
    """R1: the overlay is opt-in; nothing is drawn until asked."""
    panel, viewer = _build_panel(qtbot)
    assert panel._cp_show_diameter_circle.isChecked() is False
    assert viewer.layer_named(CIRCLE) == []


def test_tick_adds_circle_at_diameter(qtbot) -> None:
    """R1/R2/R4: ticking draws one opaque magenta ellipse of the right size."""
    panel, viewer = _build_panel(qtbot)
    panel._cp_form._diameter.setValue(300.0)

    panel._cp_show_diameter_circle.setChecked(True)

    assert len(viewer.layer_named(CIRCLE)) == 1
    _data, kwargs = viewer.add_shapes_calls[-1]
    assert kwargs["shape_type"] == "ellipse"
    assert kwargs["name"] == CIRCLE
    assert kwargs["face_color"] == [list(vp.DIAMETER_CIRCLE_FACE_COLOR)]

    bbox = np.asarray(_data[0])
    assert _extents(bbox)[0] == pytest.approx(300.0)
    assert _extents(bbox)[1] == pytest.approx(300.0)


def test_circle_is_not_editable(qtbot) -> None:
    """A reference ruler the user can reshape is a ruler that lies."""
    panel, viewer = _build_panel(qtbot)
    panel._cp_show_diameter_circle.setChecked(True)

    assert viewer.layer_named(CIRCLE)[0].editable is False


def test_diameter_edit_resizes_live(qtbot) -> None:
    """R3: the circle follows Diameter (px) without re-toggling.

    This is the scenario docs/solutions/conventions/qt-wire-user-edit-signals
    exists to protect — an unwired signal would leave the first circle frozen
    at 300 px while the field reads 150.
    """
    panel, viewer = _build_panel(qtbot)
    panel._cp_form._diameter.setValue(300.0)
    panel._cp_show_diameter_circle.setChecked(True)

    panel._cp_form._diameter.setValue(150.0)

    assert len(viewer.layer_named(CIRCLE)) == 1
    bbox = np.asarray(viewer.add_shapes_calls[-1][0][0])
    assert _extents(bbox)[0] == pytest.approx(150.0)


def test_untick_removes_layer_from_viewer(qtbot) -> None:
    """R7: no residue. Asserted against the viewer, not panel state."""
    panel, viewer = _build_panel(qtbot)
    panel._cp_show_diameter_circle.setChecked(True)
    assert viewer.layer_named(CIRCLE) != []

    panel._cp_show_diameter_circle.setChecked(False)

    assert viewer.layer_named(CIRCLE) == []


def test_repeated_toggles_never_stack_layers(qtbot) -> None:
    """Tick → untick → tick leaves exactly one circle."""
    panel, viewer = _build_panel(qtbot)

    panel._cp_show_diameter_circle.setChecked(True)
    panel._cp_show_diameter_circle.setChecked(False)
    panel._cp_show_diameter_circle.setChecked(True)

    assert len(viewer.layer_named(CIRCLE)) == 1
    assert len(viewer.add_shapes_calls) == 2


def test_zero_diameter_hides_circle_but_keeps_checkbox(qtbot) -> None:
    """R6: 0 draws nothing; a nonzero value brings the circle back."""
    panel, viewer = _build_panel(qtbot)
    panel._cp_form._diameter.setValue(300.0)
    panel._cp_show_diameter_circle.setChecked(True)

    panel._cp_form._diameter.setValue(0.0)

    assert viewer.layer_named(CIRCLE) == []
    assert panel._cp_show_diameter_circle.isChecked() is True

    panel._cp_form._diameter.setValue(80.0)

    assert len(viewer.layer_named(CIRCLE)) == 1
    bbox = np.asarray(viewer.add_shapes_calls[-1][0][0])
    assert _extents(bbox)[0] == pytest.approx(80.0)


def test_diameter_edit_while_unchecked_draws_nothing(qtbot) -> None:
    """The overlay is off; edits must not conjure a layer."""
    panel, viewer = _build_panel(qtbot)

    panel._cp_form._diameter.setValue(150.0)

    assert viewer.add_shapes_calls == []
    assert viewer.layer_named(CIRCLE) == []


def test_timelapse_uses_trailing_two_dims(qtbot) -> None:
    """A (T, H, W) channel yields a 2D ellipse sized off (H, W)."""
    stack = np.zeros((3, 256, 256), dtype=np.uint16)
    panel, viewer = _build_panel(qtbot, image_data=stack)
    panel._cp_form._diameter.setValue(100.0)

    panel._cp_show_diameter_circle.setChecked(True)

    bbox = np.asarray(viewer.add_shapes_calls[-1][0][0])
    assert bbox.shape == (4, 2), "ellipse stays 2D on a time-lapse channel"
    assert bbox[:, 0].max() <= 256.0


def test_no_viewer_does_not_raise(qtbot) -> None:
    """R8: ticking with no viewer reports instead of crashing."""
    panel, _ = _build_panel(qtbot, with_viewer=False)

    panel._cp_show_diameter_circle.setChecked(True)

    msg = panel._launcher.statusBar.return_value.showMessage
    assert msg.called
    assert "viewer" in msg.call_args[0][0].lower()


def test_no_image_layer_does_not_raise(qtbot) -> None:
    """R8: a viewer with no Image layer reports instead of crashing."""
    panel, viewer = _build_panel(qtbot, image_data=False)

    panel._cp_show_diameter_circle.setChecked(True)

    assert viewer.layer_named(CIRCLE) == []
    msg = panel._launcher.statusBar.return_value.showMessage
    assert msg.called


def test_dataset_switch_rebuilds_against_new_shape(qtbot) -> None:
    """Integration: a circle from the previous dataset must not survive.

    Drives the real state_changed.data path, including the one-tick deferral
    the handler uses because the viewer's layers do not exist yet when the
    signal fires.
    """
    panel, viewer = _build_panel(qtbot)
    panel._cp_form._diameter.setValue(100.0)
    panel._cp_show_diameter_circle.setChecked(True)
    assert len(viewer.layer_named(CIRCLE)) == 1

    # New dataset: smaller image, same active channel.
    viewer.layers[:] = [_image_layer("ch0", np.zeros((128, 128), dtype=np.uint16))]

    from percell4.model import StateChange

    panel._on_state_changed(StateChange(data=True))
    qtbot.wait(10)  # flush the QTimer.singleShot(0, ...) deferral

    circles = viewer.layer_named(CIRCLE)
    assert len(circles) == 1, "exactly one circle, rebuilt for the new dataset"
    bbox = np.asarray(circles[0].data[0])
    assert bbox.min() >= 0.0
    assert bbox[:, 0].max() <= 128.0


def test_add_does_not_steal_active_layer_from_labels(qtbot) -> None:
    """Adding the circle must not yank selection off the user's Labels layer.

    Uses napari's real ViewerModel (pure pydantic, no Qt) because this is a
    napari selection-semantics guarantee — a hand-rolled fake would just
    re-encode my assumption. napari activates every newly added layer, and
    ``editable = False`` does not prevent it; losing the active Labels layer
    also disarms the `M` multi-select keystroke, which binds on Labels
    keymaps.
    """
    from napari.components import ViewerModel

    viewer = ViewerModel()
    viewer.add_image(np.zeros((256, 256), dtype=np.uint16), name="ch0")
    viewer.add_labels(np.zeros((256, 256), dtype=np.int32), name="manual")
    assert viewer.layers.selection.active.name == "manual"

    panel, _ = _build_panel(qtbot)
    panel._launcher._windows = {"viewer": SimpleNamespace(viewer=viewer)}
    panel._cp_form._diameter.setValue(100.0)

    panel._cp_show_diameter_circle.setChecked(True)

    assert CIRCLE in {ly.name for ly in viewer.layers}, "circle was added"
    assert viewer.layers.selection.active.name == "manual", (
        "active layer must survive the circle add"
    )


def test_overlay_does_not_perturb_run_cellpose(qtbot, monkeypatch) -> None:
    """Integration: the circle is display-only — the run path is unchanged."""
    import percell4.gui.workers as workers_mod
    from percell4.gui import segmentation_panel as sp_module

    panel, _viewer = _build_panel(qtbot)
    panel._cp_form._diameter.setValue(300.0)
    panel._cp_show_diameter_circle.setChecked(True)

    monkeypatch.setattr(sp_module, "prompt_for_resource_name", lambda *a, **kw: "seg")
    calls: list = []

    class FakeWorker:
        def __init__(self, *a, **kw):
            calls.append((a, kw))
            self.finished = MagicMock()
            self.error = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)

    panel._on_run_cellpose()

    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["diameter"] == pytest.approx(300.0)
