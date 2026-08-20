"""Tests for the Cellpose "Preview saturation + blur" layer (U3).

The preview is a sibling of the diameter reference circle: a display-only
napari layer driven by a checkbox, a debounce, and one idempotent
convergence function. Every scenario is driven through the signal path
(``setChecked``, ``setValue``, ``state_changed``) and asserted against the
fake viewer's ``layers`` list and ``layer.visible`` — never against panel
attributes. Layer residue and stale visibility memory are the failure modes
that matter; see docs/solutions/ui-bugs/
phasor-roi-preview-layer-ownership-2026-05-03.md for why asserting on
internal state is not enough.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.config import viewer_presets as vp
from percell4.domain.segmentation.preprocess import preprocess_cellpose_input
from percell4.model import StateChange

PREVIEW = vp.CELLPOSE_PREVIEW_LAYER_NAME

# Comfortably past the panel's debounce so a single ``qtbot.wait`` flushes
# one timer expiry without being tuned to the exact constant.
_PAST_DEBOUNCE_MS = 350


# ══════════════════════════════════════════════════════════════
# Fakes
# ══════════════════════════════════════════════════════════════


class _FakeEvent:
    """Stand-in for a napari ``EventEmitter``: ``connect`` + ``emit``."""

    def __init__(self) -> None:
        self._callbacks: list = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, value) -> None:
        event = SimpleNamespace(value=value)
        for callback in list(self._callbacks):
            callback(event)


class _FakeLayerList(list):
    """A real list with napari's ``events.removed`` / ``events.inserted``.

    ``append`` and ``remove`` fire the matching event, as napari's
    ``LayerList`` does — including for removals the panel performs itself,
    which is exactly the case the ``_removing_preview`` flag exists for.
    Slice assignment and ``clear`` stay silent so tests can stage a rebuild
    and fire the events they want by hand.
    """

    def __init__(self, items=()) -> None:
        super().__init__(items)
        self.events = SimpleNamespace(removed=_FakeEvent(), inserted=_FakeEvent())

    def append(self, layer) -> None:
        super().append(layer)
        self.events.inserted.emit(layer)

    def remove(self, layer) -> None:
        super().remove(layer)
        self.events.removed.emit(layer)


class _FakeViewer:
    """Minimal napari stand-in that really tracks its layer list."""

    def __init__(self, image_layers) -> None:
        self.layers = _FakeLayerList(image_layers)
        self.add_image_calls: list[tuple] = []

    def add_image(self, data, **kwargs):
        self.add_image_calls.append((data, kwargs))
        layer = MagicMock()
        layer.name = kwargs["name"]
        layer.data = data
        layer.visible = True
        layer.contrast_limits = kwargs.get("contrast_limits")
        layer.kwargs = kwargs
        layer.__class__.__name__ = "Image"
        self.layers.append(layer)
        return layer

    def layer_named(self, name):
        return [ly for ly in self.layers if getattr(ly, "name", None) == name]


def _raw_data(seed=0, shape=(64, 64)):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 4000, size=shape, dtype=np.uint16)


def _image_layer(name="ch0", data=None, *, visible=True, seed=0):
    """A weak-referenceable fake Image layer with the display attributes."""
    if data is None:
        data = _raw_data(seed)
    layer = MagicMock()
    layer.name = name
    layer.data = data
    layer.visible = visible
    layer.colormap = f"cmap-{name}"
    layer.blending = "additive"
    layer.opacity = 0.8
    layer.gamma = 1.2
    # Deliberately not the dtype range: a saturation LUT stretches uint16 to
    # (0, 65535), so dtype-range limits on the raw layer could not tell
    # "computed from the preview" apart from "copied from the raw layer".
    layer.contrast_limits = (100.0, 3000.0)
    layer.__class__.__name__ = "Image"
    return layer


def _build_panel(qtbot, *, channel="ch0", image_data=None, with_viewer=True,
                 layers=None):
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
        if layers is None:
            layers = [] if image_data is False else [_image_layer(channel, image_data)]
        viewer = _FakeViewer(layers)
        viewer_win = SimpleNamespace(viewer=viewer, existing_viewer=viewer)
        launcher._windows = {"viewer": viewer_win}
    else:
        viewer = None
        launcher._windows = {}

    panel = SegmentationPanel(data_model=model, launcher=launcher)
    qtbot.addWidget(panel)
    return panel, viewer


def _status(panel):
    return panel._launcher.statusBar.return_value.showMessage


def _set(panel, *, saturation=None, sigma=None):
    if saturation is not None:
        panel._cp_form._saturation.setValue(saturation)
    if sigma is not None:
        panel._cp_form._blur_sigma.setValue(sigma)


def _expected(raw, saturation, sigma):
    return preprocess_cellpose_input(np.asarray(raw), saturation, sigma)


# ══════════════════════════════════════════════════════════════
# Default / engage / disengage
# ══════════════════════════════════════════════════════════════


def test_default_unticked_no_layer_raw_visible(qtbot) -> None:
    """R1: off by default; nothing is added and the raw layer is untouched."""
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]

    assert panel._cp_preview_preprocess.isChecked() is False
    assert viewer.layer_named(PREVIEW) == []
    assert raw.visible is True


def test_tick_2d_adds_preview_copies_style_hides_raw(qtbot) -> None:
    """AE1 / R2 / R3 / R5: one preview layer, helper-equal data, style copied.

    Contrast limits come from the preview data — the raw layer's
    ``(100, 3000)`` must not leak through.
    """
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=1.0, sigma=0.0)

    panel._cp_preview_preprocess.setChecked(True)

    previews = viewer.layer_named(PREVIEW)
    assert len(previews) == 1
    expected = _expected(raw.data, 1.0, 0.0)
    data, kwargs = viewer.add_image_calls[-1]
    np.testing.assert_array_equal(data, expected)
    assert kwargs["colormap"] == raw.colormap
    assert kwargs["blending"] == raw.blending
    assert kwargs["opacity"] == raw.opacity
    assert kwargs["gamma"] == raw.gamma
    assert kwargs["contrast_limits"] == pytest.approx(
        (float(expected.min()), float(expected.max()))
    )
    assert kwargs["contrast_limits"] != raw.contrast_limits
    assert raw.visible is False


@pytest.mark.parametrize("prior_visible", [True, False])
def test_untick_removes_preview_and_restores_prior_visibility(
    qtbot, prior_visible,
) -> None:
    """R6: untick restores exactly the visibility the raw layer had before."""
    panel, viewer = _build_panel(
        qtbot, layers=[_image_layer("ch0", visible=prior_visible)],
    )
    raw = viewer.layers[0]
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    assert raw.visible is False

    panel._cp_preview_preprocess.setChecked(False)

    assert viewer.layer_named(PREVIEW) == []
    assert raw.visible is prior_visible


def test_raw_reshown_by_hand_stays_visible_on_untick(qtbot) -> None:
    """R6: a layer the user re-showed is left as the user set it."""
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)

    raw.visible = True  # user clicks the eye icon back on
    panel._cp_preview_preprocess.setChecked(False)

    assert raw.visible is True
    assert viewer.layer_named(PREVIEW) == []


# ══════════════════════════════════════════════════════════════
# Live edits
# ══════════════════════════════════════════════════════════════


def test_spinbox_edit_while_on_updates_same_layer_in_place(qtbot) -> None:
    """AE1 / R8 / R9: after the debounce the same layer object has new data."""
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=1.0, sigma=0.0)
    panel._cp_preview_preprocess.setChecked(True)
    preview = viewer.layer_named(PREVIEW)[0]

    _set(panel, sigma=2.0)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert viewer.layer_named(PREVIEW) == [preview], "same object, not re-added"
    assert len(viewer.add_image_calls) == 1
    expected = _expected(raw.data, 1.0, 2.0)
    np.testing.assert_array_equal(preview.data, expected)
    assert preview.contrast_limits == pytest.approx(
        (float(expected.min()), float(expected.max()))
    )


def test_two_rapid_edits_recompute_once(qtbot, monkeypatch) -> None:
    """R8: the debounce coalesces back-to-back spinbox edits."""
    from percell4.gui import segmentation_panel as sp_module

    panel, viewer = _build_panel(qtbot)
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)

    calls: list = []

    def counting(plane, saturation_pct, blur_sigma):
        calls.append((saturation_pct, blur_sigma))
        return preprocess_cellpose_input(plane, saturation_pct, blur_sigma)

    monkeypatch.setattr(sp_module, "preprocess_cellpose_input", counting)

    _set(panel, sigma=1.0)
    _set(panel, sigma=2.0)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert calls == [(1.0, 2.0)]
    assert len(viewer.layer_named(PREVIEW)) == 1


def test_spinbox_edit_while_unticked_does_nothing(qtbot, monkeypatch) -> None:
    """R8: edits while off add no layer and compute nothing."""
    from percell4.gui import segmentation_panel as sp_module

    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    calls: list = []
    monkeypatch.setattr(
        sp_module, "preprocess_cellpose_input",
        lambda *a: calls.append(a) or a[0],
    )

    _set(panel, saturation=2.0, sigma=1.5)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert viewer.add_image_calls == []
    assert viewer.layer_named(PREVIEW) == []
    assert calls == []
    assert raw.visible is True


def test_identity_settings_show_nothing_until_nonzero(qtbot) -> None:
    """AE5 / R12: (0, 0) is identity — no layer, raw visible, message, box on.

    Lowering both back to 0 while on removes the layer through the panel's
    own removal path, which must not trip the removed handler and untick.
    """
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=0.0, sigma=0.0)

    panel._cp_preview_preprocess.setChecked(True)

    assert viewer.layer_named(PREVIEW) == []
    assert raw.visible is True
    assert panel._cp_preview_preprocess.isChecked() is True
    assert _status(panel).called
    assert "0" in _status(panel).call_args[0][0]

    _set(panel, sigma=1.0)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert len(viewer.layer_named(PREVIEW)) == 1
    assert raw.visible is False
    np.testing.assert_array_equal(
        viewer.layer_named(PREVIEW)[0].data, _expected(raw.data, 0.0, 1.0)
    )

    _set(panel, sigma=0.0)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert viewer.layer_named(PREVIEW) == []
    assert raw.visible is True
    assert panel._cp_preview_preprocess.isChecked() is True


# ══════════════════════════════════════════════════════════════
# Channel / timepoint following
# ══════════════════════════════════════════════════════════════


def test_channel_switch_restores_old_raw_and_previews_new(qtbot) -> None:
    """AE2 / R10: DAPI comes back, GFP is hidden, data computed from GFP."""
    dapi = _image_layer("DAPI", seed=1)
    gfp = _image_layer("GFP", seed=2)
    panel, viewer = _build_panel(qtbot, channel="DAPI", layers=[dapi, gfp])
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    assert dapi.visible is False and gfp.visible is True

    # set_active_channel emits channel=True through the model itself.
    panel.data_model.session.set_active_channel("GFP")
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert dapi.visible is True
    assert gfp.visible is False
    previews = viewer.layer_named(PREVIEW)
    assert len(previews) == 1
    np.testing.assert_array_equal(previews[0].data, _expected(gfp.data, 1.0, 0.0))


def test_channel_switch_to_missing_layer_removes_preview_keeps_box(qtbot) -> None:
    """R16: a channel with no layer clears the preview, reports, stays ticked."""
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    _status(panel).reset_mock()

    panel.data_model.session.set_active_channel("nope")
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert viewer.layer_named(PREVIEW) == []
    assert raw.visible is True
    assert panel._cp_preview_preprocess.isChecked() is True
    assert _status(panel).called
    assert "nope" in _status(panel).call_args[0][0]


def test_timelapse_previews_active_frame_only(qtbot) -> None:
    """AE3 / R11: (T, H, W) channel → (H, W) preview of the active timepoint."""
    from percell4.domain.dataset import DatasetHandle

    stack = _raw_data(3, shape=(5, 32, 32))
    panel, viewer = _build_panel(qtbot, image_data=stack)
    raw = viewer.layers[0]
    session = panel.data_model.session
    session.set_dataset(DatasetHandle(
        path=Path("/tmp/movie.h5"),
        metadata={"n_timepoints": 5, "channel_names": ["ch0"]},
    ))
    session.set_active_channel("ch0")
    qtbot.wait(10)
    _set(panel, saturation=1.0, sigma=1.0)

    panel._cp_preview_preprocess.setChecked(True)

    preview = viewer.layer_named(PREVIEW)[0]
    assert preview.data.shape == (32, 32)
    np.testing.assert_array_equal(preview.data, _expected(stack[0], 1.0, 1.0))

    # set_active_timepoint emits timepoint=True through the model itself.
    session.set_active_timepoint(3)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert viewer.layer_named(PREVIEW) == [preview]
    assert preview.data.shape == (32, 32)
    np.testing.assert_array_equal(preview.data, _expected(stack[3], 1.0, 1.0))
    assert raw.visible is False


# ══════════════════════════════════════════════════════════════
# Lifecycle: dataset / bin rebuilds, late populate, data no-op
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize("change", [StateChange(data=True), StateChange(bin=True)])
def test_rebuild_engages_fresh_without_stale_memory(qtbot, change) -> None:
    """AE4 / R7 / R13: new same-named layer objects get a fresh preview.

    The old layer's remembered visibility is never applied to the new
    object; the new raw is hidden only because the preview engages afresh.
    """
    panel, viewer = _build_panel(qtbot)
    old_raw = viewer.layers[0]
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    assert old_raw.visible is False

    new_raw = _image_layer("ch0", seed=7)
    viewer.layers[:] = [new_raw]

    panel._on_state_changed(change)
    qtbot.wait(10)

    previews = viewer.layer_named(PREVIEW)
    assert len(previews) == 1
    np.testing.assert_array_equal(previews[0].data, _expected(new_raw.data, 1.0, 0.0))
    assert new_raw.visible is False
    assert len(viewer.add_image_calls) == 2, "fresh engage, not an in-place update"
    assert old_raw.visible is False, "the gone layer is not touched by stale memory"


def test_rebuild_while_unticked_drops_stale_preview(qtbot) -> None:
    """R13: a preview that survived a repopulate is dropped even when off."""
    panel, viewer = _build_panel(qtbot)
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    stale = viewer.layer_named(PREVIEW)[0]
    panel._cp_preview_preprocess.blockSignals(True)
    panel._cp_preview_preprocess.setChecked(False)
    panel._cp_preview_preprocess.blockSignals(False)
    viewer.layers[:] = [_image_layer("ch0"), stale]

    panel._on_state_changed(StateChange(data=True))
    qtbot.wait(10)

    assert viewer.layer_named(PREVIEW) == []


def test_clear_then_late_populate_engages_on_insert(qtbot) -> None:
    """AE4 (native-bin load path): empty viewer, then the channel arrives."""
    panel, viewer = _build_panel(qtbot)
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)

    viewer.layers.clear()
    panel._on_state_changed(StateChange(data=True))
    qtbot.wait(10)

    assert viewer.layer_named(PREVIEW) == []
    assert panel._cp_preview_preprocess.isChecked() is True

    new_raw = _image_layer("ch0", seed=9)
    viewer.layers.append(new_raw)  # fires the fake ``inserted`` event
    qtbot.wait(_PAST_DEBOUNCE_MS)

    previews = viewer.layer_named(PREVIEW)
    assert len(previews) == 1
    np.testing.assert_array_equal(previews[0].data, _expected(new_raw.data, 1.0, 0.0))
    assert new_raw.visible is False


def test_data_change_with_layers_intact_is_noop(qtbot, monkeypatch) -> None:
    """R14: a measurements update must not recompute or re-add."""
    from percell4.gui import segmentation_panel as sp_module

    panel, viewer = _build_panel(qtbot)
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    preview = viewer.layer_named(PREVIEW)[0]
    calls: list = []
    monkeypatch.setattr(
        sp_module, "preprocess_cellpose_input",
        lambda *a: calls.append(a) or a[0],
    )

    panel._on_state_changed(StateChange(data=True))
    qtbot.wait(10)

    assert calls == []
    assert viewer.layer_named(PREVIEW) == [preview]
    assert len(viewer.add_image_calls) == 1


# ══════════════════════════════════════════════════════════════
# Layer-removed events
# ══════════════════════════════════════════════════════════════


def test_manual_removal_unticks_and_restores_raw(qtbot) -> None:
    """AE6 / R15: the user deletes just the preview from the layer list."""
    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    preview = viewer.layer_named(PREVIEW)[0]

    viewer.layers.remove(preview)  # fires the fake ``removed`` event
    qtbot.wait(10)

    assert panel._cp_preview_preprocess.isChecked() is False
    assert raw.visible is True
    assert viewer.layer_named(PREVIEW) == []


def test_rebuild_removal_keeps_box_ticked(qtbot) -> None:
    """R13: ``layers.clear()`` during a rebuild is not a user delete."""
    panel, viewer = _build_panel(qtbot)
    _set(panel, saturation=1.0)
    panel._cp_preview_preprocess.setChecked(True)
    preview = viewer.layer_named(PREVIEW)[0]

    list.clear(viewer.layers)
    viewer.layers.events.removed.emit(preview)
    qtbot.wait(10)

    assert panel._cp_preview_preprocess.isChecked() is True


# ══════════════════════════════════════════════════════════════
# No viewer / no layer
# ══════════════════════════════════════════════════════════════


def test_no_viewer_reports_and_keeps_box(qtbot) -> None:
    """R16: ticking with no viewer reports instead of raising."""
    panel, _ = _build_panel(qtbot, with_viewer=False)
    _set(panel, saturation=1.0)

    panel._cp_preview_preprocess.setChecked(True)

    assert panel._cp_preview_preprocess.isChecked() is True
    assert _status(panel).called
    assert "viewer" in _status(panel).call_args[0][0].lower()


def test_no_image_layer_reports_and_keeps_box(qtbot) -> None:
    """R16: a viewer with no layer for the channel reports, stays ticked."""
    panel, viewer = _build_panel(qtbot, image_data=False)
    _set(panel, saturation=1.0)

    panel._cp_preview_preprocess.setChecked(True)

    assert viewer.layer_named(PREVIEW) == []
    assert panel._cp_preview_preprocess.isChecked() is True
    assert _status(panel).called


# ══════════════════════════════════════════════════════════════
# napari semantics and the run path
# ══════════════════════════════════════════════════════════════


def test_add_and_update_do_not_steal_active_layer(qtbot) -> None:
    """R4: a real ViewerModel keeps the Labels layer selected across add+update."""
    from napari.components import ViewerModel

    viewer = ViewerModel()
    viewer.add_image(_raw_data(4, (64, 64)), name="ch0")
    viewer.add_labels(np.zeros((64, 64), dtype=np.int32), name="manual")
    assert viewer.layers.selection.active.name == "manual"

    panel, _ = _build_panel(qtbot)
    panel._launcher._windows = {
        "viewer": SimpleNamespace(viewer=viewer, existing_viewer=viewer)
    }
    _set(panel, saturation=1.0)

    panel._cp_preview_preprocess.setChecked(True)

    names = {ly.name for ly in viewer.layers}
    assert PREVIEW in names
    assert viewer.layers["ch0"].visible is False
    assert viewer.layers.selection.active.name == "manual"

    _set(panel, sigma=1.0)
    qtbot.wait(_PAST_DEBOUNCE_MS)

    assert viewer.layers.selection.active.name == "manual"
    assert [ly.name for ly in viewer.layers].count(PREVIEW) == 1

    panel._cp_preview_preprocess.setChecked(False)

    assert PREVIEW not in {ly.name for ly in viewer.layers}
    assert viewer.layers["ch0"].visible is True


def test_preview_does_not_perturb_run_cellpose(qtbot, monkeypatch) -> None:
    """R17: the worker gets the raw layer's preprocessed data; preview stays."""
    import percell4.gui.workers as workers_mod
    from percell4.gui import segmentation_panel as sp_module

    panel, viewer = _build_panel(qtbot)
    raw = viewer.layers[0]
    _set(panel, saturation=1.0, sigma=1.0)
    panel._cp_preview_preprocess.setChecked(True)

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
    args, _kwargs = calls[0]
    np.testing.assert_array_equal(args[1], _expected(raw.data, 1.0, 1.0))
    assert len(viewer.layer_named(PREVIEW)) == 1
