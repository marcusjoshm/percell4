"""U3: Modify Channel group — clip-and-stretch LUT preview.

The Modify Channel group lets the user run Cellpose against an
in-memory clipped+stretched version of the segmentation channel
without persisting the modification to /intensity. The preview is
visible in the napari viewer while the group is expanded; collapsing
reverts pixel-for-pixel.

Covers AE3 and AE5 from
``docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.segmentation.preprocess import apply_lut as _apply_lut
from percell4.gui.viewer import ViewerWindow
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.gui.workflows.single_cell.seg_qc import (
    _LAYER_IMAGE,
    SegmentationQCController,
)
from percell4.model import CellDataModel
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    EdgeMode,
    WorkflowDatasetEntry,
)

# Builds a real napari viewer, so this module carries the ``napari_viewer``
# marker: skipped by default (see pyproject addopts), run explicitly on CI.
pytestmark = pytest.mark.napari_viewer


def _make_dim_dataset(path: Path) -> np.ndarray:
    """Tiny .h5 with a heavy-tail intensity distribution.

    Most pixels are dim background (~10–50), a small cluster mid-range
    (~600), and a tiny tail of bright outliers (~60_000). Mimics the
    real microscopy data that motivated the Modify Channel feature.
    Returns the channel-0 intensity for in-test comparison.
    """
    rng = np.random.default_rng(7)
    H = W = 64
    bg = rng.integers(low=5, high=60, size=(H, W), dtype=np.int32)
    intensity_ch0 = bg.copy()
    # Inject a few "cell" regions at mid intensity.
    intensity_ch0[10:20, 10:20] = 600
    intensity_ch0[30:38, 35:42] = 750
    intensity_ch0[45:55, 12:22] = 900
    # And a few outlier hot pixels.
    intensity_ch0[3, 3] = 60000
    intensity_ch0[60, 60] = 65000

    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["mNG"]})
    arr = intensity_ch0.astype(np.float32)
    store.write_array("intensity", arr, attrs={"dims": ["H", "W"]})
    # Some non-empty labels so QC doesn't take the empty-recovery path.
    labels = np.zeros((H, W), dtype=np.int32)
    labels[10:20, 10:20] = 1
    labels[30:38, 35:42] = 2
    store.write_labels("cellpose_qc", labels)
    return arr


@pytest.fixture
def viewer_win():
    win = ViewerWindow(CellDataModel(session=Session()))
    yield win
    try:
        win.viewer.close()
    except Exception:
        pass


@pytest.fixture
def controller(tmp_path, viewer_win):
    path = tmp_path / "dim.h5"
    original = _make_dim_dataset(path)
    entry = WorkflowDatasetEntry(
        name="dim",
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["mNG"],
    )
    ctrl = SegmentationQCController(
        viewer_win=viewer_win,
        entry=entry,
        queue_index=0,
        queue_total=1,
        on_complete=lambda r: None,
        channel_idx=0,
        seg_name="cellpose_qc",
        cellpose_settings=CellposeSettings(diameter=30, gpu=False),
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        edge_margin_px=0,
    )
    ctrl.start()
    yield ctrl, path, original
    ctrl._finish(PhaseResult(success=True, message="test teardown"))


# ── Pure-function tests for _apply_lut ─────────────────────────────


def test_apply_lut_basic_int_dtype():
    """clip + stretch maps [lo, hi] to [0, dtype_max]."""
    arr = np.array([0, 200, 500, 1000, 2000], dtype=np.uint16)
    out = _apply_lut(arr, lo=0, hi=1000)
    # Below lo clamps to 0; above hi clamps to uint16 max (65535).
    assert out[0] == 0
    assert out[-1] == 65535
    # In-band values stretch linearly: 500 -> 500/1000 * 65535 ≈ 32767
    assert abs(int(out[2]) - 32767) <= 1


def test_apply_lut_degenerate_range_returns_input_unchanged():
    """When hi - lo < epsilon, return the channel as-is (no NaN)."""
    arr = np.zeros((4, 4), dtype=np.uint16)
    out = _apply_lut(arr, lo=0, hi=0)
    assert np.array_equal(out, arr)


def test_apply_lut_float_dtype_normalizes_to_unit():
    """Float input yields output in [0, 1]."""
    arr = np.array([0.0, 100.0, 1000.0, 5000.0], dtype=np.float32)
    out = _apply_lut(arr, lo=0, hi=1000)
    assert out.dtype == np.float32
    assert out[0] == pytest.approx(0.0)
    assert out[-1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(1.0)


# ── Controller-level tests ─────────────────────────────────────────


def test_auto_seeds_hi_at_p99(controller):
    """AE3: clicking Auto with sat=1% sets hi to the 99th percentile."""
    ctrl, _path, original = controller

    # Expand the Modify Channel group.
    ctrl._modify_toggle_btn.setChecked(True)

    # Auto was applied automatically on expand with the default 1% sat.
    expected_hi = float(np.percentile(original.ravel(), 99.0))
    expected_lo = float(original.min())
    assert ctrl._modify_lo_spin.value() == pytest.approx(expected_lo, abs=1e-3)
    assert ctrl._modify_hi_spin.value() == pytest.approx(expected_hi, abs=1e-3)


def test_collapse_reverts_channel_pixel_for_pixel(controller):
    """AE5: collapsing the group restores the layer's exact original data."""
    ctrl, _path, original = controller
    layer = ctrl._viewer_win.viewer.layers[_LAYER_IMAGE]

    # Snapshot the on-load channel data (matches original).
    on_load = np.array(layer.data, copy=True)
    assert np.array_equal(on_load, original)

    # Expand → preview is applied (different from original).
    ctrl._modify_toggle_btn.setChecked(True)
    after_expand = np.array(layer.data, copy=True)
    assert not np.array_equal(after_expand, original), (
        "preview should differ from original after expand"
    )

    # Collapse → layer is byte-identical to original again.
    ctrl._modify_toggle_btn.setChecked(False)
    after_collapse = np.array(layer.data, copy=True)
    assert np.array_equal(after_collapse, original)


def test_handles_cannot_cross(controller):
    """Setting hi below lo snaps hi back to lo + epsilon."""
    ctrl, _path, _original = controller
    ctrl._modify_toggle_btn.setChecked(True)

    ctrl._modify_lo_spin.setValue(500.0)
    ctrl._modify_hi_spin.setValue(200.0)  # tries to go below lo

    lo = ctrl._modify_lo_spin.value()
    hi = ctrl._modify_hi_spin.value()
    assert hi > lo


def test_drawn_labels_survive_expand_collapse_cycle(controller):
    """Modify Channel preview is on the intensity layer only — labels untouched."""
    ctrl, _path, _original = controller
    from percell4.gui.workflows.single_cell.seg_qc import _LAYER_LABELS

    labels_layer = ctrl._viewer_win.viewer.layers[_LAYER_LABELS]
    pre = np.array(labels_layer.data, copy=True)
    assert int(pre.max()) == 2

    ctrl._modify_toggle_btn.setChecked(True)
    mid = np.array(labels_layer.data, copy=True)
    assert np.array_equal(mid, pre)

    ctrl._modify_toggle_btn.setChecked(False)
    post = np.array(labels_layer.data, copy=True)
    assert np.array_equal(post, pre)


def test_on_disk_intensity_byte_identical_after_session(tmp_path, viewer_win):
    """On-disk /intensity must never be mutated by the LUT preview."""
    path = tmp_path / "ondisk.h5"
    original = _make_dim_dataset(path)

    entry = WorkflowDatasetEntry(
        name="dim",
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["mNG"],
    )
    ctrl = SegmentationQCController(
        viewer_win=viewer_win,
        entry=entry,
        queue_index=0,
        queue_total=1,
        on_complete=lambda r: None,
        channel_idx=0,
        seg_name="cellpose_qc",
        cellpose_settings=CellposeSettings(diameter=30, gpu=False),
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        edge_margin_px=0,
    )
    ctrl.start()

    # Expand, tweak, expand again, collapse, finish — never write to disk.
    ctrl._modify_toggle_btn.setChecked(True)
    ctrl._modify_sat_spin.setValue(5.0)
    ctrl._on_modify_auto_clicked()
    ctrl._modify_toggle_btn.setChecked(False)
    ctrl._finish(PhaseResult(success=True, message="test teardown"))

    persisted = DatasetStore(path).read_array("intensity")
    assert np.array_equal(persisted, original)


def test_saturation_recompute_changes_hi(controller):
    """Changing Saturation% + clicking Auto recomputes hi at the new percentile."""
    ctrl, _path, original = controller
    ctrl._modify_toggle_btn.setChecked(True)

    initial_hi = ctrl._modify_hi_spin.value()

    ctrl._modify_sat_spin.setValue(10.0)
    ctrl._on_modify_auto_clicked()
    new_hi = ctrl._modify_hi_spin.value()

    expected = float(np.percentile(original.ravel(), 90.0))
    assert new_hi == pytest.approx(expected, abs=1e-3)
    assert new_hi != pytest.approx(initial_hi, abs=1e-3)
