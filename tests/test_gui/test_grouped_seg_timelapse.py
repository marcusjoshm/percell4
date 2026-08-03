"""Per-timepoint Grouped Thresholding on a time-lapse dataset (U9 follow-up).

The interactive panel drives one QC controller per timepoint (the
TimelapseThresholdQCQueueEntry pattern) and stacks the accepted per-frame masks
into a (T,H,W) mask. These tests cover the data path the panel builds (round
spec + per-timepoint grouping) and the panel wiring; the per-frame QC stacking
itself is covered by tests/test_gui_workflows for the queue entry.
"""

from __future__ import annotations

import numpy as np

from percell4.store import DatasetStore
from percell4.workflows.models import (
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import threshold_compute_one


def _timelapse_store(path, n_t=2):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    intensity = np.zeros((n_t, 16, 16), dtype=np.float32)
    for t in range(n_t):
        intensity[t, 2:7, 2:7] = 100 + t * 10
        intensity[t, 9:14, 9:14] = 200 + t * 10
    store.write_array("intensity", intensity, attrs={"dims": ["T", "H", "W"]})
    labels = np.zeros((n_t, 16, 16), dtype=np.int32)
    labels[:, 2:7, 2:7] = 1
    labels[:, 9:14, 9:14] = 2
    store.write_labels("cp", labels)
    return store


def test_threshold_compute_one_groups_each_timepoint(tmp_path):
    """The data path the panel builds: a round spec + threshold_compute_one on a
    time-lapse dataset yields a per-timepoint grouping dict (one per frame)."""
    store = _timelapse_store(tmp_path / "movie.h5", n_t=3)
    round_spec = ThresholdingRound(
        name="grouped",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gmm_max_components=2,
        gaussian_sigma=1.0,
    )

    grouping, failure, _msg = threshold_compute_one(store, round_spec, "cp")

    assert failure is None
    assert isinstance(grouping, dict)
    assert sorted(grouping.keys()) == [0, 1, 2]  # one grouping per timepoint


def test_round_name_mirrors_mask_name():
    """The mask name becomes the round name (validated); a typical name works."""
    rs = ThresholdingRound(
        name="grouped",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.GMM,
        gmm_max_components=4,
        kmeans_n_clusters=3,
    )
    assert rs.name == "grouped"


def test_panel_accepts_repopulate_viewer_kwarg():
    """The panel exposes the repopulate_viewer hook used to restore the viewer
    after the per-timepoint QC clears it between frames."""
    import inspect

    from percell4.gui.grouped_seg_panel import GroupedSegPanel

    params = inspect.signature(GroupedSegPanel.__init__).parameters
    assert "repopulate_viewer" in params
