"""Per-timepoint thresholding: compute + headless apply (U4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.models import (
    AdaptiveClipSettings,
    AutoExtractSettings,
    CnrClassifySettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import apply_threshold_headless, threshold_compute_one


def _round() -> ThresholdingRound:
    return ThresholdingRound(
        name="GFP_split", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )


def _frame():
    """100x100 plane with 12 cells split into a dim and a bright cluster.

    K-means needs >= 10 cells to split into >1 group, so use 12 (4x3 grid).
    """
    img = np.zeros((100, 100), dtype=np.float32)
    lab = np.zeros((100, 100), dtype=np.int32)
    for i in range(12):
        r = 5 + (i // 3) * 22
        c = 5 + (i % 3) * 22
        val = (10.0 + i) if i < 6 else (100.0 + i)  # 6 dim, 6 bright
        img[r : r + 6, c : c + 6] = val
        lab[r : r + 6, c : c + 6] = i + 1
    return img, lab


def _timelapse_store(path: Path, n_t=2) -> DatasetStore:
    imgs, labs = zip(*[_frame() for _ in range(n_t)])
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.stack(imgs, 0), attrs={"dims": ["T", "H", "W"]})
    store.write_labels("cellpose_qc", np.stack(labs, 0).astype(np.int32))
    return store


def test_threshold_compute_timelapse_returns_per_frame_dict(tmp_path):
    store = _timelapse_store(tmp_path / "tl.h5", n_t=2)
    grouping, failure, _msg = threshold_compute_one(store, _round())

    assert failure is None
    assert isinstance(grouping, dict)
    assert set(grouping) == {0, 1}
    assert all(g.n_groups == 2 for g in grouping.values())


def test_apply_threshold_timelapse_writes_THW_mask_and_timepoint_groups(tmp_path):
    store = _timelapse_store(tmp_path / "tl.h5", n_t=2)
    grouping, _f, _m = threshold_compute_one(store, _round())

    failure, _msg = apply_threshold_headless(store, _round(), grouping)

    assert failure is None
    mask = store.read_mask("GFP_split")
    assert mask.shape == (2, 100, 100)
    assert int(mask.sum()) > 0
    groups = store.read_dataframe("/groups/GFP_split")
    assert "timepoint" in groups.columns
    assert set(groups["timepoint"].unique()) == {0, 1}


def _adaptive_frame():
    """One large cell with structured background (per-cell MAD > 0) + a blob."""
    img = np.zeros((100, 100), dtype=np.float32)
    rows = np.arange(100).reshape(-1, 1)
    img[20:60, 20:60] = 10 + (rows[20:60] % 3)
    img[35:45, 35:45] = 200.0
    lab = np.zeros((100, 100), dtype=np.int32)
    lab[20:60, 20:60] = 1
    return img, lab


def _adaptive_timelapse_store(path: Path, n_t=2, pixel_size_um=0.12) -> DatasetStore:
    imgs, labs = zip(*[_adaptive_frame() for _ in range(n_t)])
    store = DatasetStore(path)
    meta = {"channel_names": ["GFP"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)
    store.write_array("intensity", np.stack(imgs, 0), attrs={"dims": ["T", "H", "W"]})
    store.write_labels("cellpose_qc", np.stack(labs, 0).astype(np.int32))
    return store


def _adaptive_round():
    return ThresholdingRound(
        name="ac", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.12),
    )


def test_apply_adaptive_clip_timelapse_writes_THW_mask(tmp_path):
    store = _adaptive_timelapse_store(tmp_path / "ac_tl.h5", n_t=2)
    grouping, failure, _ = threshold_compute_one(store, _adaptive_round())
    assert failure is None
    assert isinstance(grouping, dict)  # per-frame trivial groupings

    failure, msg = apply_threshold_headless(store, _adaptive_round(), grouping)
    assert failure is None, msg
    mask = store.read_mask("ac")
    assert mask.shape == (2, 100, 100)
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert int(mask.sum()) > 0  # blob detected in each frame
    groups = store.read_dataframe("/groups/ac")
    assert "timepoint" in groups.columns
    # Degenerate single group per frame.
    assert set(groups["group_GFP_mean_intensity"].unique()) == {1}


def test_apply_adaptive_clip_timelapse_missing_pixel_size_fails(tmp_path):
    store = _adaptive_timelapse_store(tmp_path / "ac_tl_nops.h5", n_t=2, pixel_size_um=None)
    grouping, _, _ = threshold_compute_one(store, _adaptive_round())
    failure, msg = apply_threshold_headless(store, _adaptive_round(), grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "pixel size" in msg
    assert "ac" not in store.list_masks()


def test_single_timepoint_threshold_unchanged(tmp_path):
    # (H, W) single timepoint -> GroupingResult (not dict), 2D mask.
    img, lab = _frame()
    store = DatasetStore(tmp_path / "still.h5")
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", img, attrs={"dims": ["H", "W"]})
    store.write_labels("cellpose_qc", lab)

    grouping, failure, _m = threshold_compute_one(store, _round())
    assert failure is None
    assert not isinstance(grouping, dict)  # GroupingResult, as before

    failure, _msg = apply_threshold_headless(store, _round(), grouping)
    assert failure is None
    assert store.read_mask("GFP_split").ndim == 2
    groups = store.read_dataframe("/groups/GFP_split")
    assert "timepoint" not in groups.columns


# --------------------------------------------------------------------------- #
# U3: per-frame guided CNR on a time-lapse (R5/R6) — single-tp abort lifted.
# The per-frame split LOGIC is covered in tests/test_measure/test_cnr_classification.py;
# here we test the phases wiring (THW stacks / timepoint table / single-population)
# deterministically by patching the domain classify_by_cnr.
# --------------------------------------------------------------------------- #
def _cnr_round() -> ThresholdingRound:
    return ThresholdingRound(
        name="ac", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.12),
        cnr_classify=CnrClassifySettings(threshold=5.0),
    )


def _patch_classify(monkeypatch, result):
    import percell4.domain.measure.cnr_classification as cnr_mod

    def _fake(image, feature_mask, cell_labels, *, threshold=None,
              n_populations="auto", presmooth_sigma_px=1.0):
        return result

    monkeypatch.setattr(cnr_mod, "classify_by_cnr", _fake)


def _two_pop_result():
    from percell4.domain.measure.cnr_classification import ClassificationResult

    labels_image = np.zeros((100, 100), dtype=np.int32)
    labels_image[36:40, 36:40] = 1  # low-CNR population
    labels_image[44:48, 44:48] = 2  # high-CNR population
    components = [
        {"label": 1, "cell": 1, "cnr": 3.0, "subpopulation": 1},
        {"label": 2, "cell": 1, "cnr": 8.0, "subpopulation": 2},
    ]
    return ClassificationResult(
        n_subpopulations=2, labels_image=labels_image, components=components,
        split_axis="cnr", threshold=5.0, report={"decision": "guided"},
    )


def _one_pop_result():
    from percell4.domain.measure.cnr_classification import ClassificationResult

    labels_image = np.zeros((100, 100), dtype=np.int32)
    labels_image[36:40, 36:40] = 1
    components = [{"label": 1, "cell": 1, "cnr": 3.0, "subpopulation": 1}]
    return ClassificationResult(
        n_subpopulations=1, labels_image=labels_image, components=components,
        split_axis=None, threshold=None, report={"decision": "single population"},
    )


def test_cnr_timelapse_writes_THW_population_masks_and_timepoint_table(tmp_path, monkeypatch):
    """R5/R6: a time-lapse CNR round now RUNS (no single-timepoint abort), writing
    (T,H,W) _low/_high stacks and a timepoint-columned /classification table."""
    store = _adaptive_timelapse_store(tmp_path / "cnr_tl.h5", n_t=2)
    _patch_classify(monkeypatch, _two_pop_result())
    rnd = _cnr_round()
    grouping, _f, _m = threshold_compute_one(store, rnd)
    failure, msg = apply_threshold_headless(store, rnd, grouping)
    assert failure is None, msg

    low, high = store.read_mask("ac_low"), store.read_mask("ac_high")
    assert low.shape == (2, 100, 100) and high.shape == (2, 100, 100)
    assert int(low.sum()) > 0 and int(high.sum()) > 0
    table = store.read_dataframe("/classification/ac")
    assert "timepoint" in table.columns
    assert set(table["timepoint"].unique()) == {0, 1}
    assert "n_subpopulations" in table.columns


def test_cnr_timelapse_single_population_writes_no_split(tmp_path, monkeypatch):
    """A stack that never splits writes no _low/_high; the base (T,H,W) mask stands."""
    store = _adaptive_timelapse_store(tmp_path / "cnr_tl_1pop.h5", n_t=2)
    _patch_classify(monkeypatch, _one_pop_result())
    rnd = _cnr_round()
    grouping, _f, _m = threshold_compute_one(store, rnd)
    failure, msg = apply_threshold_headless(store, rnd, grouping)
    assert failure is None, msg
    assert "ac_low" not in store.list_masks()
    assert "ac_high" not in store.list_masks()
    assert store.read_mask("ac").shape == (2, 100, 100)  # base mask stands
    assert "single population" in msg


def _blank_frame():
    """A labelled cell with a flat interior — no LoG blob to size (auto-detect raises)."""
    img = np.zeros((100, 100), dtype=np.float32)
    img[20:60, 20:60] = 10.0  # uniform interior, no particle
    lab = np.zeros((100, 100), dtype=np.int32)
    lab[20:60, 20:60] = 1
    return img, lab


def test_auto_extract_timelapse_blank_frame_degrades_not_aborts(tmp_path):
    """R9: an auto-detect frame with no detectable particles becomes an empty plane,
    not a whole-dataset abort (the dissolved end of a washout)."""
    img0, lab0 = _adaptive_frame()   # frame 0 has a bright blob
    img1, lab1 = _blank_frame()      # frame 1 has none
    store = DatasetStore(tmp_path / "ae_blank.h5")
    store.create(metadata={"channel_names": ["GFP"]})  # no pixel size: auto-detect needs none
    store.write_array("intensity", np.stack([img0, img1], 0), attrs={"dims": ["T", "H", "W"]})
    store.write_labels("cellpose_qc", np.stack([lab0, lab1], 0).astype(np.int32))

    rnd = ThresholdingRound(
        name="ae", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
        auto_extract=AutoExtractSettings(),  # smallest_particle_um=None -> auto-detect
    )
    grouping, _f, _m = threshold_compute_one(store, rnd)
    failure, msg = apply_threshold_headless(store, rnd, grouping)
    assert failure is None, msg  # the blank frame did NOT abort the dataset
    mask = store.read_mask("ae")
    assert mask.shape == (2, 100, 100)
    assert int(mask[0].sum()) > 0   # frame 0 detected its blob
    assert int(mask[1].sum()) == 0  # frame 1 is an empty plane


def test_auto_extract_timelapse_genuine_error_aborts(tmp_path, monkeypatch):
    """R9 only degrades the recoverable 'no particles' case: a GENUINE per-frame
    auto_extract failure aborts the whole dataset (it is not turned into an empty frame)."""
    import percell4.domain.measure.auto_extraction as ae_mod

    store = _adaptive_timelapse_store(tmp_path / "ae_err.h5", n_t=2)
    rnd = ThresholdingRound(
        name="ae", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
        auto_extract=AutoExtractSettings(),
    )

    def boom(*a, **k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(ae_mod, "auto_extract", boom)
    grouping, _f, _m = threshold_compute_one(store, rnd)
    failure, _msg = apply_threshold_headless(store, rnd, grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "ae" not in store.list_masks()  # genuine error -> nothing written


def test_cnr_timelapse_stale_population_masks_deleted_on_reclassify(tmp_path, monkeypatch):
    """A time-lapse 2->1 population re-run leaves no stale (T,H,W) _low/_high masks
    (the delete-before-write cleanup fires for the stacks)."""
    store = _adaptive_timelapse_store(tmp_path / "cnr_tl_stale.h5", n_t=2)
    rnd = _cnr_round()

    _patch_classify(monkeypatch, _two_pop_result())
    grouping, _f, _m = threshold_compute_one(store, rnd)
    apply_threshold_headless(store, rnd, grouping)
    assert "ac_low" in store.list_masks() and "ac_high" in store.list_masks()
    assert store.read_mask("ac_low").shape == (2, 100, 100)

    _patch_classify(monkeypatch, _one_pop_result())
    grouping, _f, _m = threshold_compute_one(store, rnd)
    failure, _msg = apply_threshold_headless(store, rnd, grouping)
    assert failure is None
    assert "ac_low" not in store.list_masks()  # stale (T,H,W) populations cleared
    assert "ac_high" not in store.list_masks()
