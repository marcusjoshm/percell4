"""U2: Re-run Cellpose button inside the Modify Channel group.

The Modify Channel group owns the LUT preview; this button gives the
user a one-click way to segment against that preview without
navigating to the Re-run Cellpose group. Same handler, same worker,
same labels replacement — and the two Re-run buttons toggle their
enabled state in lockstep so concurrent worker spawns are impossible.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.domain.segmentation.preprocess import apply_lut as _apply_lut
from percell4.gui.workflows.single_cell.seg_qc import (
    SegmentationQCController,
    _LAYER_IMAGE,
)
from percell4.model import CellDataModel
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    EdgeMode,
    WorkflowDatasetEntry,
)


def _make_dataset(path: Path) -> np.ndarray:
    rng = np.random.default_rng(31)
    H = W = 48
    arr = rng.integers(low=0, high=200, size=(H, W), dtype=np.int32)
    arr[20:30, 20:30] = 800
    arr[5, 5] = 60000  # outlier so LUT clamps it
    arr_f32 = arr.astype(np.float32)
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["mNG"]})
    store.write_array("intensity", arr_f32, attrs={"dims": ["H", "W"]})
    labels = np.zeros((H, W), dtype=np.int32)
    labels[20:30, 20:30] = 1
    store.write_labels("cellpose_qc", labels)
    return arr_f32


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
    path = tmp_path / "ds.h5"
    original = _make_dataset(path)
    entry = WorkflowDatasetEntry(
        name="ds",
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


def _await_rerun(qtbot, ctrl):
    worker = ctrl._rerun_worker
    assert worker is not None
    qtbot.waitUntil(
        lambda: ctrl._rerun_button is not None and ctrl._rerun_button.isEnabled(),
        timeout=15_000,
    )
    qtbot.wait(20)


def test_modify_rerun_button_exists_after_window_build(controller):
    """The new button is present and labelled."""
    ctrl, _path, _original = controller
    assert ctrl._modify_rerun_button is not None
    assert "Run Cellpose" in ctrl._modify_rerun_button.text()


def test_clicking_modify_rerun_triggers_run_cellpose(
    qtbot, controller, monkeypatch
):
    """Happy path: the Modify Channel button kicks off a worker."""
    ctrl, _path, _original = controller

    calls: list[dict] = []
    def fake_run(image, **kwargs):
        calls.append({"image_shape": image.shape, **kwargs})
        return np.zeros(image.shape, dtype=np.int32)

    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", fake_run)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._modify_rerun_button.click()
    _await_rerun(qtbot, ctrl)

    assert len(calls) == 1, f"expected exactly one Cellpose call, got {len(calls)}"


def test_modify_rerun_uses_rerun_groups_knob_values(
    qtbot, controller, monkeypatch
):
    """Knobs from the Re-run Cellpose group are read at click time."""
    ctrl, _path, _original = controller

    captured: dict = {}
    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: (captured.update(kw), np.zeros(image.shape, dtype=np.int32))[1],
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # Set diameter in the Re-run group's spinbox, then click the
    # Modify Channel button.
    ctrl._rerun_diameter.setValue(42)
    ctrl._modify_rerun_button.click()
    _await_rerun(qtbot, ctrl)

    assert captured["diameter"] == 42


def test_both_buttons_disabled_while_worker_in_flight(
    qtbot, controller, monkeypatch
):
    """Either button click disables both buttons until the worker finishes."""
    ctrl, _path, _original = controller

    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: np.zeros(image.shape, dtype=np.int32),
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._modify_rerun_button.click()
    # Worker is in flight (not yet finished).
    assert not ctrl._rerun_button.isEnabled()
    assert not ctrl._modify_rerun_button.isEnabled()

    _await_rerun(qtbot, ctrl)
    # Both re-enabled after success.
    assert ctrl._rerun_button.isEnabled()
    assert ctrl._modify_rerun_button.isEnabled()


def test_both_buttons_reenabled_after_worker_error(
    qtbot, controller, monkeypatch
):
    """Worker error path also re-enables both buttons."""
    ctrl, _path, _original = controller

    def raising(*a, **kw):
        raise RuntimeError("cellpose blew up")
    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", raising)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._modify_rerun_button.click()
    qtbot.waitUntil(
        lambda: ctrl._modify_rerun_button.isEnabled(),
        timeout=15_000,
    )
    assert ctrl._rerun_button.isEnabled()
    assert ctrl._modify_rerun_button.isEnabled()


def test_both_buttons_lockstep_when_rerun_group_button_clicked(
    qtbot, controller, monkeypatch
):
    """Symmetric: clicking the OTHER Re-run button also disables both."""
    ctrl, _path, _original = controller

    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: np.zeros(image.shape, dtype=np.int32),
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._rerun_button.click()
    assert not ctrl._rerun_button.isEnabled()
    assert not ctrl._modify_rerun_button.isEnabled()
    _await_rerun(qtbot, ctrl)
    assert ctrl._rerun_button.isEnabled()
    assert ctrl._modify_rerun_button.isEnabled()


def test_modify_rerun_with_active_lut_feeds_modified_image(
    qtbot, controller, monkeypatch
):
    """Load-bearing integration: clicking the Modify Channel Re-run
    button with the group expanded passes the clipped/stretched array
    to Cellpose, not the raw on-disk channel."""
    ctrl, _path, original = controller

    captured: dict = {}
    def fake_run(image, **kwargs):
        captured["image"] = np.asarray(image, copy=True)
        return np.zeros(image.shape, dtype=np.int32)
    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", fake_run)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # Expand Modify Channel → installs the preview into the napari
    # channel layer; click the Modify Channel button.
    ctrl._modify_toggle_btn.setChecked(True)
    lo = ctrl._modify_lo_spin.value()
    hi = ctrl._modify_hi_spin.value()
    expected = _apply_lut(original, lo, hi)

    ctrl._modify_rerun_button.click()
    _await_rerun(qtbot, ctrl)

    assert np.array_equal(captured["image"], expected), (
        "Modify Channel Re-run should pass the clipped preview, not the raw channel"
    )
