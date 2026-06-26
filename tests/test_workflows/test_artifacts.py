"""Tests for workflows/artifacts.py — atomic writes and run_config.json round-trip."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from percell4.workflows.artifacts import (
    _cellpose_from_dict,
    _round_from_dict,
    _round_to_dict,
    config_from_dict,
    config_to_dict,
    create_run_folder,
    read_run_config,
    write_atomic,
    write_run_config,
)
from percell4.workflows.failures import DatasetFailure, FailureRecord
from percell4.workflows.models import (
    AdaptiveClipSettings,
    AutoExtractSettings,
    CellposeSettings,
    CnrClassifySettings,
    DatasetSource,
    GmmCriterion,
    PunctaDetectorSettings,
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
        cellpose=CellposeSettings(diameter=25.0, gpu=False, flow_threshold=0.5),
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


def test_create_run_folder_custom_prefix(tmp_path: Path) -> None:
    folder = create_run_folder(tmp_path, prefix="flim_fret_run")
    assert folder.name.startswith("flim_fret_run_")
    assert folder.exists()


def test_create_run_folder_skip_subdirs(tmp_path: Path) -> None:
    folder = create_run_folder(tmp_path, prefix="flim_fret_run", create_subdirs=False)
    assert folder.exists()
    # FLIM-FRET workflow writes a single combined CSV directly under the
    # run folder; it has no use for per_dataset/ or staging/.
    assert not (folder / "per_dataset").exists()
    assert not (folder / "staging").exists()


def test_create_run_folder_default_prefix_keeps_single_cell_behavior(
    tmp_path: Path,
) -> None:
    # Defaults must preserve existing callers (single-cell workflow,
    # base_runner) which call create_run_folder(output_parent) with no
    # kwargs.
    folder = create_run_folder(tmp_path)
    assert folder.name.startswith("run_")
    assert (folder / "per_dataset").is_dir()
    assert (folder / "staging").is_dir()


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
    for r_rnd, orig_rnd in zip(restored.thresholding_rounds, cfg.thresholding_rounds):
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
    (folder / "run_config.json").write_text(json.dumps(_pre_evolution_payload()), encoding="utf-8")

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


# ── PunctaDetectorSettings round-trip (U1) ───────────────────


def test_legacy_round_omits_puncta_key() -> None:
    """A legacy (Otsu) round serializes without the puncta_detector key, so
    existing run_config.json files round-trip byte-identically."""
    r = ThresholdingRound(
        name="GFP_bright",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.GMM,
    )
    assert "puncta_detector" not in _round_to_dict(r)
    assert _round_from_dict(_round_to_dict(r)) == r
    assert _round_from_dict(_round_to_dict(r)).puncta is None


def test_pre_evolution_round_without_puncta_loads_as_otsu() -> None:
    """A round dict that predates puncta detection (no puncta_detector key)
    reconstructs as a legacy Otsu round."""
    legacy_round = {
        "name": "GFP_bright",
        "channel": "GFP",
        "metric": "mean_intensity",
        "algorithm": "gmm",
    }
    r = _round_from_dict(legacy_round)
    assert r.puncta is None


def test_adaptive_clip_round_round_trips() -> None:
    """An adaptive-clip round survives to_dict → from_dict with params intact."""
    r = ThresholdingRound(
        name="SG",
        channel="RFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=2.0,
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.40, k=1.5, presmooth_sigma_px=1.5),
    )
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r
    assert restored.adaptive_clip is not None
    assert restored.adaptive_clip.d_min_um == 0.40
    assert restored.adaptive_clip.k == 1.5
    assert restored.adaptive_clip.presmooth_sigma_px == 1.5


def test_legacy_round_omits_adaptive_clip_key() -> None:
    r = ThresholdingRound(
        name="GFP_bright",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.GMM,
    )
    assert "adaptive_clip" not in _round_to_dict(r)
    assert _round_from_dict(_round_to_dict(r)).adaptive_clip is None


def test_adaptive_clip_mixed_with_legacy_round_round_trips() -> None:
    """A config mixing an adaptive round and a legacy Otsu round round-trips both."""
    from percell4.workflows.artifacts import config_from_dict, config_to_dict

    cfg = _sample_config()
    cfg = replace(
        cfg,
        thresholding_rounds=[
            ThresholdingRound(
                name="ac",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                adaptive_clip=AdaptiveClipSettings(d_min_um=0.14),
            ),
            ThresholdingRound(
                name="otsu",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
            ),
        ],
    )
    restored = config_from_dict(config_to_dict(cfg))
    assert restored.thresholding_rounds[0].adaptive_clip.d_min_um == 0.14
    assert restored.thresholding_rounds[1].adaptive_clip is None


def test_auto_extract_round_round_trips_with_override() -> None:
    """An auto-extract round with a µm override survives to_dict → from_dict."""
    r = ThresholdingRound(
        name="SG",
        channel="RFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=2.0,
        auto_extract=AutoExtractSettings(smallest_particle_um=0.4, presmooth_sigma_px=1.5),
    )
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r
    assert restored.auto_extract is not None
    assert restored.auto_extract.smallest_particle_um == 0.4
    assert restored.auto_extract.presmooth_sigma_px == 1.5


def test_auto_extract_round_round_trips_autodetect() -> None:
    """smallest_particle_um=None (auto-detect) round-trips as None."""
    r = ThresholdingRound(
        name="SG",
        channel="RFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        auto_extract=AutoExtractSettings(),
    )
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r
    assert restored.auto_extract is not None
    assert restored.auto_extract.smallest_particle_um is None


def test_cnr_classify_round_round_trips() -> None:
    """An auto-extract round with cnr_classify round-trips both."""
    r = ThresholdingRound(
        name="SG",
        channel="RFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        auto_extract=AutoExtractSettings(smallest_particle_um=0.4),
        cnr_classify=CnrClassifySettings(threshold=5.0),
    )
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r
    assert restored.cnr_classify is not None
    assert restored.cnr_classify.threshold == 5.0


def test_legacy_round_omits_auto_extract_and_cnr_keys() -> None:
    r = ThresholdingRound(
        name="GFP_bright",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.GMM,
    )
    d = _round_to_dict(r)
    assert "auto_extract" not in d
    assert "cnr_classify" not in d
    restored = _round_from_dict(d)
    assert restored.auto_extract is None
    assert restored.cnr_classify is None


def test_auto_extract_cnr_mixed_config_round_trips() -> None:
    """A config mixing an auto-extract+CNR round and a legacy Otsu round round-trips."""
    from percell4.workflows.artifacts import config_from_dict, config_to_dict

    cfg = _sample_config()
    cfg = replace(
        cfg,
        thresholding_rounds=[
            ThresholdingRound(
                name="ae",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                auto_extract=AutoExtractSettings(smallest_particle_um=0.14),
                cnr_classify=CnrClassifySettings(threshold=4.0),
            ),
            ThresholdingRound(
                name="otsu",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
            ),
        ],
    )
    restored = config_from_dict(config_to_dict(cfg))
    assert restored.thresholding_rounds[0].auto_extract.smallest_particle_um == 0.14
    assert restored.thresholding_rounds[0].cnr_classify.threshold == 4.0
    assert restored.thresholding_rounds[1].auto_extract is None
    assert restored.thresholding_rounds[1].cnr_classify is None


def test_size_unit_round_trips_px() -> None:
    """The px size unit survives the round-trip for both ALC methods."""
    ac = ThresholdingRound(
        name="ac", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        adaptive_clip=AdaptiveClipSettings(d_min_um=3.0, d_min_unit="px"),
    )
    ae = ThresholdingRound(
        name="ae", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        auto_extract=AutoExtractSettings(smallest_particle_um=3.0, smallest_particle_unit="px"),
    )
    assert _round_from_dict(_round_to_dict(ac)).adaptive_clip.d_min_unit == "px"
    assert _round_from_dict(_round_to_dict(ae)).auto_extract.smallest_particle_unit == "px"


def test_legacy_adaptive_round_defaults_size_unit_to_um() -> None:
    """A legacy adaptive_clip dict without d_min_unit reconstructs as µm."""
    legacy = {
        "name": "ac", "channel": "GFP", "metric": "mean_intensity",
        "algorithm": "kmeans", "adaptive_clip": {"d_min_um": 0.4},
    }
    r = _round_from_dict(legacy)
    assert r.adaptive_clip.d_min_unit == "um"


def test_puncta_round_round_trips_with_real_tuple_and_dict_params() -> None:
    """A puncta round with dict-input params and a real tuple scale prior
    round-trips to an equal object (the list/tuple and dict/tuple hazards)."""
    p = PunctaDetectorSettings(
        detector_name="log",
        seed_detector_name="log",
        background_estimator_name="gaussian-peak",
        detector_params={"threshold_rel": 0.1, "k": 2.5},
        min_spot_px=2,
        max_spot_px=20,
        spot_scale_prior=(1.0, 4.0),
    )
    r = ThresholdingRound(
        name="SG",
        channel="RFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        puncta=p,
    )
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r
    # spot_scale_prior must survive the JSON list detour as a float tuple.
    assert restored.puncta is not None
    assert restored.puncta.spot_scale_prior == (1.0, 4.0)
    assert isinstance(restored.puncta.spot_scale_prior, tuple)


def test_puncta_round_round_trips_through_full_config() -> None:
    cfg = replace(
        _sample_config(),
        thresholding_rounds=[
            ThresholdingRound(
                name="SG",
                channel=_sample_config().thresholding_rounds[0].channel,
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
                puncta=PunctaDetectorSettings(detector_name="log", detector_params={"k": 2.5}),
            )
        ],
    )
    restored = config_from_dict(config_to_dict(cfg))
    assert restored == cfg
    assert restored.thresholding_rounds[0].puncta is not None
    assert restored.thresholding_rounds[0].puncta.detector_name == "log"


def test_puncta_round_round_trips_on_disk(tmp_path: Path) -> None:
    folder = create_run_folder(tmp_path)
    cfg = replace(
        _sample_config(),
        thresholding_rounds=[
            ThresholdingRound(
                name="SG",
                channel=_sample_config().thresholding_rounds[0].channel,
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.GMM,
                puncta=PunctaDetectorSettings(
                    detector_name="log",
                    detector_params={"threshold_rel": 0.08},
                    spot_scale_prior=(1.5, 5.0),
                ),
            )
        ],
    )
    meta = _sample_metadata(folder)
    write_run_config(folder, cfg, meta)
    loaded_cfg, _ = read_run_config(folder)
    loaded_round = loaded_cfg.thresholding_rounds[0]
    assert loaded_round.puncta is not None
    assert loaded_round.puncta.detector_name == "log"
    assert loaded_round.puncta.spot_scale_prior == (1.5, 5.0)
    assert dict(loaded_round.puncta.detector_params) == {"threshold_rel": 0.08}


def test_run_seg_qc_on_existing_round_trips():
    cfg = replace(_sample_config(), run_seg_qc_on_existing=False)
    restored = config_from_dict(config_to_dict(cfg))
    assert restored.run_seg_qc_on_existing is False


def test_config_to_dict_includes_run_seg_qc():
    assert config_to_dict(_sample_config())["run_seg_qc_on_existing"] is True


def test_config_from_dict_defaults_run_seg_qc_true_when_absent():
    # Pre-feature run folders have no run_seg_qc_on_existing key → True.
    cfg = replace(_sample_config(), run_seg_qc_on_existing=False)
    data = config_to_dict(cfg)
    data.pop("run_seg_qc_on_existing", None)
    assert config_from_dict(data).run_seg_qc_on_existing is True


def test_existing_mask_fields_round_trip():
    cfg = replace(
        _sample_config(),
        use_existing_masks=True,
        existing_mask_selections={"DS1": ["P-body_mask", "grouped"], "DS2": ["m2"]},
    )
    data = config_to_dict(cfg)
    # The dict must CONTAIN both keys (not merely load without crashing).
    assert data["use_existing_masks"] is True
    assert data["existing_mask_selections"] == {
        "DS1": ["P-body_mask", "grouped"],
        "DS2": ["m2"],
    }
    restored = config_from_dict(data)
    assert restored.use_existing_masks is True
    assert restored.existing_mask_selections == {
        "DS1": ["P-body_mask", "grouped"],
        "DS2": ["m2"],
    }


def test_config_from_dict_defaults_existing_mask_fields_when_absent():
    # Legacy (rounds-present) config has neither key → masks-reuse off,
    # empty selections, and it still loads.
    data = config_to_dict(_sample_config())
    data.pop("use_existing_masks", None)
    data.pop("existing_mask_selections", None)
    restored = config_from_dict(data)
    assert restored.use_existing_masks is False
    assert restored.existing_mask_selections == {}


def test_cellpose_from_dict_defaults_to_cpsam_v2():
    """A cellpose dict without a model uses the current default."""
    c = _cellpose_from_dict({"diameter": 30.0})
    assert c.model == "cpsam_v2"


def test_cellpose_from_dict_coerces_unknown_model(recwarn):
    """An old/dropped model (e.g. a 3.x cyto3 config) is coerced to the default
    with a warning rather than carrying a name no selector offers (R6)."""
    c = _cellpose_from_dict({"model": "cyto3", "diameter": 30.0})
    assert c.model == "cpsam_v2"
    assert any("cyto3" in str(w.message) for w in recwarn.list)


def test_cellpose_from_dict_keeps_valid_model():
    """A still-valid model name round-trips unchanged."""
    assert _cellpose_from_dict({"model": "cpdino"}).model == "cpdino"
