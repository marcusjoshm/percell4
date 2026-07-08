"""U4: Modify Channel preview feeds Re-run Cellpose when active.

When the Modify Channel group is expanded, the napari intensity layer
shows the clipped+stretched preview. ``_cellpose_input_image()`` reads
the layer's current ``.data``, so Re-run naturally receives the
preview as Cellpose input — no extra wiring needed.

Covers AE4 from
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


def _make_dataset(path: Path) -> np.ndarray:
    rng = np.random.default_rng(13)
    H = W = 48
    arr = rng.integers(low=0, high=200, size=(H, W), dtype=np.int32)
    arr[20:30, 20:30] = 800   # "cell"
    arr[5, 5] = 60000          # outlier
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


def test_rerun_with_modify_active_uses_clipped_image(qtbot, controller, monkeypatch):
    """AE4: Modify Channel preview is what Cellpose sees on Re-run."""
    ctrl, _path, original = controller

    captured: dict = {}
    def fake_run(image, **kwargs):
        captured["image"] = np.array(image, copy=True)
        return np.zeros(image.shape, dtype=np.int32)

    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", fake_run)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # Expand Modify Channel; Auto runs with default sat=1%.
    ctrl._modify_toggle_btn.setChecked(True)
    lo = ctrl._modify_lo_spin.value()
    hi = ctrl._modify_hi_spin.value()
    expected = _apply_lut(original, lo, hi)

    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    assert np.array_equal(captured["image"], expected), (
        "Re-run should have received the clipped/stretched preview, not the raw channel"
    )


def test_rerun_with_modify_collapsed_uses_raw_channel(qtbot, controller, monkeypatch):
    """When Modify Channel is collapsed, Re-run uses the raw channel."""
    ctrl, _path, original = controller

    captured: dict = {}
    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: (
            captured.setdefault("image", np.array(image, copy=True)),
            np.zeros(image.shape, dtype=np.int32),
        )[1],
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # Modify Channel never expanded — should pass raw channel.
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    assert np.array_equal(captured["image"], original)


def test_collapse_then_rerun_uses_raw_again(qtbot, controller, monkeypatch):
    """Expand → click Re-run → collapse → Re-run again: second uses raw."""
    ctrl, _path, original = controller

    received = []
    def fake_run(image, **kwargs):
        received.append(np.array(image, copy=True))
        return np.zeros(image.shape, dtype=np.int32)

    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", fake_run)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # First Re-run with Modify active.
    ctrl._modify_toggle_btn.setChecked(True)
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)
    assert not np.array_equal(received[0], original), (
        "first Re-run should NOT match raw — Modify Channel was active"
    )

    # Collapse → second Re-run uses raw.
    ctrl._modify_toggle_btn.setChecked(False)
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)
    assert np.array_equal(received[1], original)


def test_accept_with_modify_active_does_not_persist_modified_channel(
    qtbot, controller, monkeypatch
):
    """Accept after a Re-run with Modify active persists labels but
    leaves /intensity byte-identical to the on-disk original."""
    ctrl, path, original = controller

    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: np.array(
            [[1 if r > 20 and c > 20 else 0 for c in range(image.shape[1])]
             for r in range(image.shape[0])], dtype=np.int32,
        ),
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._modify_toggle_btn.setChecked(True)
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)
    ctrl._on_accept_clicked()

    persisted_intensity = DatasetStore(path).read_array("intensity")
    assert np.array_equal(persisted_intensity, original)

    persisted_labels = DatasetStore(path).read_labels("cellpose_qc")
    # Labels reflect what Cellpose found on the modified image.
    assert int(persisted_labels.max()) >= 1
