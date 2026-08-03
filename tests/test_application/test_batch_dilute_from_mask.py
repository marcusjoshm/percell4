"""Tests for the batch dilute-from-mask orchestrator (U2).

The orchestrator is exercised against real HDF5 files. The expected
dilute mask is recomputed independently from the plan's formula
``(seg_labels > 0) & ~dilation(condensed, disk(radius))`` (raw skimage,
not the domain helper under test) so the assertions verify the
end-to-end wiring — read → compute → exact-``T`` write → re-read — not a
tautology against ``dilute_from_mask``.

Fixtures are synthesized in-place: ``/metadata`` carries
``channel_names`` + ``n_timepoints`` (+ ``native_shape`` for time-lapse),
``/masks/<name>`` is uint8, ``/labels/<seg>`` is int32. Time-stacked
resources are stamped ``dims=["T","H","W"]`` so the store's per-frame
read path slices instead of broadcasting.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
from skimage.morphology import dilation, disk

from percell4.application.use_cases.batch_dilute_from_mask import (
    DiluteItemResult,
    DiluteReport,
    batch_dilute_from_mask,
)

# ── Oracle + fixture helpers ────────────────────────────────────────────


def _expected_dilute(
    condensed: np.ndarray, seg: np.ndarray, radius: int
) -> np.ndarray:
    """Independent reference: the plan's formula via raw skimage."""
    cond = condensed.astype(bool)
    grown = dilation(cond, disk(radius)) if radius > 0 else cond
    return (seg > 0) & ~grown


def _dims_for(arr: np.ndarray) -> list[str]:
    return ["T", "H", "W"] if arr.ndim == 3 else ["H", "W"]


def _make_h5(
    path: Path,
    *,
    channels: list[str] | None = None,
    n_timepoints: int = 1,
    native_shape: tuple[int, int] | None = None,
    masks: dict[str, np.ndarray] | None = None,
    labels: dict[str, np.ndarray] | None = None,
) -> Path:
    """Create a minimal .h5 with /metadata + optional /masks + /labels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    channels = channels if channels is not None else ["ch0"]
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        meta.attrs["n_timepoints"] = n_timepoints
        if native_shape is not None:
            meta.attrs["native_shape"] = native_shape
        if masks:
            mg = f.create_group("masks")
            for name, arr in masks.items():
                ds = mg.create_dataset(name, data=arr.astype(np.uint8))
                ds.attrs["dims"] = _dims_for(arr)
        if labels:
            lg = f.create_group("labels")
            for name, arr in labels.items():
                ds = lg.create_dataset(name, data=arr.astype(np.int32))
                ds.attrs["dims"] = _dims_for(arr)
    return path


def _two_cells(shape: tuple[int, int]) -> np.ndarray:
    """Two cells: left half label 1, right half label 2 (fills the frame)."""
    h, w = shape
    lab = np.zeros(shape, dtype=np.int32)
    lab[:, : w // 2] = 1
    lab[:, w // 2 :] = 2
    return lab


def _square(shape: tuple[int, int], top, bottom, left, right) -> np.ndarray:
    m = np.zeros(shape, dtype=np.uint8)
    m[top:bottom, left:right] = 1
    return m


def _read_mask(path: Path, name: str) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return f[f"masks/{name}"][()]


def _read_labels(path: Path, name: str) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return f[f"labels/{name}"][()]


def _mask_exists(path: Path, name: str) -> bool:
    with h5py.File(path, "r") as f:
        return f"masks/{name}" in f


# ── Happy path: single 2D file (the test-first e2e gate) ─────────────────


def test_single_2d_file_writes_expected_mask(tmp_path: Path) -> None:
    shape = (12, 12)
    condensed = _square(shape, 4, 7, 4, 7)  # a 3x3 blob in cell 1
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": condensed},
        labels={"cells": seg},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=3,
        output_name="dilute",
    )

    assert isinstance(report, DiluteReport)
    assert len(report.items) == 1
    item = report.items[0]
    assert isinstance(item, DiluteItemResult)
    assert item.status == "processed"
    assert report.total_processed == 1

    on_disk = _read_mask(h5, "dilute")
    expected = _expected_dilute(condensed, seg, 3)
    assert on_disk.dtype == np.uint8
    assert on_disk.ndim == 2
    np.testing.assert_array_equal(on_disk, expected.astype(np.uint8))
    # binary on disk
    assert set(np.unique(on_disk)).issubset({0, 1})


def test_radius_zero_is_pure_invert_within_cells(tmp_path: Path) -> None:
    shape = (10, 10)
    condensed = _square(shape, 2, 5, 2, 5)
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": condensed},
        labels={"cells": seg},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=0,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    on_disk = _read_mask(h5, "dilute")
    expected = ((seg > 0) & ~condensed.astype(bool)).astype(np.uint8)
    np.testing.assert_array_equal(on_disk, expected)


# ── Happy path: multiple files ───────────────────────────────────────────


def test_three_files_all_processed(tmp_path: Path) -> None:
    shape = (10, 10)
    seg = _two_cells(shape)
    paths = []
    for i in range(3):
        cond = _square(shape, 1 + i, 4 + i, 1, 4)
        paths.append(
            _make_h5(
                tmp_path / f"ds{i}.h5",
                masks={"condensed": cond},
                labels={"cells": seg},
                native_shape=shape,
            )
        )

    report = batch_dilute_from_mask(
        paths,
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=2,
        output_name="dilute",
    )

    assert report.total_processed == 3
    assert all(it.status == "processed" for it in report.items)
    for p in paths:
        assert _mask_exists(p, "dilute")


def test_duplicate_paths_deduped(tmp_path: Path) -> None:
    shape = (8, 8)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": _square(shape, 2, 4, 2, 4)},
        labels={"cells": _two_cells(shape)},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5, h5, h5.resolve()],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    assert len(report.items) == 1


# ── Time-lapse ───────────────────────────────────────────────────────────


def test_timelapse_thw_mask_and_thw_seg(tmp_path: Path) -> None:
    nt, shape = 3, (8, 8)
    seg = np.stack([_two_cells(shape) for _ in range(nt)], axis=0)
    cond = np.zeros((nt, *shape), dtype=np.uint8)
    for t in range(nt):
        cond[t, 1 + t : 3 + t, 1:3] = 1  # blob moves down per frame
    h5 = _make_h5(
        tmp_path / "tl.h5",
        n_timepoints=nt,
        native_shape=shape,
        masks={"condensed": cond},
        labels={"cells": seg},
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=2,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    on_disk = _read_mask(h5, "dilute")
    assert on_disk.shape == (nt, *shape)  # exact-T
    with h5py.File(h5, "r") as f:
        assert list(f["masks/dilute"].attrs["dims"]) == ["T", "H", "W"]
    for t in range(nt):
        np.testing.assert_array_equal(
            on_disk[t], _expected_dilute(cond[t], seg[t], 2).astype(np.uint8)
        )
    # frames genuinely differ
    assert not np.array_equal(on_disk[0], on_disk[1])


def test_timelapse_thw_mask_2d_seg_broadcasts(tmp_path: Path) -> None:
    nt, shape = 3, (8, 8)
    seg2d = _two_cells(shape)
    cond = np.zeros((nt, *shape), dtype=np.uint8)
    for t in range(nt):
        cond[t, 1 + t : 3 + t, 1:3] = 1
    h5 = _make_h5(
        tmp_path / "tl.h5",
        n_timepoints=nt,
        native_shape=shape,
        masks={"condensed": cond},
        labels={"cells": seg2d},  # 2D seg, time-invariant
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=2,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    on_disk = _read_mask(h5, "dilute")
    assert on_disk.shape == (nt, *shape)
    for t in range(nt):
        np.testing.assert_array_equal(
            on_disk[t], _expected_dilute(cond[t], seg2d, 2).astype(np.uint8)
        )


def test_timelapse_2d_mask_thw_seg_broadcasts(tmp_path: Path) -> None:
    """Reverse broadcast: a 2D (time-invariant) mask + a (T,H,W) segmentation →
    the 2D mask broadcasts per frame, the seg varies per frame, output is
    exact-T (T,H,W) and genuinely differs across frames (per-frame compute)."""
    nt, shape = 3, (8, 8)
    cond2d = _square(shape, 2, 5, 2, 5)  # 2D condensed mask, time-invariant
    seg = np.zeros((nt, *shape), dtype=np.int32)
    for t in range(nt):
        seg[t, 1 : 6 + t, 1 : 6 + t] = 1  # one cell that grows per frame
    h5 = _make_h5(
        tmp_path / "tl.h5",
        n_timepoints=nt,
        native_shape=shape,
        masks={"condensed": cond2d},  # 2D, time-invariant
        labels={"cells": seg},  # (T,H,W), varies per frame
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    on_disk = _read_mask(h5, "dilute")
    assert on_disk.shape == (nt, *shape)
    for t in range(nt):
        np.testing.assert_array_equal(
            on_disk[t], _expected_dilute(cond2d, seg[t], 1).astype(np.uint8)
        )
    assert not np.array_equal(on_disk[0], on_disk[2])  # per-frame, not broadcast


def test_multi_t_but_both_inputs_2d_writes_2d(tmp_path: Path) -> None:
    nt, shape = 3, (8, 8)
    cond = _square(shape, 2, 4, 2, 4)
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "tl.h5",
        n_timepoints=nt,
        native_shape=shape,
        masks={"condensed": cond},  # 2D
        labels={"cells": seg},  # 2D
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=2,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    on_disk = _read_mask(h5, "dilute")
    assert on_disk.ndim == 2  # time-invariant → 2D
    np.testing.assert_array_equal(
        on_disk, _expected_dilute(cond, seg, 2).astype(np.uint8)
    )
    with h5py.File(h5, "r") as f:
        assert list(f["masks/dilute"].attrs["dims"]) == ["H", "W"]


def test_single_timepoint_writes_2d(tmp_path: Path) -> None:
    shape = (8, 8)
    cond = _square(shape, 2, 4, 2, 4)
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        n_timepoints=1,
        native_shape=shape,
        masks={"condensed": cond},
        labels={"cells": seg},
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=2,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    assert _read_mask(h5, "dilute").ndim == 2


# ── Binarize ─────────────────────────────────────────────────────────────


def test_condensed_0_255_binarized_on_disk(tmp_path: Path) -> None:
    shape = (10, 10)
    cond = np.zeros(shape, dtype=np.uint8)
    cond[3:6, 3:6] = 255  # 0/255, not 0/1
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": cond},
        labels={"cells": seg},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    assert report.items[0].status == "processed"
    on_disk = _read_mask(h5, "dilute")
    assert on_disk.dtype == np.uint8
    assert set(np.unique(on_disk)).issubset({0, 1})
    np.testing.assert_array_equal(
        on_disk, _expected_dilute(cond, seg, 1).astype(np.uint8)
    )


# ── Skips: missing inputs ────────────────────────────────────────────────


def test_missing_mask_skips_other_files_processed(tmp_path: Path) -> None:
    shape = (8, 8)
    good = _make_h5(
        tmp_path / "good.h5",
        masks={"condensed": _square(shape, 2, 4, 2, 4)},
        labels={"cells": _two_cells(shape)},
        native_shape=shape,
    )
    # no /masks/condensed (only a different mask)
    bad = _make_h5(
        tmp_path / "bad.h5",
        masks={"other": _square(shape, 1, 2, 1, 2)},
        labels={"cells": _two_cells(shape)},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [bad, good],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    bad_item, good_item = report.items[0], report.items[1]
    assert bad_item.status == "skipped"
    assert "mask not present" in bad_item.message
    assert good_item.status == "processed"
    assert _mask_exists(good, "dilute")


def test_missing_segmentation_skips(tmp_path: Path) -> None:
    shape = (8, 8)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": _square(shape, 2, 4, 2, 4)},
        labels={"other": _two_cells(shape)},  # no 'cells'
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    item = report.items[0]
    assert item.status == "skipped"
    assert "segmentation not present" in item.message


# ── Skips: output-name collision ─────────────────────────────────────────


def test_collision_with_channel_skips(tmp_path: Path) -> None:
    shape = (8, 8)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["dilute"],  # output name collides with a channel
        masks={"condensed": _square(shape, 2, 4, 2, 4)},
        labels={"cells": _two_cells(shape)},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    item = report.items[0]
    assert item.status == "skipped"
    assert "collides with channel" in item.message
    assert not _mask_exists(h5, "dilute")  # nothing written


def test_collision_with_existing_mask_leaves_it_untouched(tmp_path: Path) -> None:
    shape = (8, 8)
    cond = _square(shape, 2, 4, 2, 4)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": cond},
        labels={"cells": _two_cells(shape)},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="condensed",  # collides with the source mask
    )

    item = report.items[0]
    assert item.status == "skipped"
    assert "collides with mask" in item.message
    np.testing.assert_array_equal(_read_mask(h5, "condensed"), cond)


def test_collision_with_existing_label_skips(tmp_path: Path) -> None:
    shape = (8, 8)
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": _square(shape, 2, 4, 2, 4)},
        labels={"cells": seg},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="cells",  # collides with a /labels entry
    )

    item = report.items[0]
    assert item.status == "skipped"
    assert "collides with label" in item.message
    assert not _mask_exists(h5, "cells")
    np.testing.assert_array_equal(_read_labels(h5, "cells"), seg)


# ── Empty-output annotation ──────────────────────────────────────────────


def test_huge_radius_yields_empty_processed_with_flag(tmp_path: Path) -> None:
    shape = (10, 10)
    cond = _square(shape, 4, 6, 4, 6)
    seg = _two_cells(shape)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": cond},
        labels={"cells": seg},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=40,  # disk(40) fills a 10x10 frame entirely
        output_name="dilute",
    )

    item = report.items[0]
    assert item.status == "processed"
    assert "empty" in item.message.lower()
    assert int(_read_mask(h5, "dilute").sum()) == 0


def test_zero_cell_segmentation_yields_empty(tmp_path: Path) -> None:
    shape = (8, 8)
    cond = _square(shape, 2, 4, 2, 4)
    seg = np.zeros(shape, dtype=np.int32)  # no cells
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": cond},
        labels={"cells": seg},
        native_shape=shape,
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    item = report.items[0]
    assert item.status == "processed"
    assert "empty" in item.message.lower()
    assert int(_read_mask(h5, "dilute").sum()) == 0


# ── Error paths ──────────────────────────────────────────────────────────


def test_shape_mismatch_fails_with_clear_reason(tmp_path: Path) -> None:
    cond = _square((10, 10), 2, 4, 2, 4)
    seg = _two_cells((12, 12))  # different (H, W)
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": cond},
        labels={"cells": seg},
        # no native_shape: 2D writes don't validate against it anyway
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    item = report.items[0]
    assert item.status == "failed"
    assert "shape" in item.message.lower()
    assert not _mask_exists(h5, "dilute")


def test_missing_file_fails_batch_continues(tmp_path: Path) -> None:
    shape = (8, 8)
    good = _make_h5(
        tmp_path / "good.h5",
        masks={"condensed": _square(shape, 2, 4, 2, 4)},
        labels={"cells": _two_cells(shape)},
        native_shape=shape,
    )
    missing = tmp_path / "nope.h5"

    report = batch_dilute_from_mask(
        [missing, good],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    assert report.items[0].status == "failed"
    assert report.items[0].message
    assert report.items[1].status == "processed"


def test_timestacked_leading_axis_mismatch_fails(tmp_path: Path) -> None:
    """Defensive pre-check: a time-stacked mask whose leading axis disagrees
    with n_timepoints fails with a clear reason, not a mid-loop IndexError."""
    nt, shape = 3, (8, 8)
    cond = np.zeros((2, *shape), dtype=np.uint8)  # only 2 frames, nt=3
    cond[:, 1:3, 1:3] = 1
    seg = np.stack([_two_cells(shape) for _ in range(nt)], axis=0)
    h5 = _make_h5(
        tmp_path / "tl.h5",
        n_timepoints=nt,
        native_shape=shape,
        masks={"condensed": cond},  # stamped dims=["T","H","W"], leading=2
        labels={"cells": seg},
    )

    report = batch_dilute_from_mask(
        [h5],
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )

    item = report.items[0]
    assert item.status == "failed"
    assert "mis-stack" in item.message or "frame" in item.message
    assert not _mask_exists(h5, "dilute")


# ── Cancel ───────────────────────────────────────────────────────────────


def test_cancel_check_stops_early(tmp_path: Path) -> None:
    shape = (8, 8)
    seg = _two_cells(shape)
    paths = [
        _make_h5(
            tmp_path / f"ds{i}.h5",
            masks={"condensed": _square(shape, 2, 4, 2, 4)},
            labels={"cells": seg},
            native_shape=shape,
        )
        for i in range(3)
    ]

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] == 3  # fire before the 3rd dataset

    seen: list[Path] = []
    report = batch_dilute_from_mask(
        paths,
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
        cancel_check=cancel,
        progress_callback=lambda it: seen.append(it.h5_path),
    )

    assert len(report.items) == 2
    assert len(seen) == 2
    assert not _mask_exists(paths[2], "dilute")  # 3rd never processed
    assert _mask_exists(paths[0], "dilute")


# ── Progress callback ────────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset(tmp_path: Path) -> None:
    shape = (8, 8)
    seg = _two_cells(shape)
    paths = [
        _make_h5(
            tmp_path / f"ds{i}.h5",
            masks={"condensed": _square(shape, 2, 4, 2, 4)},
            labels={"cells": seg},
            native_shape=shape,
        )
        for i in range(2)
    ]

    captured: list[DiluteItemResult] = []
    batch_dilute_from_mask(
        paths,
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
        progress_callback=captured.append,
    )

    assert [c.h5_path for c in captured] == paths


# ── Input validation (before any I/O) ────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"output_name": ""}, r"output_name"),
        ({"radius_px": -1}, r"radius"),
        ({"mask_name": ""}, r"mask_name"),
        ({"segmentation_name": ""}, r"segmentation"),
    ],
)
def test_invalid_args_raise(tmp_path: Path, kwargs: dict, match: str) -> None:
    h5 = _make_h5(
        tmp_path / "ds.h5",
        masks={"condensed": _square((8, 8), 2, 4, 2, 4)},
        labels={"cells": _two_cells((8, 8))},
        native_shape=(8, 8),
    )
    base = dict(
        mask_name="condensed",
        segmentation_name="cells",
        radius_px=1,
        output_name="dilute",
    )
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        batch_dilute_from_mask([h5], **base)


def test_empty_paths_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"h5_paths"):
        batch_dilute_from_mask(
            [],
            mask_name="condensed",
            segmentation_name="cells",
            radius_px=1,
            output_name="dilute",
        )
