"""Apply Visible as Mask must equal what the histogram renders.

Regression coverage for the bug where ``_on_apply_mask`` emitted the raw
ROI-membership mask, ignoring every FLIM-tab filter (active mask,
filter_ids, intensity threshold, reference circle). The user expectation
is "function literally": the saved binary mask must match the visible
phasor histogram pixel-for-pixel.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import (
    PhasorPlotWindow,
    PhasorROI,
)


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    sess = Session()
    sess._dataset = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    return sess


def _wide_phasor_maps(shape=(16, 16)) -> tuple[np.ndarray, np.ndarray]:
    """Build deterministic g/s maps that span a wide phasor region."""
    h, w = shape
    gs = np.linspace(0.05, 0.95, h * w, dtype=np.float32).reshape(shape)
    ss = np.linspace(0.05, 0.45, h * w, dtype=np.float32).reshape(shape)
    return gs, ss


def _add_wide_roi(window: PhasorPlotWindow, name: str = "ROI_test") -> None:
    """Place a single ROI big enough to enclose the entire phasor cloud."""
    roi = PhasorROI(
        name=name,
        center=(0.5, 0.25),
        radii=(0.6, 0.4),
        angle_deg=0,
        label=1,
        color="#ff00ff",
    )
    window._create_roi_widget(roi)


@pytest.fixture
def phasor_window(qtbot, session_with_dataset) -> PhasorPlotWindow:
    repo = MagicMock()
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: repo)
    qtbot.addWidget(win)
    g, s = _wide_phasor_maps()
    win._g_map = g
    win._s_map = s
    win._total_valid_pixels = int((np.isfinite(g) & (g != 0)).sum())
    return win


def _capture_apply(window: PhasorPlotWindow):
    captured: list[list[tuple[str, np.ndarray, str]]] = []
    window.mask_applied.connect(captured.append)
    window._on_apply_mask()
    assert captured, "mask_applied was never emitted"
    return captured[-1]


def test_apply_respects_active_mask_filter(phasor_window, session_with_dataset):
    """Saved binary must be a subset of the active mask when filter is on."""
    sparse = np.zeros((16, 16), dtype=np.uint8)
    sparse[2:6, 2:6] = 1  # 16 pixels of 256

    session_with_dataset.set_active_mask("nucleus")
    phasor_window._mask_filter_check.setChecked(True)
    phasor_window._active_mask_array = sparse
    phasor_window._active_mask_flat = sparse.ravel()

    _add_wide_roi(phasor_window)
    emitted = _capture_apply(phasor_window)

    assert len(emitted) == 1
    _name, binary, _color = emitted[0]
    assert binary.shape == (16, 16)
    # Every emitted "1" pixel must lie inside the active mask.
    assert np.all(binary[sparse == 0] == 0), (
        "Apply Visible as Mask leaked pixels outside the active mask"
    )
    # And the ROI is wide enough to actually catch some of those pixels.
    assert binary.sum() > 0


def test_apply_equals_napari_preview(phasor_window, session_with_dataset):
    """The binary emitted on Apply must equal the binary emitted as preview.

    Same filter configuration, same ROI — saved == preview is the contract.
    """
    sparse = np.zeros((16, 16), dtype=np.uint8)
    sparse[4:12, 4:12] = 1

    session_with_dataset.set_active_mask("nucleus")
    phasor_window._mask_filter_check.setChecked(True)
    phasor_window._active_mask_array = sparse
    phasor_window._active_mask_flat = sparse.ravel()

    _add_wide_roi(phasor_window)

    previews: list[tuple[str, np.ndarray, str, bool]] = []
    phasor_window.preview_roi_upserted.connect(
        lambda name, binary, color, visible: previews.append(
            (name, binary.copy(), color, visible)
        )
    )
    phasor_window._update_preview()
    assert previews, "preview_roi_upserted was never emitted"
    _pname, preview_binary, _pcolor, _pvis = previews[-1]

    emitted = _capture_apply(phasor_window)
    _name, applied_binary, _color = emitted[0]

    np.testing.assert_array_equal(applied_binary, preview_binary)


def test_apply_respects_filter_ids(phasor_window, session_with_dataset):
    """When filter_ids is set, no nonzero pixels may fall on excluded labels."""
    labels = np.zeros((16, 16), dtype=np.int32)
    labels[:8, :] = 1
    labels[8:, :] = 2
    phasor_window._labels = labels
    phasor_window._labels_flat = labels.ravel()

    session_with_dataset.set_filter({1})  # only label 1 cells

    _add_wide_roi(phasor_window)
    emitted = _capture_apply(phasor_window)
    _name, binary, _color = emitted[0]

    assert np.all(binary[labels == 2] == 0), (
        "Apply Visible as Mask included pixels from a filtered-out cell label"
    )
    assert binary[labels == 1].sum() > 0


def test_apply_respects_intensity_threshold(phasor_window):
    """When an intensity threshold is set, sub-threshold pixels must be 0."""
    intensity = np.zeros((16, 16), dtype=np.float32)
    intensity[:, 8:] = 100.0  # right half bright, left half dim
    phasor_window._intensity = intensity
    phasor_window._intensity_threshold = 50.0

    _add_wide_roi(phasor_window)
    emitted = _capture_apply(phasor_window)
    _name, binary, _color = emitted[0]

    assert np.all(binary[intensity < 50.0] == 0)
    assert binary[intensity >= 50.0].sum() > 0


def test_apply_respects_reference_circle(phasor_window):
    """When a reference circle is configured, pixels outside it must be 0."""
    phasor_window._ref_circle_center = (0.5, 0.25)
    phasor_window._ref_circle_radius = 0.1

    _add_wide_roi(phasor_window)
    emitted = _capture_apply(phasor_window)
    _name, binary, _color = emitted[0]

    g = phasor_window._g_map
    s = phasor_window._s_map
    inside = (g - 0.5) ** 2 + (s - 0.25) ** 2 <= 0.1 ** 2
    assert np.all(binary[~inside] == 0), (
        "Apply Visible as Mask leaked pixels outside the reference circle"
    )


def test_apply_with_no_filters_equals_raw_roi(phasor_window):
    """No filters configured → behavior matches the raw ROI membership.

    Pins the no-filter baseline so the new visible-AND path doesn't
    silently shrink the unfiltered case.
    """
    _add_wide_roi(phasor_window)
    widget = phasor_window._roi_widgets[0]

    emitted = _capture_apply(phasor_window)
    _name, binary, _color = emitted[0]

    g = phasor_window._g_map
    expected = np.zeros(g.shape, dtype=np.uint8)
    expected[widget.cached_mask & np.isfinite(g) & (g != 0)] = 1
    np.testing.assert_array_equal(binary, expected)
