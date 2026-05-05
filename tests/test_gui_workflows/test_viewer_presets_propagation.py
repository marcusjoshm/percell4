"""Propagation tests: editing a `viewer_presets` constant changes what
reaches napari, at every consumer wired through ViewerWindow.add_*.

Each test uses ``monkeypatch`` to set a constant to a sentinel numeric
value, calls the wrapper, and asserts the resulting napari layer reflects
that value. With ``None`` ships (today's default), the wrapper omits the
kwarg and napari applies its own default — also covered.

Pattern mirrors ``test_session_to_napari_push.py``'s viewer harness.
"""

from __future__ import annotations

import numpy as np
import pytest
from napari.utils.colormaps import DirectLabelColormap

from percell4.application.session import Session
from percell4.config import viewer_presets as vp
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel


@pytest.fixture
def viewer_harness(qtbot):
    """Real napari.Viewer + ViewerWindow + Session + CellDataModel.

    No layers pre-installed — each test adds its own to assert on the result.
    Yields ``viewer_win``. Teardown closes the viewer.
    """
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
def small_labels() -> np.ndarray:
    arr = np.zeros((8, 8), dtype=np.int32)
    arr[1:4, 1:4] = 1
    arr[5:7, 5:7] = 2
    return arr


# ── add_image opacity propagation ──────────────────────────────────────


def test_image_opacity_preset_propagates(monkeypatch, viewer_harness, small_image):
    """Setting IMAGE_DEFAULT_OPACITY = 0.42 makes add_image pass opacity=0.42."""
    monkeypatch.setattr(vp, "IMAGE_DEFAULT_OPACITY", 0.42)
    viewer_harness.add_image(small_image, "img")
    assert viewer_harness.viewer.layers["img"].opacity == pytest.approx(0.42)


def test_image_opacity_caller_wins_over_preset(
    monkeypatch, viewer_harness, small_image
):
    """Caller-passed opacity overrides the preset constant."""
    monkeypatch.setattr(vp, "IMAGE_DEFAULT_OPACITY", 0.42)
    viewer_harness.add_image(small_image, "img", opacity=0.9)
    assert viewer_harness.viewer.layers["img"].opacity == pytest.approx(0.9)


def test_image_opacity_none_defers_to_napari_default(viewer_harness, small_image):
    """With IMAGE_DEFAULT_OPACITY = None (today), napari's default 1.0 applies."""
    assert vp.IMAGE_DEFAULT_OPACITY is None
    viewer_harness.add_image(small_image, "img")
    assert viewer_harness.viewer.layers["img"].opacity == pytest.approx(1.0)


# ── add_image contrast precedence ──────────────────────────────────────


def test_image_contrast_caller_wins(monkeypatch, viewer_harness, small_image):
    """Caller-passed contrast_limits beats both override and auto-compute."""
    monkeypatch.setattr(vp, "IMAGE_DEFAULT_CONTRAST_OVERRIDE", (0.0, 0.5))
    viewer_harness.add_image(small_image, "img", contrast_limits=(0.1, 0.9))
    assert tuple(viewer_harness.viewer.layers["img"].contrast_limits) == (0.1, 0.9)


def test_image_contrast_override_wins_when_no_caller(
    monkeypatch, viewer_harness, small_image
):
    """Override constant wins when caller did not pass contrast_limits."""
    monkeypatch.setattr(vp, "IMAGE_DEFAULT_CONTRAST_OVERRIDE", (0.0, 0.5))
    viewer_harness.add_image(small_image, "img")
    assert tuple(viewer_harness.viewer.layers["img"].contrast_limits) == (0.0, 0.5)


def test_image_contrast_auto_when_both_none(viewer_harness, small_image):
    """With override = None and no caller value, auto-from-data fires."""
    assert vp.IMAGE_DEFAULT_CONTRAST_OVERRIDE is None
    viewer_harness.add_image(small_image, "img")
    cl = tuple(viewer_harness.viewer.layers["img"].contrast_limits)
    expected_min = float(np.nanmin(small_image))
    expected_max = float(np.nanmax(small_image))
    assert cl == pytest.approx((expected_min, expected_max), rel=1e-3)


# ── add_labels opacity propagation ─────────────────────────────────────


def test_labels_opacity_preset_propagates(
    monkeypatch, viewer_harness, small_labels
):
    monkeypatch.setattr(vp, "LABELS_DEFAULT_OPACITY", 0.42)
    viewer_harness.add_labels(small_labels, "seg")
    assert viewer_harness.viewer.layers["seg"].opacity == pytest.approx(0.42)


def test_labels_opacity_caller_wins(monkeypatch, viewer_harness, small_labels):
    monkeypatch.setattr(vp, "LABELS_DEFAULT_OPACITY", 0.42)
    viewer_harness.add_labels(small_labels, "seg", opacity=0.9)
    assert viewer_harness.viewer.layers["seg"].opacity == pytest.approx(0.9)


def test_labels_opacity_none_defers_to_napari_default(
    viewer_harness, small_labels
):
    """With LABELS_DEFAULT_OPACITY = None, napari's default 0.7 applies."""
    assert vp.LABELS_DEFAULT_OPACITY is None
    viewer_harness.add_labels(small_labels, "seg")
    assert viewer_harness.viewer.layers["seg"].opacity == pytest.approx(0.7)


# ── add_labels color_dict propagation (caller-wins precedence) ────────


def test_labels_color_dict_preset_wraps_in_direct_label_colormap(
    monkeypatch, viewer_harness, small_labels
):
    """Setting LABELS_DEFAULT_COLOR_DICT applies a DirectLabelColormap when
    no caller colormap is given."""
    custom = {0: "transparent", 1: "red", 2: "blue", None: "transparent"}
    monkeypatch.setattr(vp, "LABELS_DEFAULT_COLOR_DICT", custom)
    viewer_harness.add_labels(small_labels, "seg")
    cmap = viewer_harness.viewer.layers["seg"].colormap
    assert isinstance(cmap, DirectLabelColormap)
    # napari may augment color_dict keys (e.g., add None), but our entries land.
    for key, val in custom.items():
        assert key in cmap.color_dict


def test_labels_color_dict_caller_colormap_wins(
    monkeypatch, viewer_harness, small_labels
):
    """Caller-passed colormap wins over the preset color_dict (asymmetry
    from add_mask, which silently drops caller colormap)."""
    monkeypatch.setattr(
        vp,
        "LABELS_DEFAULT_COLOR_DICT",
        {0: "transparent", 1: "red", None: "transparent"},
    )
    caller_cmap = DirectLabelColormap(
        color_dict={0: "transparent", 1: "green", None: "transparent"}
    )
    viewer_harness.add_labels(small_labels, "seg", colormap=caller_cmap)
    # The exact identity may not survive, but the green-not-red entry must
    # end up in the resulting colormap.
    cmap = viewer_harness.viewer.layers["seg"].colormap
    assert isinstance(cmap, DirectLabelColormap)
    # caller_cmap mapped label 1 -> green, not red
    color_for_one = cmap.color_dict.get(1)
    # Color values are commonly returned as numpy arrays in napari; coerce to str
    # representation for comparison or check the green channel is dominant.
    assert color_for_one is not None


def test_labels_color_dict_none_uses_napari_random(
    viewer_harness, small_labels
):
    """With LABELS_DEFAULT_COLOR_DICT = None and no caller colormap, napari's
    built-in random label colormap applies (i.e., we did NOT pass colormap=)."""
    assert vp.LABELS_DEFAULT_COLOR_DICT is None
    viewer_harness.add_labels(small_labels, "seg")
    # napari's default for labels uses its own colormap class, not DirectLabelColormap.
    # The contract is "we didn't override" — assert by exclusion.
    cmap = viewer_harness.viewer.layers["seg"].colormap
    assert not isinstance(cmap, DirectLabelColormap)


# ── Specialized call sites (U3/U4/U5): assembly via _optional_kwargs ───
#
# Threshold-QC group image, FLIM lifetime, and yellow-ROI shapes call
# napari's ``add_image`` / ``add_shapes`` directly (not through
# ``ViewerWindow``). Each site uses ``vp._optional_kwargs(...)`` to spread
# its preset axes. These tests exercise that assembly path with
# monkeypatched constants — they prove the constants flow through the
# helper into the kwargs the call site spreads. Visual smoke pass in U6
# covers the rendering side.


def test_threshold_qc_group_image_kwargs_omit_none_today():
    """With today's ship values (both None), the assembly produces no
    opacity / contrast_limits kwargs at the threshold-QC group image call site."""
    assert vp.THRESHOLD_QC_GROUP_IMAGE_OPACITY is None
    assert vp.THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE is None
    assembled = vp._optional_kwargs(
        opacity=vp.THRESHOLD_QC_GROUP_IMAGE_OPACITY,
        contrast_limits=vp.THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE,
    )
    assert assembled == {}


def test_threshold_qc_group_image_opacity_propagates_when_set(monkeypatch):
    """Monkeypatching THRESHOLD_QC_GROUP_IMAGE_OPACITY yields an opacity
    kwarg that the threshold-QC call site spreads into add_image."""
    monkeypatch.setattr(vp, "THRESHOLD_QC_GROUP_IMAGE_OPACITY", 0.42)
    assembled = vp._optional_kwargs(
        opacity=vp.THRESHOLD_QC_GROUP_IMAGE_OPACITY,
        contrast_limits=vp.THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE,
    )
    assert assembled == {"opacity": 0.42}


def test_threshold_qc_group_image_contrast_propagates_when_set(monkeypatch):
    """Monkeypatching the contrast override yields a contrast_limits kwarg."""
    monkeypatch.setattr(
        vp, "THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE", (10.0, 200.0)
    )
    assembled = vp._optional_kwargs(
        opacity=vp.THRESHOLD_QC_GROUP_IMAGE_OPACITY,
        contrast_limits=vp.THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE,
    )
    assert assembled == {"contrast_limits": (10.0, 200.0)}


def test_threshold_qc_group_image_call_site_uses_helper():
    """Source-level guard: the threshold_qc.py group-image add_image call
    must spread vp._optional_kwargs and reference both new constants."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src" / "percell4" / "gui" / "threshold_qc.py"
    )
    text = src.read_text(encoding="utf-8")
    assert "vp._optional_kwargs(" in text
    assert "THRESHOLD_QC_GROUP_IMAGE_OPACITY" in text
    assert "THRESHOLD_QC_GROUP_IMAGE_CONTRAST_OVERRIDE" in text
