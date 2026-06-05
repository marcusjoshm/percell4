"""Tests for workflow config dataclasses and their validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    DiluteSettings,
    EdgeMode,
    GmmCriterion,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)


def _valid_round(**overrides) -> ThresholdingRound:
    defaults = {
        "name": "GFP_bright",
        "channel": "GFP",
        "metric": "mean_intensity",
        "algorithm": ThresholdAlgorithm.GMM,
    }
    defaults.update(overrides)
    return ThresholdingRound(**defaults)


def _valid_entry(**overrides) -> WorkflowDatasetEntry:
    defaults = {
        "name": "DS1",
        "source": DatasetSource.H5_EXISTING,
        "h5_path": Path("/tmp/DS1.h5"),
        "channel_names": ["GFP", "RFP"],
    }
    defaults.update(overrides)
    return WorkflowDatasetEntry(**defaults)


# ── ThresholdingRound ────────────────────────────────────────


def test_round_accepts_valid_names():
    for name in ["GFP_bright", "GFP-dim", "r1", "Round_2", "_hidden", "AaBb-01"]:
        _valid_round(name=name)


def test_round_rejects_bad_names():
    bad = [
        "",  # empty
        "1round",  # leading digit
        "has space",
        "slash/inside",
        "dot.inside",
        "a" * 41,  # too long
        "-leading-hyphen",
    ]
    for name in bad:
        with pytest.raises(ValueError, match="round name"):
            _valid_round(name=name)


def test_round_rejects_unknown_metric():
    with pytest.raises(ValueError, match="metric must be one of"):
        _valid_round(metric="nonsense")


def test_round_accepts_all_builtin_metrics():
    from percell4.domain.measure.metrics import BUILTIN_METRICS

    for metric in BUILTIN_METRICS:
        _valid_round(metric=metric)


def test_round_rejects_bad_counts():
    with pytest.raises(ValueError, match="gmm_max_components"):
        _valid_round(gmm_max_components=1)
    with pytest.raises(ValueError, match="kmeans_n_clusters"):
        _valid_round(kmeans_n_clusters=1)
    with pytest.raises(ValueError, match="gaussian_sigma"):
        _valid_round(gaussian_sigma=-0.1)


def test_round_rejects_empty_channel():
    with pytest.raises(ValueError, match="channel must be non-empty"):
        _valid_round(channel="")


def test_round_is_frozen():
    r = _valid_round()
    with pytest.raises((AttributeError, TypeError)):
        r.name = "mutated"  # type: ignore[misc]


# ── CellposeSettings ─────────────────────────────────────────


def test_cellpose_defaults():
    c = CellposeSettings()
    assert c.model == "cpsam"
    assert c.diameter == 30.0
    assert c.gpu is True
    assert c.blur_sigma == 0.0


def test_cellpose_rejects_bad_values():
    with pytest.raises(ValueError, match="diameter"):
        CellposeSettings(diameter=-1)
    with pytest.raises(ValueError, match="min_size"):
        CellposeSettings(min_size=-1)
    with pytest.raises(ValueError, match="blur_sigma"):
        CellposeSettings(blur_sigma=-1)


def test_cellpose_accepts_blur_sigma():
    c = CellposeSettings(blur_sigma=1.5)
    assert c.blur_sigma == 1.5


# ── WorkflowDatasetEntry ─────────────────────────────────────


def test_entry_h5_existing_ok():
    _valid_entry()


def test_entry_tiff_pending_requires_compress_plan():
    with pytest.raises(ValueError, match="compress_plan"):
        WorkflowDatasetEntry(
            name="DS1",
            source=DatasetSource.TIFF_PENDING,
            h5_path=Path("/tmp/DS1.h5"),
            channel_names=[],
        )


def test_entry_tiff_pending_with_compress_plan():
    WorkflowDatasetEntry(
        name="DS1",
        source=DatasetSource.TIFF_PENDING,
        h5_path=Path("/tmp/DS1.h5"),
        channel_names=["GFP"],
        compress_plan={"source_dir": "/tmp/tiffs"},
    )


def test_entry_rejects_empty_name():
    with pytest.raises(ValueError, match="dataset name"):
        _valid_entry(name="")


# ── WorkflowConfig ───────────────────────────────────────────


def test_config_requires_datasets():
    with pytest.raises(ValueError, match="at least one dataset"):
        WorkflowConfig(
            datasets=[],
            cellpose=CellposeSettings(),
            thresholding_rounds=[_valid_round()],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
        )


def test_config_requires_rounds():
    with pytest.raises(ValueError, match="at least one thresholding round"):
        WorkflowConfig(
            datasets=[_valid_entry()],
            cellpose=CellposeSettings(),
            thresholding_rounds=[],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
        )


def test_config_rejects_duplicate_round_names():
    with pytest.raises(ValueError, match="unique"):
        WorkflowConfig(
            datasets=[_valid_entry()],
            cellpose=CellposeSettings(),
            thresholding_rounds=[
                _valid_round(name="R1"),
                _valid_round(name="R1"),
            ],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
        )


def test_config_rejects_duplicate_dataset_names():
    with pytest.raises(ValueError, match="dataset names must be unique"):
        WorkflowConfig(
            datasets=[_valid_entry(name="DS1"), _valid_entry(name="DS1")],
            cellpose=CellposeSettings(),
            thresholding_rounds=[_valid_round()],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
        )


def test_config_happy_path():
    cfg = WorkflowConfig(
        datasets=[_valid_entry(name="DS1"), _valid_entry(name="DS2")],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round(name="R1"), _valid_round(name="R2")],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=Path("/tmp/runs"),
    )
    assert len(cfg.datasets) == 2
    assert len(cfg.thresholding_rounds) == 2
    assert cfg.cellpose.gpu is True


def test_config_is_frozen():
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round()],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
    )
    with pytest.raises((AttributeError, TypeError)):
        cfg.selected_csv_columns = ["mutated"]  # type: ignore[misc]


# ── StrEnum serialization sanity ─────────────────────────────


def test_strenum_round_trip_through_value():
    assert ThresholdAlgorithm("gmm") is ThresholdAlgorithm.GMM
    assert GmmCriterion("bic") is GmmCriterion.BIC
    assert DatasetSource("h5_existing") is DatasetSource.H5_EXISTING
    # StrEnum values serialize as plain strings
    assert str(ThresholdAlgorithm.GMM) == "gmm" or ThresholdAlgorithm.GMM.value == "gmm"


# ── EdgeMode ─────────────────────────────────────────────────


def test_edge_mode_has_three_values():
    assert EdgeMode("exclude") is EdgeMode.EXCLUDE
    assert EdgeMode("include_as_normal") is EdgeMode.INCLUDE_AS_NORMAL
    assert (
        EdgeMode("include_as_size_normalized_cohort")
        is EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT
    )


def test_edge_mode_rejects_unknown_string():
    with pytest.raises(ValueError):
        EdgeMode("excludeed")


# ── DiluteSettings ───────────────────────────────────────────


def _valid_dilute(**overrides) -> DiluteSettings:
    defaults = {
        "mask_name": "dilute",
        "dilation_radius_px": 3,
        "channel": "GFP",
        "metric": "mean_intensity",
        "algorithm": ThresholdAlgorithm.GMM,
    }
    defaults.update(overrides)
    return DiluteSettings(**defaults)


def test_dilute_settings_happy_path():
    d = _valid_dilute()
    assert d.mask_name == "dilute"
    assert d.dilation_radius_px == 3
    assert d.channel == "GFP"
    assert d.metric == "mean_intensity"
    assert d.algorithm is ThresholdAlgorithm.GMM
    # Defaults
    assert d.gmm_criterion is GmmCriterion.BIC
    assert d.gmm_max_components == 4
    assert d.kmeans_n_clusters == 3
    assert d.gaussian_sigma == 1.0


def test_dilute_settings_is_frozen():
    d = _valid_dilute()
    with pytest.raises((AttributeError, TypeError)):
        d.mask_name = "mutated"  # type: ignore[misc]


def test_dilute_settings_rejects_bad_mask_name():
    for bad in ["", "1starts_with_digit", "has space", "dot.in.name"]:
        with pytest.raises(ValueError, match="mask_name"):
            _valid_dilute(mask_name=bad)


def test_dilute_settings_rejects_empty_channel():
    with pytest.raises(ValueError, match="channel must be non-empty"):
        _valid_dilute(channel="")


def test_dilute_settings_rejects_unknown_metric():
    with pytest.raises(ValueError, match="metric must be one of"):
        _valid_dilute(metric="nonsense")


def test_dilute_settings_rejects_non_positive_radius():
    for bad in [0, -1, -5]:
        with pytest.raises(ValueError, match="dilution_radius_px"):
            _valid_dilute(dilation_radius_px=bad)


def test_dilute_settings_rejects_bad_algorithm_params():
    with pytest.raises(ValueError, match="gmm_max_components"):
        _valid_dilute(gmm_max_components=1)
    with pytest.raises(ValueError, match="kmeans_n_clusters"):
        _valid_dilute(kmeans_n_clusters=1)
    with pytest.raises(ValueError, match="gaussian_sigma"):
        _valid_dilute(gaussian_sigma=-0.1)


# ── WorkflowConfig with edge_mode / dilute_settings ──────────


def test_config_defaults_edge_mode_to_exclude():
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round()],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
    )
    assert cfg.edge_mode is EdgeMode.EXCLUDE


def test_config_defaults_dilute_settings_to_none():
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round()],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
    )
    assert cfg.dilute_settings is None


def test_config_accepts_explicit_edge_mode_and_dilute_settings():
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round(name="ch0_bright")],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
        dilute_settings=_valid_dilute(),
    )
    assert cfg.edge_mode is EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT
    assert cfg.dilute_settings is not None
    assert cfg.dilute_settings.mask_name == "dilute"


def test_config_rejects_dilute_name_collision_with_round_name():
    """Origin AE4: dilute mask_name conflicts with thresholding round name."""
    with pytest.raises(ValueError, match="dilute mask_name.*conflicts"):
        WorkflowConfig(
            datasets=[_valid_entry()],
            cellpose=CellposeSettings(),
            thresholding_rounds=[
                _valid_round(name="puncta_bright"),
                _valid_round(name="puncta_dim"),
            ],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
            dilute_settings=_valid_dilute(mask_name="puncta_bright"),
        )


def test_config_dilute_none_skips_collision_check():
    """When dilute_settings is None, no collision check runs."""
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round(name="dilute")],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
        dilute_settings=None,
    )
    assert cfg.thresholding_rounds[0].name == "dilute"


# ── RunMetadata.per_dataset_dilute_round_counts ──────────────


def test_run_metadata_defaults_dilute_round_counts_to_empty_dict():
    from datetime import UTC, datetime

    meta = RunMetadata(
        run_id="run_test",
        run_folder=Path("/tmp/run"),
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
    )
    assert meta.per_dataset_dilute_round_counts == {}


def test_run_metadata_accepts_dilute_round_counts():
    from datetime import UTC, datetime

    meta = RunMetadata(
        run_id="run_test",
        run_folder=Path("/tmp/run"),
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
        per_dataset_dilute_round_counts={"DS1": 2, "DS2": 4},
    )
    assert meta.per_dataset_dilute_round_counts == {"DS1": 2, "DS2": 4}


def test_run_metadata_is_mutable_for_dilute_round_counts():
    """The dict is mutable so the runner can append per-dataset counts as it goes."""
    from datetime import UTC, datetime

    meta = RunMetadata(
        run_id="run_test",
        run_folder=Path("/tmp/run"),
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
    )
    meta.per_dataset_dilute_round_counts["DS1"] = 3
    assert meta.per_dataset_dilute_round_counts == {"DS1": 3}


def test_run_seg_qc_on_existing_defaults_true():
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round()],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
    )
    assert cfg.run_seg_qc_on_existing is True


def test_run_seg_qc_on_existing_explicit_false():
    cfg = WorkflowConfig(
        datasets=[_valid_entry()],
        cellpose=CellposeSettings(),
        thresholding_rounds=[_valid_round()],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
        run_seg_qc_on_existing=False,
    )
    assert cfg.run_seg_qc_on_existing is False
