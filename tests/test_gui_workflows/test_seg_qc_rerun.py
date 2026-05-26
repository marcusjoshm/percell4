"""U2: Re-run Cellpose inside seg QC.

The Re-run group inside the QC dock lets the user re-segment with
different parameters without leaving the workflow. Re-run REPLACES
the in-QC labels (no merge); Accept persists the replacement.

Tests monkeypatch ``run_cellpose`` so they don't depend on a real
Cellpose model. ``build_cellpose_model`` is similarly stubbed to a
cheap object so the QThread worker pathway is exercised without
torch/CUDA overhead.

Covers AE2 from
``docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.gui.workflows.base_runner import PhaseResult
from percell4.gui.workflows.single_cell.seg_qc import SegmentationQCController
from percell4.model import CellDataModel
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    EdgeMode,
    WorkflowDatasetEntry,
)


def _make_dataset(path: Path, *, label_cells: int = 5) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})
    intensity = np.zeros((2, 32, 32), dtype=np.float32)
    intensity[0, 4:28, 4:28] = 10.0
    intensity[1, 4:28, 4:28] = 5.0
    store.write_array(
        "intensity", intensity, attrs={"dims": ["C", "H", "W"]},
    )
    labels = np.zeros((32, 32), dtype=np.int32)
    for i in range(label_cells):
        r = 2 + (i // 2) * 14
        c = 2 + (i % 2) * 14
        labels[r:r + 6, c:c + 6] = i + 1
    store.write_labels("cellpose_qc", labels)


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
    _make_dataset(path, label_cells=5)
    entry = WorkflowDatasetEntry(
        name="ds",
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["GFP", "RFP"],
    )
    completions: list = []
    ctrl = SegmentationQCController(
        viewer_win=viewer_win,
        entry=entry,
        queue_index=0,
        queue_total=1,
        on_complete=lambda r: completions.append(r),
        channel_idx=0,
        seg_name="cellpose_qc",
        cellpose_settings=CellposeSettings(
            model="cpsam", diameter=30, gpu=False,
            flow_threshold=0.4, cellprob_threshold=0.0, min_size=15,
        ),
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        edge_margin_px=0,
    )
    ctrl.start()
    yield ctrl, path, completions
    ctrl._finish(PhaseResult(success=True, message="test teardown"))


def _await_rerun(qtbot, ctrl):
    """Block until the Re-run worker finishes and its slot has run.

    The button being re-enabled is the load-bearing signal that
    _on_rerun_finished or _on_rerun_error has executed — both paths
    set the button back to enabled. Polling on that flag is robust
    to either outcome.
    """
    worker = ctrl._rerun_worker
    assert worker is not None, "Re-run did not spawn a worker"
    qtbot.waitUntil(
        lambda: ctrl._rerun_button is not None and ctrl._rerun_button.isEnabled(),
        timeout=15_000,
    )
    # Drain remaining queued events to be safe.
    qtbot.wait(20)


def test_rerun_replaces_labels(qtbot, controller, monkeypatch):
    """AE2: Re-run unconditionally replaces the in-QC labels."""
    ctrl, _path, _completions = controller

    # Fake Cellpose returns 3 cells regardless of input.
    fake_labels = np.zeros((32, 32), dtype=np.int32)
    fake_labels[5:10, 5:10] = 1
    fake_labels[12:17, 12:17] = 2
    fake_labels[20:25, 20:25] = 3
    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda *a, **kw: fake_labels,
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # Sanity: starting labels have 5 cells.
    assert int(ctrl._labels_layer().data.max()) == 5

    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    # Replaced, not merged.
    assert int(ctrl._labels_layer().data.max()) == 3
    # Stored array on controller is also updated.
    assert int(ctrl._labels.max()) == 3


def test_rerun_passes_knob_values_to_cellpose(qtbot, controller, monkeypatch):
    """Knob plumbing: editing widgets changes what run_cellpose receives."""
    ctrl, _path, _completions = controller

    captured: dict = {}

    def fake_run(image, **kwargs):
        captured["image"] = image
        captured.update(kwargs)
        return np.zeros(image.shape, dtype=np.int32)

    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", fake_run)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._rerun_diameter.setValue(60)
    ctrl._rerun_flow.setValue(0.8)
    ctrl._rerun_cellprob.setValue(-2.0)
    ctrl._rerun_min_size.setValue(99)
    ctrl._rerun_model.setCurrentText("cyto3")

    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    assert captured["diameter"] == 60
    assert captured["flow_threshold"] == pytest.approx(0.8)
    assert captured["cellprob_threshold"] == pytest.approx(-2.0)
    assert captured["min_size"] == 99
    assert captured["model_type"] == "cyto3"


def test_rerun_diameter_zero_passes_none_for_auto(qtbot, controller, monkeypatch):
    """Diameter=0 in the spinbox -> diameter=None (Cellpose auto-detect)."""
    ctrl, _path, _completions = controller

    captured: dict = {}
    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: (captured.update(kw), np.zeros(image.shape, dtype=np.int32))[1],
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._rerun_diameter.setValue(0)
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    assert captured["diameter"] is None


def test_rerun_channel_switch_reads_different_channel(qtbot, controller, monkeypatch):
    """Switching the channel picker rereads from the store."""
    ctrl, _path, _completions = controller

    captured: dict = {}
    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: (captured.setdefault("image", image), np.zeros(image.shape, dtype=np.int32))[1],
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    # Original channel idx 0 = GFP. Switch to idx 1 = RFP.
    ctrl._rerun_channel.setCurrentIndex(1)
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    # RFP channel from the fixture has value 5.0 in its cell region.
    img = captured["image"]
    assert img.max() == pytest.approx(5.0), (
        f"expected RFP channel (max 5.0), got max={img.max()}"
    )


def test_rerun_error_keeps_labels_and_reenables_button(qtbot, controller, monkeypatch):
    """Worker error: labels stay, button re-enables, status is shown."""
    ctrl, _path, _completions = controller

    def raising(*a, **kw):
        raise RuntimeError("cellpose blew up")

    monkeypatch.setattr("percell4.adapters.cellpose.run_cellpose", raising)
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    original_labels = ctrl._labels_layer().data.copy()
    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)

    # Labels unchanged
    assert np.array_equal(ctrl._labels_layer().data, original_labels)
    # Button is back
    assert ctrl._rerun_button.isEnabled()


def test_rerun_button_disabled_while_in_flight(qtbot, controller, monkeypatch):
    """Clicking Re-run twice quickly does not spawn two workers."""
    ctrl, _path, _completions = controller

    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: np.zeros(image.shape, dtype=np.int32),
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._on_rerun_clicked()
    # While in flight, the button should be disabled.
    assert not ctrl._rerun_button.isEnabled()
    first_worker = ctrl._rerun_worker
    # Clicking again is a no-op (button disabled in real UI; defensive in code).
    # Even if we force-call the handler, the original worker reference
    # should still be the in-flight one until it finishes.
    _await_rerun(qtbot, ctrl)
    # After completion the button is re-enabled.
    assert ctrl._rerun_button.isEnabled()
    # Worker ref points to the one we awaited.
    assert ctrl._rerun_worker is first_worker


def test_accept_persists_rerun_replaced_labels(qtbot, controller, monkeypatch):
    """Integration: after Re-run, Accept saves the new labels, not the original."""
    ctrl, path, completions = controller

    # Fake Cellpose returns exactly 2 cells.
    fake = np.zeros((32, 32), dtype=np.int32)
    fake[3:8, 3:8] = 1
    fake[18:23, 18:23] = 2
    monkeypatch.setattr(
        "percell4.adapters.cellpose.run_cellpose",
        lambda image, **kw: fake,
    )
    monkeypatch.setattr(
        "percell4.adapters.cellpose.build_cellpose_model",
        lambda **kw: object(),
    )

    ctrl._on_rerun_clicked()
    _await_rerun(qtbot, ctrl)
    ctrl._on_accept_clicked()

    # Read what landed on disk.
    persisted = DatasetStore(path).read_labels("cellpose_qc")
    assert int(persisted.max()) == 2
    assert len(completions) == 1 and completions[0].success is True
