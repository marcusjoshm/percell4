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


def test_batch_channel_names_override_renames_and_resolves_seg_channel(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)  # imports one channel as "ch00"
    out = tmp_path / "still.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        channel_names=["mNG"], seg_channel="mNG",  # seg-channel uses new name
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.items[0].succeeded
    # The override is persisted to /metadata (relabel, order preserved).
    assert list(DatasetStore(out).metadata.get("channel_names", [])) == ["mNG"]


def test_batch_channel_names_count_mismatch_is_recorded_failure(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)  # one channel
    out = tmp_path / "still.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        channel_names=["a", "b"],  # two names for a one-channel dataset
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_failed == 1
    assert "channel-names" in (report.items[0].error or "")


def test_batch_seg_name_sets_segmentation_layer_name(tmp_path):
    src = tmp_path / "movie"
    _timelapse_tiffs(src, n_t=2)
    out = tmp_path / "movie.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00", seg_name="nuclei", track=True,
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.items[0].succeeded
    labels = DatasetStore(out).list_labels()
    assert "nuclei" in labels
    assert "nuclei_tracked" in labels  # tracking derives from the chosen name


def test_batch_seg_name_collision_with_channel_is_recorded_failure(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)  # channel "ch00"
    out = tmp_path / "still.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00", seg_name="ch00",  # collides with the channel name
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_failed == 1
    assert "collides" in (report.items[0].error or "")


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


# --- U2: full CellposeSettings + edge options + preprocessing + .h5 input ---

from percell4.workflows.models import CellposeSettings  # noqa: E402


class RecordingSegmenter:
    """Records the image + kwargs of each .run call; returns two interior cells."""

    def __init__(self) -> None:
        self.images: list = []
        self.kwargs: list[dict] = []

    def run(self, image, **kwargs):
        self.images.append(np.asarray(image).copy())
        self.kwargs.append(kwargs)
        h, w = image.shape[-2], image.shape[-1]
        lab = np.zeros((h, w), dtype=np.int32)
        lab[h // 4 : h // 4 + 4, w // 4 : w // 4 + 4] = 1
        lab[h // 2 : h // 2 + 4, w // 2 : w // 2 + 4] = 2
        return lab


class RawMaskSegmenter:
    """Returns a fixed raw mask regardless of input (to exercise finalize)."""

    def __init__(self, mask: np.ndarray) -> None:
        self._mask = mask.astype(np.int32)

    def run(self, image, **kwargs):
        return self._mask.copy()


def _gradient_tiff(src: Path):
    src.mkdir(parents=True)
    ramp = np.tile(np.linspace(0, 600, 40, dtype=np.uint16), (40, 1))
    ramp[0:2, :] = 6000  # hot rows so the saturation LUT visibly bites
    tifffile.imwrite(str(src / "grad_ch00.tif"), ramp)


def _read_stored_channel(out: Path, ch="ch00"):
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.adapters.null_viewer import NullViewerAdapter
    from percell4.application.session import Session
    from percell4.application.use_cases.load_dataset import LoadDataset

    repo = Hdf5DatasetRepository()
    handle = LoadDataset(repo, NullViewerAdapter(), Session()).execute(out)
    return repo.read_channel_images(handle)[ch]


def test_batch_forwards_cellpose_inference_settings(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)
    seg = RecordingSegmenter()

    batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=tmp_path / "o.h5")],
        seg_channel="ch00",
        settings=CellposeSettings(
            model="cyto3", diameter=120.0, flow_threshold=0.7,
            cellprob_threshold=-1.0, min_size=22,
            saturation_pct=0.0, blur_sigma=0.0,
        ),
        segmenter=seg, tracker=FakeTracker(),
    )

    kw = seg.kwargs[0]
    assert kw["model_type"] == "cyto3"
    assert kw["diameter"] == 120.0
    assert kw["flow_threshold"] == 0.7
    assert kw["cellprob_threshold"] == -1.0
    assert kw["min_size"] == 22


def test_batch_diameter_zero_means_auto(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)
    seg = RecordingSegmenter()

    batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=tmp_path / "o.h5")],
        seg_channel="ch00",
        settings=CellposeSettings(diameter=0.0, saturation_pct=0.0, blur_sigma=0.0),
        segmenter=seg, tracker=FakeTracker(),
    )

    assert seg.kwargs[0]["diameter"] is None  # 0 -> auto-detect


def test_batch_preprocessing_applied_without_mutating_intensity(tmp_path):
    src = tmp_path / "grad"
    _gradient_tiff(src)
    out = tmp_path / "o.h5"
    seg = RecordingSegmenter()

    batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=1.0, blur_sigma=2.0),
        segmenter=seg, tracker=FakeTracker(),
    )

    fed = seg.images[0]
    stored = _read_stored_channel(out)
    # Preprocessing changed what Cellpose saw...
    assert not np.array_equal(fed, stored)
    # ...but the on-disk /intensity was untouched (still the imported ramp).
    assert stored.max() >= 6000


def test_batch_no_preprocessing_feeds_raw_channel(tmp_path):
    src = tmp_path / "grad"
    _gradient_tiff(src)
    out = tmp_path / "o.h5"
    seg = RecordingSegmenter()

    batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=out)],
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
        segmenter=seg, tracker=FakeTracker(),
    )

    assert np.array_equal(seg.images[0], _read_stored_channel(out))


def _edge_and_interior_mask() -> np.ndarray:
    m = np.zeros((40, 40), dtype=np.int32)
    m[10:25, 10:25] = 1   # interior cell (not touching border)
    m[0:6, 30:36] = 2     # border-touching cell (row 0)
    return m


def test_batch_remove_edge_cells_toggle(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)
    mask = _edge_and_interior_mask()

    removed = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=tmp_path / "rm.h5")],
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0, min_size=1),
        remove_edge_cells=True,
        segmenter=RawMaskSegmenter(mask), tracker=FakeTracker(),
    )
    kept = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=tmp_path / "keep.h5")],
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0, min_size=1),
        remove_edge_cells=False,
        segmenter=RawMaskSegmenter(mask), tracker=FakeTracker(),
    )

    assert removed.items[0].n_cells == 1   # border cell dropped
    assert kept.items[0].n_cells == 2      # border cell retained


def test_batch_min_size_drops_small_cells(tmp_path):
    src = tmp_path / "still"
    _single_tiff(src)
    mask = np.zeros((40, 40), dtype=np.int32)
    mask[10:25, 10:25] = 1   # big interior cell (225 px)
    mask[30:32, 30:31] = 2   # tiny cell (2 px)

    big_only = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=tmp_path / "a.h5")],
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0, min_size=15),
        remove_edge_cells=False,
        segmenter=RawMaskSegmenter(mask), tracker=FakeTracker(),
    )
    both = batch_process_datasets(
        [DatasetSpec(source_dir=src, output_h5=tmp_path / "b.h5")],
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0, min_size=1),
        remove_edge_cells=False,
        segmenter=RawMaskSegmenter(mask), tracker=FakeTracker(),
    )

    assert big_only.items[0].n_cells == 1  # tiny cell filtered by min_size=15
    assert both.items[0].n_cells == 2      # min_size=1 keeps both


def _make_source_h5(tmp_path: Path, name: str, timelapse=False) -> Path:
    """Import TIFFs to a standalone .h5 to use as an already-compressed source."""
    from percell4.adapters.importer import import_dataset

    src = tmp_path / f"{name}_tiffs"
    if timelapse:
        _timelapse_tiffs(src, n_t=2)
    else:
        _single_tiff(src)
    h5 = tmp_path / f"{name}.h5"
    import_dataset(src, h5)
    return h5


def test_batch_h5_source_segments_in_place(tmp_path):
    h5 = _make_source_h5(tmp_path, "dish")

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],  # in place
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.items[0].succeeded
    # Segmentation landed in the same file; channel still present.
    labels = DatasetStore(h5).list_labels()
    assert any(not n.endswith("_tracked") for n in labels)
    assert "ch00" in DatasetStore(h5).metadata.get("channel_names", [])


def test_batch_h5_source_copies_to_output_and_leaves_original(tmp_path):
    import hashlib

    h5 = _make_source_h5(tmp_path, "dish")
    before = hashlib.sha256(h5.read_bytes()).hexdigest()
    out = tmp_path / "copied" / "dish.h5"

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=out)],  # copy then segment
        seg_channel="ch00",
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.items[0].succeeded
    assert out.exists()
    assert any(not n.endswith("_tracked") for n in DatasetStore(out).list_labels())
    # The original .h5 is byte-for-byte unchanged.
    assert hashlib.sha256(h5.read_bytes()).hexdigest() == before


def test_batch_h5_source_timelapse_tracks(tmp_path):
    h5 = _make_source_h5(tmp_path, "movie", timelapse=True)

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_channel="ch00", track=True,
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.items[0].n_timepoints == 2
    assert report.items[0].tracked is True
    assert any(n.endswith("_tracked") for n in DatasetStore(h5).list_labels())


def test_batch_h5_in_place_seg_name_collision_recorded(tmp_path):
    h5 = _make_source_h5(tmp_path, "dish")

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_channel="ch00", seg_name="ch00",  # collides with existing channel
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_failed == 1
    assert "collides" in (report.items[0].error or "")


# --- skip_segmentation (track-only) ---

class RaisingSegmenter:
    """Fails the test if segmentation is invoked (proves it was skipped)."""

    def run(self, image, **kwargs):
        raise AssertionError("segmenter.run must not be called when skipping")


def _seed_segmentation(h5: Path, seg_channel="ch00") -> str:
    """Run one segment pass (no tracking) so an existing raw seg layer exists.

    Returns the raw segmentation layer name.
    """
    batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_channel=seg_channel, track=False,
        settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
        segmenter=FakeSegmenter(), tracker=FakeTracker(),
    )
    labels = [n for n in DatasetStore(h5).list_labels() if not n.endswith("_tracked")]
    assert labels, "expected a raw segmentation layer to be seeded"
    return labels[0]


def test_skip_segmentation_tracks_existing_without_cellpose(tmp_path):
    h5 = _make_source_h5(tmp_path, "movie", timelapse=True)
    seg = _seed_segmentation(h5)

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_name=seg, skip_segmentation=True, track=True,
        segmenter=RaisingSegmenter(),  # must NOT be called
        tracker=FakeTracker(),
    )

    assert report.items[0].succeeded
    assert report.items[0].tracked is True
    assert report.items[0].n_tracks > 0
    assert f"{seg}_tracked" in DatasetStore(h5).list_labels()


def test_skip_segmentation_requires_seg_name(tmp_path):
    h5 = _make_source_h5(tmp_path, "movie", timelapse=True)

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_name=None, skip_segmentation=True, track=True,
        segmenter=RaisingSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_failed == 1
    assert "requires seg_name" in (report.items[0].error or "")


def test_skip_segmentation_nonexistent_layer_recorded(tmp_path):
    h5 = _make_source_h5(tmp_path, "movie", timelapse=True)

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_name="does_not_exist", skip_segmentation=True, track=True,
        segmenter=RaisingSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_failed == 1
    assert "not found" in (report.items[0].error or "")


def test_skip_segmentation_single_timepoint_recorded(tmp_path):
    h5 = _make_source_h5(tmp_path, "still")  # single timepoint
    seg = _seed_segmentation(h5)

    report = batch_process_datasets(
        [DatasetSpec(source_dir=h5, output_h5=h5)],
        seg_name=seg, skip_segmentation=True, track=True,
        segmenter=RaisingSegmenter(), tracker=FakeTracker(),
    )

    assert report.n_failed == 1
    assert "nothing to do" in (report.items[0].error or "")


# --- verbose instrumentation (DEBUG progress + timing) ---

def test_batch_emits_debug_progress(tmp_path, caplog):
    import logging
    import re

    h5 = _make_source_h5(tmp_path, "movie", timelapse=True)

    with caplog.at_level(logging.DEBUG):  # capture batch + segment_cells loggers
        batch_process_datasets(
            [DatasetSpec(source_dir=h5, output_h5=h5)],
            seg_channel="ch00", track=True,
            settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
            segmenter=FakeSegmenter(), tracker=FakeTracker(),
        )

    text = "\n".join(r.getMessage() for r in caplog.records)
    # Per-frame model time + cell count (from SegmentCells).
    assert re.search(r"cellpose frame 1/2: \d+ cells found in [\d.]+ s", text)
    assert re.search(r"cellpose frame 2/2: \d+ cells found in [\d.]+ s", text)
    # Per-dataset totals + tracking (from the batch use case).
    assert "cellpose inference finished" in text
    assert "tracking" in text


def test_run_inference_logs_time_and_count_single_frame(tmp_path, caplog):
    """Single-image path logs model time + cell count too."""
    import logging

    src = tmp_path / "still"
    _single_tiff(src)

    with caplog.at_level(logging.DEBUG):
        batch_process_datasets(
            [DatasetSpec(source_dir=src, output_h5=tmp_path / "o.h5")],
            seg_channel="ch00",
            settings=CellposeSettings(saturation_pct=0.0, blur_sigma=0.0),
            segmenter=FakeSegmenter(), tracker=FakeTracker(),
        )

    text = "\n".join(r.getMessage() for r in caplog.records)
    import re
    assert re.search(r"cellpose: \d+ cells found in [\d.]+ s", text)
