"""Tests for workflow config dataclasses and their validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.workflows.models import (
    AdaptiveClipSettings,
    CellposeSettings,
    DatasetSource,
    DiluteSettings,
    EdgeMode,
    GmmCriterion,
    IterativeOtsuSettings,
    PunctaDetectorSettings,
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


# ── PunctaDetectorSettings (U1) ──────────────────────────────


def test_round_defaults_puncta_to_none():
    assert _valid_round().puncta is None


def test_puncta_settings_defaults_are_valid():
    p = PunctaDetectorSettings()
    assert p.detector_name == "otsu"
    assert p.background_estimator_name == "gaussian-peak"
    assert p.detector_params == ()
    assert p.spot_scale_prior is None


def test_puncta_settings_rejects_unknown_names():
    with pytest.raises(ValueError, match="detector_name"):
        PunctaDetectorSettings(detector_name="nonsense")
    with pytest.raises(ValueError, match="seed_detector_name"):
        PunctaDetectorSettings(seed_detector_name="nonsense")
    with pytest.raises(ValueError, match="background_estimator_name"):
        PunctaDetectorSettings(background_estimator_name="nonsense")


def test_puncta_settings_rejects_bad_spot_px():
    with pytest.raises(ValueError, match="min_spot_px"):
        PunctaDetectorSettings(min_spot_px=0)
    with pytest.raises(ValueError, match="max_spot_px"):
        PunctaDetectorSettings(min_spot_px=5, max_spot_px=3)


def test_puncta_settings_rejects_bad_scale_prior():
    with pytest.raises(ValueError, match="spot_scale_prior"):
        PunctaDetectorSettings(spot_scale_prior=(4.0, 1.0))  # lo > hi
    with pytest.raises(ValueError, match="spot_scale_prior"):
        PunctaDetectorSettings(spot_scale_prior=(0.0, 4.0))  # lo not > 0


def test_puncta_params_canonicalize_to_sorted_tuple():
    # Dict input (any order) normalizes to a sorted tuple of pairs so the
    # frozen dataclass is hashable and round-trips order-independently.
    p = PunctaDetectorSettings(
        detector_name="log", detector_params={"threshold_rel": 0.1, "k": 2.5}
    )
    assert p.detector_params == (("k", 2.5), ("threshold_rel", 0.1))
    assert dict(p.detector_params) == {"threshold_rel": 0.1, "k": 2.5}


def test_puncta_params_reject_non_scalar_values():
    with pytest.raises(ValueError, match="JSON scalar"):
        PunctaDetectorSettings(detector_params={"bad": [1, 2, 3]})


def test_puncta_scale_prior_coerces_list_to_tuple():
    # A JSON-loaded list must become a float tuple so __eq__/__hash__ are
    # stable across a run_config.json round-trip.
    p = PunctaDetectorSettings(spot_scale_prior=[1.0, 4.0])  # type: ignore[arg-type]
    assert p.spot_scale_prior == (1.0, 4.0)
    assert isinstance(p.spot_scale_prior, tuple)


def test_round_with_puncta_is_hashable():
    p = PunctaDetectorSettings(
        detector_name="log", detector_params={"k": 2.5}, spot_scale_prior=(1.0, 4.0)
    )
    r = _valid_round(puncta=p)
    # Must not raise — _grouping_cache and any future set/dict use depend on it.
    hash(r)
    hash(p)


def test_puncta_names_validation_is_skimage_free():
    # Constructing a round (legacy or puncta) must not import scikit-image:
    # validation pulls names from the dependency-light puncta_names module.
    import sys

    for mod in [m for m in sys.modules if m.startswith("skimage")]:
        del sys.modules[mod]
    PunctaDetectorSettings(detector_name="log")
    _valid_round()
    assert not any(m.startswith("skimage") for m in sys.modules)


# ── IterativeOtsuSettings (U2) ───────────────────────────────


def test_round_defaults_iterative_otsu_to_none():
    assert _valid_round().iterative_otsu is None


def test_iterative_otsu_defaults_are_valid():
    s = IterativeOtsuSettings()
    assert s.scope == "per-cell"
    assert s.dilation_radius_px == 5
    assert s.max_rounds == 10
    assert s.stop_criteria == ("bg-floor", "positive-fraction-high")
    assert s.stop_combine == "any"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": "nonsense"},
        {"dilation_radius_px": 0},
        {"dilation_radius_px": -3},
        {"max_rounds": 0},
        {"stop_criteria": ()},
        {"stop_criteria": ("not-a-criterion",)},
        {"stop_combine": "maybe"},
        {"stop_params": (("bg-floor.k", [1, 2, 3]),)},  # non-scalar value
        {"stop_params": (("nope.k", 2.0),)},  # unknown criterion prefix
        {"stop_params": (("undotted", 2.0),)},  # missing dotted prefix
        {"fixed_iterations": 0},  # must be >= 1 when set
        {"fixed_iterations": -2},
    ],
)
def test_iterative_otsu_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        IterativeOtsuSettings(**kwargs)


def test_iterative_otsu_defaults_fixed_iterations_none():
    assert IterativeOtsuSettings().fixed_iterations is None


def test_iterative_otsu_fixed_iterations_allows_empty_criteria():
    # Fixed-count mode blocks the criteria, so an empty tuple is valid there.
    s = IterativeOtsuSettings(stop_criteria=(), fixed_iterations=3)
    assert s.fixed_iterations == 3
    assert s.stop_criteria == ()


def test_iterative_otsu_fixed_iterations_roundtrip():
    from percell4.workflows.artifacts import _round_from_dict, _round_to_dict

    r = _valid_round(iterative_otsu=IterativeOtsuSettings(stop_criteria=(), fixed_iterations=4))
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r
    assert restored.iterative_otsu.fixed_iterations == 4


def test_iterative_otsu_params_namespaced_and_canonical():
    from percell4.domain.measure.iterative_otsu import params_by_name

    s = IterativeOtsuSettings(
        stop_criteria=("bg-floor", "peak-prominence"),
        stop_params=(("peak-prominence.k", 3.0), ("bg-floor.k", 2.5)),
    )
    # Canonical sorted tuple (hashable, stable round-trip).
    assert s.stop_params == (("bg-floor.k", 2.5), ("peak-prominence.k", 3.0))
    grouped = params_by_name(s.stop_params)
    assert grouped["bg-floor"] == {"k": 2.5}
    assert grouped["peak-prominence"] == {"k": 3.0}


def test_round_rejects_both_puncta_and_iterative_otsu():
    with pytest.raises(ValueError, match="at most one"):
        _valid_round(puncta=PunctaDetectorSettings(detector_name="log"),
                     iterative_otsu=IterativeOtsuSettings())


# ── AdaptiveClipSettings (U1) ─────────────────────────────────


def test_round_defaults_adaptive_clip_to_none():
    assert _valid_round().adaptive_clip is None


def test_adaptive_clip_defaults_are_valid():
    s = AdaptiveClipSettings(d_min_um=0.40)
    assert s.k == 1.0
    assert s.d_min_um == 0.40
    # Presmooth defaults to the validated 1 px (NOT 0 / the grouped sigma default).
    assert s.presmooth_sigma_px == 1.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"d_min_um": 0.0},
        {"d_min_um": -0.1},
        {"d_min_um": 0.40, "k": -1.0},
        {"d_min_um": 0.40, "presmooth_sigma_px": -0.5},
    ],
)
def test_adaptive_clip_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        AdaptiveClipSettings(**kwargs)


def test_round_accepts_adaptive_clip():
    r = _valid_round(adaptive_clip=AdaptiveClipSettings(d_min_um=0.40))
    assert r.adaptive_clip.d_min_um == 0.40
    assert r.puncta is None
    assert r.iterative_otsu is None


def test_round_with_adaptive_clip_is_hashable():
    r = _valid_round(adaptive_clip=AdaptiveClipSettings(d_min_um=0.40, k=1.5))
    hash(r)
    hash(r.adaptive_clip)


@pytest.mark.parametrize(
    "other",
    [
        {"puncta": PunctaDetectorSettings(detector_name="log")},
        {"iterative_otsu": IterativeOtsuSettings()},
    ],
)
def test_round_rejects_adaptive_clip_with_another_method(other):
    with pytest.raises(ValueError, match="at most one"):
        _valid_round(adaptive_clip=AdaptiveClipSettings(d_min_um=0.40), **other)


def test_adaptive_clip_validation_is_skimage_free():
    import sys

    for mod in [m for m in sys.modules if m.startswith("skimage")]:
        del sys.modules[mod]
    AdaptiveClipSettings(d_min_um=0.40)
    assert not any(m.startswith("skimage") for m in sys.modules)


def test_round_with_iterative_otsu_is_hashable():
    r = _valid_round(iterative_otsu=IterativeOtsuSettings(stop_params=(("bg-floor.k", 2.0),)))
    hash(r)
    hash(r.iterative_otsu)


def test_iterative_otsu_validation_is_skimage_free():
    import sys

    for mod in [m for m in sys.modules if m.startswith("skimage")]:
        del sys.modules[mod]
    IterativeOtsuSettings(scope="whole-field")
    _valid_round(iterative_otsu=IterativeOtsuSettings())
    assert not any(m.startswith("skimage") for m in sys.modules)


def test_iterative_otsu_round_config_roundtrip():
    from percell4.workflows.artifacts import _round_from_dict, _round_to_dict

    r = _valid_round(
        iterative_otsu=IterativeOtsuSettings(
            scope="groups",
            dilation_radius_px=4,
            max_rounds=8,
            stop_criteria=("bg-floor", "separability"),
            stop_params=(("bg-floor.k", 2.5), ("separability.min_eta", 0.8)),
            stop_combine="all",
        )
    )
    restored = _round_from_dict(_round_to_dict(r))
    assert restored == r


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


def test_config_allows_empty_rounds_with_existing_masks():
    cfg = WorkflowConfig(
        datasets=[_valid_entry(name="DS1")],
        cellpose=CellposeSettings(),
        thresholding_rounds=[],
        selected_csv_columns=[],
        output_parent=Path("/tmp/runs"),
        use_existing_masks=True,
        existing_mask_selections={"DS1": ["P-body_mask"]},
    )
    assert cfg.use_existing_masks is True
    assert cfg.existing_mask_selections == {"DS1": ["P-body_mask"]}
    assert cfg.thresholding_rounds == []


def test_config_existing_masks_without_selection_still_requires_rounds():
    with pytest.raises(ValueError, match="at least one thresholding round"):
        WorkflowConfig(
            datasets=[_valid_entry(name="DS1")],
            cellpose=CellposeSettings(),
            thresholding_rounds=[],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
            use_existing_masks=True,
            existing_mask_selections={},
        )


def test_config_empty_selection_value_rejected():
    # DS1 has a real selection (so the empty-rounds guard passes), but DS2
    # is explicitly keyed with an empty list — that is rejected.
    with pytest.raises(ValueError, match="empty selection"):
        WorkflowConfig(
            datasets=[_valid_entry(name="DS1"), _valid_entry(name="DS2")],
            cellpose=CellposeSettings(),
            thresholding_rounds=[],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
            use_existing_masks=True,
            existing_mask_selections={"DS1": ["m"], "DS2": []},
        )


def test_config_mask_selection_unknown_dataset_rejected():
    with pytest.raises(ValueError, match="unknown dataset"):
        WorkflowConfig(
            datasets=[_valid_entry(name="DS1")],
            cellpose=CellposeSettings(),
            thresholding_rounds=[_valid_round()],
            selected_csv_columns=[],
            output_parent=Path("/tmp/runs"),
            existing_mask_selections={"DS_NOPE": ["m"]},
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
        EdgeMode("include_as_size_normalized_cohort") is EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT
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
