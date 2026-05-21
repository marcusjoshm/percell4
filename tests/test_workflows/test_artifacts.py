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
            diameter=25.0, gpu=False, flow_threshold=0.5
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
    assert not target.with_name(target.name + ".tmp").exists()


def test_write_atomic_multi_dot_path_no_sibling_collision(tmp_path: Path) -> None:
    """``measurements.parquet.gz`` must not clobber ``measurements.parquet``."""
    sibling = tmp_path / "measurements.parquet"
    sibling.write_text("sibling content")
    target = tmp_path / "measurements.parquet.gz"

    write_atomic(target, lambda tmp: tmp.write_text("new content"))

    assert target.read_text() == "new content"
    assert sibling.read_text() == "sibling content"  # untouched
    assert not (tmp_path / "measurements.parquet.tmp").exists()


def test_write_atomic_cleans_tmp_on_error(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    def _writer(tmp: Path) -> None:
        tmp.write_text("partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_atomic(target, _writer)

    assert not target.exists()
    assert not target.with_suffix(".txt.tmp").exists()


def test_write_atomic_fsync_reopen_is_writable(tmp_path: Path, monkeypatch) -> None:
    """Regression: the post-write fsync must reopen the tmp file with a
    writable handle. On Windows os.fsync maps to FlushFileBuffers, which
    rejects a read-only ("rb") descriptor with EBADF ("Bad file descriptor").
    """
    import builtins

    real_open = builtins.open
    binary_modes: list[str] = []

    def _spy_open(file, mode="r", *args, **kwargs):
        if str(file).endswith(".tmp") and "b" in mode:
            binary_modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _spy_open)

    target = tmp_path / "out.txt"
    write_atomic(target, lambda tmp: tmp.write_text("hello"))

    assert binary_modes, "expected a binary reopen of the tmp file for fsync"
    for mode in binary_modes:
        assert "+" in mode or "w" in mode or "a" in mode, (
            f"fsync reopen used non-writable mode {mode!r}; "
            "os.fsync raises EBADF on a read-only handle on Windows"
        )


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


# ── Pre-evolution back-compat (characterization-first) ───────
#
# These tests pin the load behavior for run_config.json files produced by
# the workflow BEFORE the EdgeMode / DiluteSettings / per_dataset_dilute_round_counts
# evolution lands. They are the load-time safety net for Resume on
# pre-existing run folders.


def _pre_evolution_payload() -> dict:
    """A run_config.json payload in the pre-EdgeMode/DiluteSettings shape.

    Constructed by hand to match the serializer's output shape PRIOR to
    the schema evolution: no ``edge_mode`` key on ``config``, no
    ``dilute_settings`` key on ``config``, no ``per_dataset_dilute_round_counts``
    key on ``metadata``.
    """
    return {
        "config": {
            "datasets": [
                {
                    "name": "DS1",
                    "source": "h5_existing",
                    "h5_path": "/tmp/DS1.h5",
                    "channel_names": ["GFP", "RFP"],
                    "compress_plan": None,
                },
            ],
            "cellpose": {
                "model": "cpsam",
                "diameter": 30.0,
                "gpu": True,
                "flow_threshold": 0.4,
                "cellprob_threshold": 0.0,
                "min_size": 15,
            },
            "thresholding_rounds": [
                {
                    "name": "GFP_bright",
                    "channel": "GFP",
                    "metric": "mean_intensity",
                    "algorithm": "gmm",
                    "gmm_criterion": "bic",
                    "gmm_max_components": 4,
                    "kmeans_n_clusters": 3,
                    "gaussian_sigma": 1.0,
                },
            ],
            "selected_csv_columns": ["GFP_mean_intensity"],
            "output_parent": "/tmp/percell4_runs",
            "seg_channel_name": "GFP",
        },
        "metadata": {
            "run_id": "run_test_pre_evolution",
            "run_folder": "/tmp/percell4_runs/run_pre_evolution",
            "started_at": "2026-04-10T14:30:22+00:00",
            "finished_at": None,
            "intersected_channels": ["GFP", "RFP"],
            "failures": [],
        },
    }


def test_pre_evolution_config_loads_with_default_edge_mode() -> None:
    """A run_config.json without edge_mode defaults to EXCLUDE."""
    from percell4.workflows.models import EdgeMode

    payload = _pre_evolution_payload()
    cfg = config_from_dict(payload["config"])

    assert cfg.edge_mode is EdgeMode.EXCLUDE


def test_pre_evolution_config_loads_with_no_dilute_settings() -> None:
    """A run_config.json without dilute_settings defaults to None."""
    payload = _pre_evolution_payload()
    cfg = config_from_dict(payload["config"])

    assert cfg.dilute_settings is None


def test_pre_evolution_metadata_loads_with_empty_dilute_round_counts() -> None:
    """A run_config.json without per_dataset_dilute_round_counts defaults to {}."""
    from percell4.workflows.artifacts import metadata_from_dict

    payload = _pre_evolution_payload()
    meta = metadata_from_dict(payload["metadata"])

    assert meta.per_dataset_dilute_round_counts == {}


def test_pre_evolution_run_config_loads_from_disk(tmp_path: Path) -> None:
    """Full path: write a pre-evolution payload to disk, then read_run_config it."""
    from percell4.workflows.models import EdgeMode

    folder = tmp_path / "run_pre_evolution"
    folder.mkdir()
    (folder / "run_config.json").write_text(
        json.dumps(_pre_evolution_payload()), encoding="utf-8"
    )

    cfg, meta = read_run_config(folder)

    assert cfg.edge_mode is EdgeMode.EXCLUDE
    assert cfg.dilute_settings is None
    assert meta.per_dataset_dilute_round_counts == {}
    # Existing fields still load correctly
    assert len(cfg.thresholding_rounds) == 1
    assert cfg.thresholding_rounds[0].name == "GFP_bright"


def test_post_evolution_round_trip_preserves_new_fields(tmp_path: Path) -> None:
    """A run_config.json written with the new fields round-trips through read_run_config."""
    from percell4.workflows.models import DiluteSettings, EdgeMode

    folder = create_run_folder(tmp_path)
    cfg = WorkflowConfig(
        datasets=[
            WorkflowDatasetEntry(
                name="DS1",
                source=DatasetSource.H5_EXISTING,
                h5_path=Path("/tmp/DS1.h5"),
                channel_names=["GFP", "RFP"],
            ),
        ],
        cellpose=CellposeSettings(),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_bright",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
            ),
        ],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=Path("/tmp/percell4_runs"),
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        dilute_settings=DiluteSettings(
            mask_name="dilute",
            dilation_radius_px=3,
            channel="GFP",
            metric="mean_intensity",
            algorithm=ThresholdAlgorithm.GMM,
            gmm_criterion=GmmCriterion.BIC,
            gmm_max_components=4,
            gaussian_sigma=1.0,
        ),
    )
    meta = RunMetadata(
        run_id="run_test_post_evolution",
        run_folder=folder,
        started_at=datetime(2026, 5, 20, 14, 30, 22, tzinfo=UTC),
        per_dataset_dilute_round_counts={"DS1": 3, "DS2": 5},
    )

    write_run_config(folder, cfg, meta)
    loaded_cfg, loaded_meta = read_run_config(folder)

    assert loaded_cfg.edge_mode is EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT
    assert loaded_cfg.dilute_settings is not None
    assert loaded_cfg.dilute_settings.mask_name == "dilute"
    assert loaded_cfg.dilute_settings.dilation_radius_px == 3
    assert loaded_cfg.dilute_settings.channel == "GFP"
    assert loaded_cfg.dilute_settings.algorithm is ThresholdAlgorithm.GMM
    assert loaded_cfg.dilute_settings.gmm_criterion is GmmCriterion.BIC
    assert loaded_meta.per_dataset_dilute_round_counts == {"DS1": 3, "DS2": 5}
