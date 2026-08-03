"""Optional segmentation QC for segmentations the workflow creates (U4).

``run_seg_qc_on_existing`` gates only the pre-segmented path. A dataset that
Cellpose segments during the run always opened the QC editor, so a batch with
settled Cellpose parameters could not run unattended — it stopped at the first
dataset waiting for a human.

The two flags are separate decisions and must not interfere: reviewing a
segmentation that arrived with the dataset is a different question from
reviewing one this run just produced. Both are subordinate to the runner's
``interactive_qc`` switch.

A skipped QC step is always disclosed in the run log — an unreviewed
segmentation must never be handed downstream silently.
"""

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

SIZE = 20


def _make_h5(path: Path, labels: dict[str, np.ndarray] | None = None) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity",
        np.zeros((SIZE, SIZE), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    for name, arr in (labels or {}).items():
        store.write_labels(name, arr)


def _label() -> np.ndarray:
    arr = np.zeros((SIZE, SIZE), dtype=np.int32)
    arr[5:9, 5:9] = 1
    return arr


def _entry(path: Path, name: str) -> WorkflowDatasetEntry:
    return WorkflowDatasetEntry(
        name=name,
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["GFP"],
    )


def _config(entries, tmp_path, **overrides) -> WorkflowConfig:
    base = dict(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_split",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
                gaussian_sigma=0.0,
            ),
        ],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
        seg_channel_name="GFP",
    )
    base.update(overrides)
    return WorkflowConfig(**base)


def _phases_by_dataset(cfg, *, interactive_qc: bool = True) -> dict[str, list[str]]:
    meta = RunMetadata(
        run_id="r",
        run_folder=Path("/tmp/r"),
        started_at=datetime.now(UTC),
        intersected_channels=["GFP"],
    )
    runner = SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=interactive_qc
    )
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


# ── The new gate ────────────────────────────────────────────────────────


def test_fresh_segmentation_runs_qc_by_default(qtbot, tmp_path):
    """Historical behavior is preserved when the flag is left alone."""
    p = tmp_path / "DS1.h5"
    _make_h5(p)  # no labels -> Cellpose segments it
    by_ds = _phases_by_dataset(_config([_entry(p, "DS1")], tmp_path))

    assert "segment" in by_ds["DS1"]
    assert "seg_qc" in by_ds["DS1"]


def test_fresh_segmentation_skips_qc_when_flag_off(qtbot, tmp_path):
    """The whole point: an unattended run over fresh datasets."""
    p = tmp_path / "DS1.h5"
    _make_h5(p)
    by_ds = _phases_by_dataset(
        _config(
            [_entry(p, "DS1")], tmp_path, run_seg_qc_on_new_segmentations=False
        )
    )

    assert "segment" in by_ds["DS1"]
    assert "seg_qc" not in by_ds["DS1"]
    # The run still proceeds to thresholding.
    assert any(ph.startswith("threshold") for ph in by_ds["DS1"])


def test_skipping_qc_is_disclosed_in_the_run_log(qtbot, tmp_path):
    """Silence is not an acceptable signal for a skipped QC step."""
    p = tmp_path / "DS1.h5"
    _make_h5(p)
    cfg = _config(
        [_entry(p, "DS1")], tmp_path, run_seg_qc_on_new_segmentations=False
    )
    meta = RunMetadata(
        run_id="r",
        run_folder=Path("/tmp/r"),
        started_at=datetime.now(UTC),
        intersected_channels=["GFP"],
    )
    runner = SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=True
    )
    logged: list[dict] = []
    runner._log = lambda **fields: logged.append(fields)

    list(runner._phase_generator())

    skips = [e for e in logged if e.get("event") == "skipped_no_qc"]
    assert len(skips) == 1, f"expected one disclosure, got {logged}"
    assert skips[0]["dataset"] == "DS1"
    assert "without QC" in skips[0]["message"]


# ── Independence from the existing-segmentation flag ────────────────────


def test_new_flag_does_not_affect_presegmented_datasets(qtbot, tmp_path):
    """Turning off create-QC must leave existing-segmentation QC alone."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, labels={"cellpose": _label()})
    by_ds = _phases_by_dataset(
        _config(
            [_entry(p, "DS1")],
            tmp_path,
            run_seg_qc_on_new_segmentations=False,
            run_seg_qc_on_existing=True,
        )
    )

    assert "segment" not in by_ds["DS1"]
    assert "seg_qc" in by_ds["DS1"], (
        "the pre-existing-segmentation QC step must be unaffected"
    )


def test_existing_flag_does_not_affect_fresh_segmentations(qtbot, tmp_path):
    """And the reverse: turning off existing-QC leaves create-QC alone."""
    p = tmp_path / "DS1.h5"
    _make_h5(p)  # no labels -> fresh Cellpose
    by_ds = _phases_by_dataset(
        _config(
            [_entry(p, "DS1")],
            tmp_path,
            run_seg_qc_on_existing=False,
            run_seg_qc_on_new_segmentations=True,
        )
    )

    assert "seg_qc" in by_ds["DS1"]


def test_both_flags_off_yields_no_seg_qc_anywhere(qtbot, tmp_path):
    fresh = tmp_path / "FRESH.h5"
    presegmented = tmp_path / "PRESEG.h5"
    _make_h5(fresh)
    _make_h5(presegmented, labels={"cellpose": _label()})
    by_ds = _phases_by_dataset(
        _config(
            [_entry(fresh, "FRESH"), _entry(presegmented, "PRESEG")],
            tmp_path,
            run_seg_qc_on_existing=False,
            run_seg_qc_on_new_segmentations=False,
        )
    )

    assert "seg_qc" not in by_ds["FRESH"]
    assert "seg_qc" not in by_ds["PRESEG"]


# ── Subordinate to interactive_qc ───────────────────────────────────────


def test_headless_run_is_unaffected_by_the_new_flag(qtbot, tmp_path):
    """A headless run never yields QC either way."""
    p = tmp_path / "DS1.h5"
    _make_h5(p)
    for flag in (True, False):
        by_ds = _phases_by_dataset(
            _config(
                [_entry(p, "DS1")],
                tmp_path,
                run_seg_qc_on_new_segmentations=flag,
            ),
            interactive_qc=False,
        )
        assert "seg_qc" not in by_ds["DS1"]


def test_failed_segment_yields_no_qc_regardless_of_flag(qtbot, tmp_path):
    """The existing failed-dataset guard still applies when the flag is on."""
    from percell4.workflows.failures import DatasetFailure
    from percell4.workflows.phases import record_failure

    p = tmp_path / "DS1.h5"
    _make_h5(p)
    cfg = _config([_entry(p, "DS1")], tmp_path)
    meta = RunMetadata(
        run_id="r",
        run_folder=Path("/tmp/r"),
        started_at=datetime.now(UTC),
        intersected_channels=["GFP"],
    )
    runner = SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=True
    )

    phases: list[str] = []
    gen = runner._phase_generator()
    for req in gen:
        phases.append(req.phase_name)
        if req.phase_name == "segment":
            record_failure(
                meta,
                dataset_name="DS1",
                phase_name="segment",
                failure=DatasetFailure.SEGMENTATION_EMPTY,
                message="no cells",
            )

    assert "seg_qc" not in phases


# ── Serialization ───────────────────────────────────────────────────────


def test_flag_round_trips_through_run_config(tmp_path):
    from percell4.workflows.artifacts import config_from_dict, config_to_dict

    cfg = _config(
        [_entry(tmp_path / "x.h5", "DS1")],
        tmp_path,
        run_seg_qc_on_new_segmentations=False,
    )
    restored = config_from_dict(config_to_dict(cfg))
    assert restored.run_seg_qc_on_new_segmentations is False


def test_legacy_run_config_defaults_to_true(tmp_path):
    """Pre-feature run folders had every fresh segmentation QC'd."""
    from percell4.workflows.artifacts import config_from_dict, config_to_dict

    blob = config_to_dict(_config([_entry(tmp_path / "x.h5", "DS1")], tmp_path))
    blob.pop("run_seg_qc_on_new_segmentations")

    assert config_from_dict(blob).run_seg_qc_on_new_segmentations is True
