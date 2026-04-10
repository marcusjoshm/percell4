"""Tests for workflow config dataclasses and their validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    GmmCriterion,
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
    from percell4.measure.metrics import BUILTIN_METRICS

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


def test_cellpose_rejects_bad_values():
    with pytest.raises(ValueError, match="diameter"):
        CellposeSettings(diameter=-1)
    with pytest.raises(ValueError, match="min_size"):
        CellposeSettings(min_size=-1)
    with pytest.raises(ValueError, match="batch_size"):
        CellposeSettings(batch_size=0)
    with pytest.raises(ValueError, match="channel_idx"):
        CellposeSettings(channel_idx=-1)


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
