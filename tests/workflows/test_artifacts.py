"""Tests for workflows/artifacts.py — atomic writes and run_config.json round-trip."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from percell4.workflows.artifacts import (
    config_from_dict,
    config_to_dict,
    create_run_folder,
    read_run_config,
    write_atomic,
    write_run_config,
)
from percell4.workflows.failures import DatasetFailure, FailureRecord
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    GmmCriterion,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)


def _sample_config() -> WorkflowConfig:
    return WorkflowConfig(
        datasets=[
            WorkflowDatasetEntry(
                name="DS1",
                source=DatasetSource.H5_EXISTING,
                h5_path=Path("/tmp/DS1.h5"),
                channel_names=["GFP", "RFP", "DAPI"],
            ),
            WorkflowDatasetEntry(
                name="DS2",
                source=DatasetSource.TIFF_PENDING,
                h5_path=Path("/tmp/DS2.h5"),
                channel_names=["GFP", "RFP"],
                compress_plan={"source_dir": "/tmp/tiffs"},
            ),
        ],
        cellpose=CellposeSettings(
            diameter=25.0, gpu=False, batch_size=16
        ),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_bright",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
                gmm_criterion=GmmCriterion.SILHOUETTE,
                gmm_max_components=5,
            ),
            ThresholdingRound(
                name="RFP_pos",
                channel="RFP",
                metric="integrated_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=4,
                gaussian_sigma=2.0,
            ),
        ],
        selected_csv_columns=["GFP_mean_intensity", "RFP_integrated_intensity"],
        output_parent=Path("/tmp/percell4_runs"),
    )


def _sample_metadata(run_folder: Path) -> RunMetadata:
    return RunMetadata(
        run_id="run_test_deadbeef",
        run_folder=run_folder,
        started_at=datetime(2026, 4, 10, 14, 30, 22, tzinfo=UTC),
        intersected_channels=["GFP", "RFP"],
        failures=[
            FailureRecord(
                dataset_name="DS3",
                phase_name="segment",
                failure=DatasetFailure.SEGMENTATION_EMPTY,
                message="no cells detected",
                ts=datetime(2026, 4, 10, 14, 35, 0, tzinfo=UTC),
            )
        ],
    )


# ── write_atomic ─────────────────────────────────────────────


def test_write_atomic_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    def _writer(tmp: Path) -> None:
        tmp.write_text("hello")

    write_atomic(target, _writer)

    assert target.read_text() == "hello"
    assert not target.with_suffix(".txt.tmp").exists()


def test_write_atomic_cleans_tmp_on_error(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    def _writer(tmp: Path) -> None:
        tmp.write_text("partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_atomic(target, _writer)

    assert not target.exists()
    assert not target.with_suffix(".txt.tmp").exists()


def test_write_atomic_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old")

    write_atomic(target, lambda tmp: tmp.write_text("new"))

    assert target.read_text() == "new"


def test_write_atomic_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "out.txt"
    write_atomic(target, lambda tmp: tmp.write_text("x"))
    assert target.read_text() == "x"


# ── create_run_folder ────────────────────────────────────────


def test_create_run_folder(tmp_path: Path) -> None:
    folder = create_run_folder(tmp_path)
    assert folder.exists()
    assert folder.parent == tmp_path
    assert folder.name.startswith("run_")
    assert (folder / "per_dataset").is_dir()
    assert (folder / "staging").is_dir()


def test_create_run_folder_two_runs_do_not_collide(tmp_path: Path) -> None:
    a = create_run_folder(tmp_path)
    b = create_run_folder(tmp_path)
    assert a != b
    assert a.exists() and b.exists()


# ── config_to_dict / config_from_dict ────────────────────────


def test_config_roundtrip_dict() -> None:
    cfg = _sample_config()
    data = config_to_dict(cfg)
    # JSON-safe: should serialize without errors
    blob = json.dumps(data)
    loaded = json.loads(blob)

    restored = config_from_dict(loaded)

    # Sanity: full structural equality via fields
    assert restored.schema_version == cfg.schema_version
    assert restored.output_parent == cfg.output_parent
    assert isinstance(restored.output_parent, Path)

    assert len(restored.datasets) == len(cfg.datasets)
    for r_ds, orig_ds in zip(restored.datasets, cfg.datasets):
        assert r_ds.name == orig_ds.name
        assert r_ds.source == orig_ds.source
        assert r_ds.h5_path == orig_ds.h5_path
        assert isinstance(r_ds.h5_path, Path)
        assert r_ds.channel_names == orig_ds.channel_names
        assert r_ds.compress_plan == orig_ds.compress_plan

    assert restored.cellpose == cfg.cellpose

    assert len(restored.thresholding_rounds) == len(cfg.thresholding_rounds)
    for r_rnd, orig_rnd in zip(
        restored.thresholding_rounds, cfg.thresholding_rounds
    ):
        assert r_rnd == orig_rnd


def test_config_from_dict_runs_validation() -> None:
    cfg = _sample_config()
    data = config_to_dict(cfg)
    # Corrupt a round name
    data["thresholding_rounds"][0]["name"] = "has space"
    with pytest.raises(ValueError, match="round name"):
        config_from_dict(data)


# ── write_run_config / read_run_config ───────────────────────


def test_run_config_roundtrip_on_disk(tmp_path: Path) -> None:
    folder = create_run_folder(tmp_path)
    cfg = _sample_config()
    meta = _sample_metadata(folder)

    write_run_config(folder, cfg, meta)
    assert (folder / "run_config.json").exists()

    loaded_cfg, loaded_meta = read_run_config(folder)

    assert loaded_cfg.output_parent == cfg.output_parent
    assert len(loaded_cfg.thresholding_rounds) == 2
    assert loaded_cfg.thresholding_rounds[1].gaussian_sigma == 2.0

    assert loaded_meta.run_id == meta.run_id
    assert loaded_meta.started_at == meta.started_at
    assert loaded_meta.intersected_channels == meta.intersected_channels
    assert len(loaded_meta.failures) == 1
    assert loaded_meta.failures[0].failure == DatasetFailure.SEGMENTATION_EMPTY
    assert loaded_meta.failures[0].ts == meta.failures[0].ts


def test_run_config_file_is_written_atomically(tmp_path: Path) -> None:
    folder = create_run_folder(tmp_path)
    cfg = _sample_config()
    meta = _sample_metadata(folder)

    write_run_config(folder, cfg, meta)

    # No .tmp residue
    assert not (folder / "run_config.json.tmp").exists()
