"""Generator auto-skips Cellpose for datasets with existing segmentation (U13)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner
from percell4.store import DatasetStore
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)


def _make_h5(path: Path, labels: dict[str, np.ndarray] | None = None,
             *, n_timepoints: int = 1):
    store = DatasetStore(path)
    meta = {"channel_names": ["GFP"]}
    if n_timepoints > 1:
        meta["n_timepoints"] = n_timepoints
        store.create(metadata=meta)
        store.write_array(
            "intensity",
            np.zeros((n_timepoints, 20, 20), dtype=np.float32),
            attrs={"dims": ["T", "H", "W"]},
        )
    else:
        store.create(metadata=meta)
        store.write_array("intensity", np.zeros((20, 20), dtype=np.float32),
                          attrs={"dims": ["H", "W"]})
    for name, arr in (labels or {}).items():
        store.write_labels(name, arr)


def _2d_label(size: int = 20) -> np.ndarray:
    arr = np.zeros((size, size), dtype=np.int32)
    arr[5:9, 5:9] = 1
    return arr


def _config(entries, tmp_path, **overrides):
    base = dict(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_split", channel="GFP", metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
                gaussian_sigma=0.0,
            ),
        ],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
    )
    base.update(overrides)
    return WorkflowConfig(**base)


def _runner(cfg, *, interactive_qc: bool = False, overrides=None):
    meta = RunMetadata(run_id="r", run_folder=Path("/tmp/r"),
                       started_at=datetime.now(UTC), intersected_channels=["GFP"])
    return SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=interactive_qc,
        segmentation_overrides=overrides,
    )


def _entry(path: Path, name: str) -> WorkflowDatasetEntry:
    return WorkflowDatasetEntry(
        name=name, source=DatasetSource.H5_EXISTING,
        h5_path=path, channel_names=["GFP"],
    )


def _first_threshold_idx(phases: list[str]) -> int:
    for i, ph in enumerate(phases):
        if ph.startswith("threshold"):
            return i
    raise AssertionError(f"no threshold phase in {phases}")


def _phases_by_dataset(runner):
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


def test_presegmented_dataset_skips_segment(qtbot, tmp_path):
    p = tmp_path / "DS1.h5"
    cell = np.zeros((20, 20), dtype=np.int32)
    cell[5:9, 5:9] = 1
    _make_h5(p, labels={"cellpose": cell})
    cfg = _config(
        [WorkflowDatasetEntry(name="DS1", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    runner = _runner(cfg)

    by_ds = _phases_by_dataset(runner)

    assert "segment" not in by_ds.get("DS1", [])
    assert "seg_qc" not in by_ds.get("DS1", [])
    assert runner._effective_seg["DS1"] == "cellpose"


def test_unsegmented_dataset_still_segments(qtbot, tmp_path):
    p = tmp_path / "DS2.h5"
    _make_h5(p, labels=None)  # no /labels
    cfg = _config(
        [WorkflowDatasetEntry(name="DS2", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    runner = _runner(cfg)

    by_ds = _phases_by_dataset(runner)

    assert "segment" in by_ds.get("DS2", [])
    assert "DS2" not in runner._effective_seg


def test_tracked_layer_preferred_when_present(qtbot, tmp_path):
    p = tmp_path / "DS3.h5"
    cell = np.zeros((20, 20), dtype=np.int32)
    cell[5:9, 5:9] = 1
    _make_h5(p, labels={"cellpose": cell, "cellpose_tracked": cell})
    cfg = _config(
        [WorkflowDatasetEntry(name="DS3", source=DatasetSource.H5_EXISTING,
                              h5_path=p, channel_names=["GFP"])],
        tmp_path,
    )
    runner = _runner(cfg)

    by_ds = _phases_by_dataset(runner)

    assert "segment" not in by_ds.get("DS3", [])
    assert runner._effective_seg["DS3"] == "cellpose_tracked"


# ── Optional seg-QC on pre-segmented datasets (interactive mode) ────────


def test_presegmented_runs_seg_qc_when_interactive_and_flag_on(qtbot, tmp_path):
    # Default run_seg_qc_on_existing=True + interactive: a pre-segmented
    # single-frame dataset gets a seg-QC pass on its existing layer before
    # thresholding (but never re-segments).
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels={"cellpose": _2d_label()})
    cfg = _config([_entry(p, "DS1")], tmp_path)
    runner = _runner(cfg, interactive_qc=True)
    by_ds = _phases_by_dataset(runner)
    assert "segment" not in by_ds["DS1"]
    assert "seg_qc" in by_ds["DS1"]
    assert by_ds["DS1"].index("seg_qc") < _first_threshold_idx(by_ds["DS1"])


def test_presegmented_skips_seg_qc_when_flag_off(qtbot, tmp_path):
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels={"cellpose": _2d_label()})
    cfg = _config([_entry(p, "DS1")], tmp_path, run_seg_qc_on_existing=False)
    runner = _runner(cfg, interactive_qc=True)
    by_ds = _phases_by_dataset(runner)
    assert "segment" not in by_ds["DS1"]
    assert "seg_qc" not in by_ds["DS1"]
    assert any(ph.startswith("threshold") for ph in by_ds["DS1"])


def test_presegmented_headless_never_yields_seg_qc(qtbot, tmp_path):
    # R6: headless never yields interactive phases, flag notwithstanding.
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels={"cellpose": _2d_label()})
    cfg = _config([_entry(p, "DS1")], tmp_path)  # flag defaults True
    runner = _runner(cfg, interactive_qc=False)
    by_ds = _phases_by_dataset(runner)
    assert "seg_qc" not in by_ds["DS1"]


def test_presegmented_tracked_layer_skips_seg_qc(qtbot, tmp_path):
    # A *_tracked layer's values are track ids tied to /tracks lineage;
    # the raw-label QC tools would desync it, so seg-QC is skipped even
    # though the layer is 2D and the flag is on.
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels={"cellpose_tracked": _2d_label()})
    cfg = _config([_entry(p, "DS1")], tmp_path)  # flag defaults True
    runner = _runner(cfg, interactive_qc=True)
    by_ds = _phases_by_dataset(runner)
    assert runner._effective_seg["DS1"] == "cellpose_tracked"
    assert "seg_qc" not in by_ds["DS1"]


def test_presegmented_timelapse_stack_skips_seg_qc(qtbot, tmp_path):
    # The single-frame seg-QC editor can't edit a (T, H, W) labels stack,
    # so a pre-segmented time-lapse dataset skips seg-QC.
    p = tmp_path / "DS1.h5"
    tl_labels = np.zeros((3, 20, 20), dtype=np.int32)
    tl_labels[:, 5:9, 5:9] = 1
    _make_h5(p, labels={"cellpose": tl_labels}, n_timepoints=3)
    cfg = _config([_entry(p, "DS1")], tmp_path)  # flag defaults True
    runner = _runner(cfg, interactive_qc=True)
    by_ds = _phases_by_dataset(runner)
    assert "seg_qc" not in by_ds["DS1"]


def test_presegmented_2d_gate_on_timelapse_runs_seg_qc(qtbot, tmp_path):
    # A 2D whole-field gate (e.g. percell4-batch-whole-field) on a
    # time-lapse dataset IS editable in the single-frame editor, so it
    # runs seg-QC — the guard keys on label rank, not n_timepoints.
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels={"cellpose": _2d_label()}, n_timepoints=3)
    cfg = _config([_entry(p, "DS1")], tmp_path)  # flag defaults True
    runner = _runner(cfg, interactive_qc=True)
    by_ds = _phases_by_dataset(runner)
    assert "seg_qc" in by_ds["DS1"]


def test_presegmented_missing_override_layer_skips_seg_qc(qtbot, tmp_path):
    # A segmentation_overrides entry naming a layer not on disk must NOT
    # open seg-QC (which would error inside the controller). It falls
    # through to thresholding, which records the missing-layer failure.
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels=None)  # no labels on disk
    cfg = _config([_entry(p, "DS1")], tmp_path)  # flag defaults True
    runner = _runner(
        cfg, interactive_qc=True, overrides={"DS1": "does_not_exist"}
    )
    by_ds = _phases_by_dataset(runner)
    assert "seg_qc" not in by_ds["DS1"]
    assert "segment" not in by_ds["DS1"]  # override treated as pre-segmented


def test_fresh_dataset_seg_qc_unaffected_by_flag(qtbot, tmp_path):
    # R4: the flag governs only pre-segmented datasets. A fresh-Cellpose
    # dataset yields segment then seg_qc for BOTH flag values.
    for flag in (True, False):
        p = tmp_path / f"DS_{flag}.h5"
        _make_h5(p, labels=None)  # no labels → fresh Cellpose path
        cfg = _config(
            [_entry(p, f"DS_{flag}")], tmp_path,
            run_seg_qc_on_existing=flag,
        )
        runner = _runner(cfg, interactive_qc=True)
        by_ds = _phases_by_dataset(runner)
        assert "segment" in by_ds[f"DS_{flag}"]
        assert "seg_qc" in by_ds[f"DS_{flag}"]
        assert (
            by_ds[f"DS_{flag}"].index("segment")
            < by_ds[f"DS_{flag}"].index("seg_qc")
        )
