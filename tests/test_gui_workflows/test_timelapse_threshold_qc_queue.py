"""TimelapseThresholdQCQueueEntry: per-timepoint interactive threshold QC.

The wrapper runs the single-frame ThresholdQCController once per timepoint
(persist_round_outputs=False), accumulates each frame's accepted 2D mask, and
after the final timepoint writes a (T,H,W) /masks/<round> resource plus a
/groups/<round> table carrying a timepoint column — the same shape the
headless per-frame path produces.

The real controller opens a Qt modal; here we monkeypatch it with a fake that
immediately completes each frame, so the test exercises the wrapper's
sequencing + final persistence without any user interaction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult
from percell4.store import DatasetStore
from percell4.workflows.models import (
    DatasetSource,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowDatasetEntry,
)

N_TP = 3
H = W = 12


def _store(path, *, labels="cellpose_tracked"):
    s = DatasetStore(path)
    s.create(metadata={"channel_names": ["GFP"], "n_timepoints": N_TP})
    s.write_array(
        "intensity",
        np.ones((N_TP, H, W), dtype=np.float32) * 10.0,
        attrs={"dims": ["T", "H", "W"]},
    )
    cell = np.zeros((N_TP, H, W), dtype=np.int32)
    cell[:, 2:5, 2:5] = 1  # one cell, present in every frame
    s.write_labels(labels, cell)
    return s


def _grouping():
    return GroupingResult(
        group_assignments=pd.Series(
            data=np.array([1], dtype=int),
            index=pd.Index([1], name="label"),
            name="group",
        ),
        n_groups=1,
        group_means=[10.0],
    )


@pytest.fixture
def fake_viewer():
    viewer = MagicMock()
    viewer.layers = MagicMock()
    win = MagicMock()
    win.viewer = viewer
    win.existing_viewer = viewer
    return win


class _FakeController:
    """Stand-in for ThresholdQCController: completes immediately on start().

    Records each (channel_image, seg_labels) it was constructed with, and on
    start() fires on_complete(True, msg, mask) where mask = (seg_labels > 0).
    """

    instances: list[_FakeController] = []

    def __init__(self, *, seg_labels, channel_image, on_complete, **kwargs):
        self.seg_labels = np.asarray(seg_labels)
        self.channel_image = np.asarray(channel_image)
        self._on_complete = on_complete
        self.kwargs = kwargs
        _FakeController.instances.append(self)

    def start(self):
        mask = (self.seg_labels > 0).astype(np.uint8)
        self._on_complete(True, "frame accepted", mask)


def _round():
    return ThresholdingRound(
        name="GFP_split", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )


def _make_entry(path):
    return WorkflowDatasetEntry(
        name="TL", source=DatasetSource.H5_EXISTING, h5_path=path,
        channel_names=["GFP"],
    )


def test_per_timepoint_qc_writes_thw_mask_and_grouped_df(
    tmp_path, fake_viewer, monkeypatch
):
    from percell4.gui.workflows.single_cell import threshold_qc_queue as mod

    _FakeController.instances = []
    monkeypatch.setattr(mod, "ThresholdQCController", _FakeController)

    p = tmp_path / "TL.h5"
    _store(p)
    grouping_by_t = {t: _grouping() for t in range(N_TP)}

    results = []
    entry = mod.TimelapseThresholdQCQueueEntry(
        viewer_win=fake_viewer,
        data_model=MagicMock(),
        entry=_make_entry(p),
        round_spec=_round(),
        grouping_by_timepoint=grouping_by_t,
        queue_index=0,
        queue_total=1,
        on_complete=results.append,
        seg_name="cellpose_tracked",
    )
    entry.start()

    # One controller per timepoint (each frame QC'd interactively).
    assert len(_FakeController.instances) == N_TP

    # The phase succeeds.
    assert len(results) == 1
    assert results[0].success

    # /masks/<round> is a (T,H,W) stack, one accepted frame per timepoint.
    s = DatasetStore(p)
    mask = s.read_mask("GFP_split")
    assert mask.shape == (N_TP, H, W)
    for t in range(N_TP):
        assert mask[t, 2:5, 2:5].all()

    # /groups/<round> carries a timepoint column, one block per frame.
    groups = s.read_dataframe("/groups/GFP_split")
    assert "timepoint" in groups.columns
    assert set(groups["timepoint"]) == set(range(N_TP))
    assert "group_GFP_mean_intensity" in groups.columns


def test_frame_with_no_grouping_yields_empty_mask_frame(
    tmp_path, fake_viewer, monkeypatch
):
    from percell4.gui.workflows.single_cell import threshold_qc_queue as mod

    _FakeController.instances = []
    monkeypatch.setattr(mod, "ThresholdQCController", _FakeController)

    p = tmp_path / "TL.h5"
    _store(p)
    # Timepoint 1 has no groupable cells (omitted from the dict).
    grouping_by_t = {0: _grouping(), 2: _grouping()}

    results = []
    entry = mod.TimelapseThresholdQCQueueEntry(
        viewer_win=fake_viewer,
        data_model=MagicMock(),
        entry=_make_entry(p),
        round_spec=_round(),
        grouping_by_timepoint=grouping_by_t,
        queue_index=0,
        queue_total=1,
        on_complete=results.append,
        seg_name="cellpose_tracked",
    )
    entry.start()

    # Controllers run only for the two timepoints that have a grouping.
    assert len(_FakeController.instances) == 2
    assert results[0].success

    s = DatasetStore(p)
    mask = s.read_mask("GFP_split")
    assert mask.shape == (N_TP, H, W)
    assert mask[0].any() and mask[2].any()
    assert not mask[1].any()  # skipped frame -> empty

    groups = s.read_dataframe("/groups/GFP_split")
    assert set(groups["timepoint"]) == {0, 2}


def test_user_cancel_aborts_without_writing(tmp_path, fake_viewer, monkeypatch):
    from percell4.gui.workflows.single_cell import threshold_qc_queue as mod

    class _CancellingController(_FakeController):
        def start(self):
            self._on_complete(False, "user cancelled", None)

    monkeypatch.setattr(mod, "ThresholdQCController", _CancellingController)

    p = tmp_path / "TL.h5"
    _store(p)
    grouping_by_t = {t: _grouping() for t in range(N_TP)}

    results = []
    entry = mod.TimelapseThresholdQCQueueEntry(
        viewer_win=fake_viewer,
        data_model=MagicMock(),
        entry=_make_entry(p),
        round_spec=_round(),
        grouping_by_timepoint=grouping_by_t,
        queue_index=0,
        queue_total=1,
        on_complete=results.append,
        seg_name="cellpose_tracked",
    )
    entry.start()

    assert len(results) == 1
    assert not results[0].success
    s = DatasetStore(p)
    assert "GFP_split" not in s.list_masks()
