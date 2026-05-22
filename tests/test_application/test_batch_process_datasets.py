"""Headless batch compress+segment+track use case (U10)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import tifffile

from percell4.application.use_cases.batch_process_datasets import (
    DatasetSpec,
    batch_process_datasets,
)
from percell4.ports.tracker import TrackingResult
from percell4.store import DatasetStore


class FakeSegmenter:
    """Returns two interior cells regardless of input (survives postprocess)."""

    def run(self, image, **kwargs):
        h, w = image.shape[-2], image.shape[-1]
        lab = np.zeros((h, w), dtype=np.int32)
        lab[h // 4 : h // 4 + 4, w // 4 : w // 4 + 4] = 1
        lab[h // 2 : h // 2 + 4, w // 2 : w // 2 + 4] = 2
        return lab


class FakeTracker:
    """Maps each cell to a stable track (track_id = label - 1)."""

    def track(self, stack):
        rows = []
        for t in range(stack.shape[0]):
            for lab in np.unique(stack[t]):
                if lab == 0:
                    continue
                rows.append((t, int(lab), int(lab) - 1, 0))
        track_df = pd.DataFrame(
            rows, columns=["timepoint", "label", "track_id", "tree_id"]
        )
        split_df = pd.DataFrame(columns=["parent_track_id", "child_track_id"])
        return TrackingResult(track_df=track_df, split_df=split_df)


def _timelapse_tiffs(src: Path, n_t=2):
    src.mkdir(parents=True)
    for t in range(n_t):
        tifffile.imwrite(
            str(src / f"movie_t{t:02d}_ch00.tif"),
            np.full((40, 40), 100 + t, dtype=np.uint16),
        )


def _single_tiff(src: Path):
    src.mkdir(parents=True)
    tifffile.imwrite(str(src / "still_ch00.tif"),
                     np.full((40, 40), 100, dtype=np.uint16))


def test_batch_timelapse_segments_and_tracks(tmp_path):
    src = tmp_path / "movie"
    _timelapse_tiffs(src, n_t=2)
    out = tmp_path / "movie.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00", track=True,
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_succeeded == 1 and report.n_failed == 0
    item = report.items[0]
    assert item.n_timepoints == 2
    assert item.tracked is True
    assert item.n_tracks == 2
    # Both the raw and tracked segmentation resources exist on disk.
    store = DatasetStore(out)
    labels = store.list_labels()
    assert any(n.endswith("_tracked") for n in labels)
    assert any(not n.endswith("_tracked") for n in labels)


def test_batch_single_timepoint_not_tracked(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)
    out = tmp_path / "still.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00", track=True,
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    item = report.items[0]
    assert item.succeeded
    assert item.n_timepoints == 1
    assert item.tracked is False


def test_batch_track_false_skips_tracking(tmp_path):
    src = tmp_path / "movie"
    _timelapse_tiffs(src, n_t=2)
    out = tmp_path / "movie.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00", track=False,
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )
    assert report.items[0].tracked is False
    assert not any(n.endswith("_tracked") for n in DatasetStore(out).list_labels())


def test_batch_continues_after_per_dataset_failure(tmp_path):
    bad = DatasetSpec(source_dir=tmp_path / "nonexistent", output_h5=tmp_path / "bad.h5")
    good_src = tmp_path / "movie"
    _timelapse_tiffs(good_src, n_t=2)
    good = DatasetSpec(source_dir=good_src, output_h5=tmp_path / "good.h5")

    calls = []
    report = batch_process_datasets(
        [bad, good], seg_channel="ch00",
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
        progress_callback=lambda d, n, m: calls.append((d, n)),
    )

    assert report.n_failed == 1
    assert report.n_succeeded == 1
    assert report.items[0].error is not None  # bad dataset recorded
    assert report.items[1].succeeded          # good dataset still processed
    assert calls == [(1, 2), (2, 2)]           # progress fired for both
