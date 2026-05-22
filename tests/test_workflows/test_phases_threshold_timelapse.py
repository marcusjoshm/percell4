"""Per-timepoint thresholding: compute + headless apply (U4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from percell4.store import DatasetStore
from percell4.workflows.models import ThresholdAlgorithm, ThresholdingRound
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
