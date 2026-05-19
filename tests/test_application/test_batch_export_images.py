"""Tests for the batch image-export orchestrator.

Exercised against real HDF5 files written via DatasetStore + h5py, with
tifffile.imwrite hitting the real filesystem under tmp_path. The
underlying ExportImages use case is NOT monkeypatched -- it's small
enough to run end-to-end and the test asserts the resulting TIFF set on
disk.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
import tifffile

from percell4.application.use_cases.batch_export_images import (
    BatchExportItemResult,
    BatchExportReport,
    _enumerate_channels,
    batch_export_images,
)
from percell4.store import DatasetStore


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    intensity_shape: tuple[int, ...] | None = (2, 4, 4),
    channel_names: list[str] | None = None,
    labels: dict[str, np.ndarray] | None = None,
    masks: dict[str, np.ndarray] | None = None,
) -> Path:
    """Create a populated .h5 via DatasetStore (so chunking + compression
    are the real production defaults)."""
    store = DatasetStore(path)
    store.create(
        metadata={"channel_names": channel_names or []}
    )
    if intensity_shape is not None:
        arr = np.zeros(intensity_shape, dtype=np.uint16)
        # Stamp the channel index into the array so per-channel files
        # are distinguishable on disk.
        if arr.ndim == 3:
            for i in range(arr.shape[0]):
                arr[i, ...] = (i + 1) * 10
        store.write_array("intensity", arr)
    for name, data in (labels or {}).items():
        store.write_labels(name, data)
    for name, data in (masks or {}).items():
        store.write_mask(name, data)
    return path


# ── _enumerate_channels helper ──────────────────────────────────────────


def test_enumerate_channels_2d_intensity_with_name():
    """A 2D /intensity uses the first channel name when present."""
    out = _enumerate_channels((4, 4), ["mNG"])
    assert out == [("mNG", 0)]


def test_enumerate_channels_2d_intensity_without_name():
    """A 2D /intensity falls back to 'Intensity' when channel_names is empty."""
    out = _enumerate_channels((4, 4), [])
    assert out == [("Intensity", 0)]


def test_enumerate_channels_3d_intensity_full_names():
    """A 3D /intensity (C, H, W) uses every name in channel_names."""
    out = _enumerate_channels((3, 4, 4), ["A", "B", "C"])
    assert out == [("A", 0), ("B", 1), ("C", 2)]


def test_enumerate_channels_3d_intensity_missing_names_fall_back_to_ch_i():
    """When channel_names is shorter than the channel dimension, missing
    slots fall back to f'ch{i}' -- mirrors read_channel_images."""
    out = _enumerate_channels((3, 4, 4), ["A", "B"])
    assert out == [("A", 0), ("B", 1), ("ch2", 2)]


def test_enumerate_channels_none_intensity():
    """Datasets without /intensity yield an empty channel list."""
    assert _enumerate_channels(None, []) == []


# ── Happy path ──────────────────────────────────────────────────────────


def test_two_datasets_two_channels_with_labels_and_masks(tmp_path: Path):
    """End-to-end: two .h5 files, intensity + labels + masks each. The
    resulting TIFFs land flat in output_dir with the dataset-stem prefix."""
    out = tmp_path / "out"
    labels = {"seg": np.zeros((4, 4), dtype=np.int32)}
    masks = {"threshold": np.zeros((4, 4), dtype=np.uint8)}
    h5_a = _make_h5(
        tmp_path / "dish_1.h5",
        channel_names=["ch0", "ch1"],
        labels=labels,
        masks=masks,
    )
    h5_b = _make_h5(
        tmp_path / "dish_2.h5",
        channel_names=["ch0", "ch1"],
        labels=labels,
        masks=masks,
    )

    report = batch_export_images([h5_a, h5_b], output_dir=out)

    assert len(report.items) == 2
    assert report.total_succeeded == 2
    assert report.total_failed == 0
    assert report.total_files_written == 8  # 4 layers x 2 datasets

    expected_files = {
        "dish_1_ch0.tif", "dish_1_ch1.tif",
        "dish_1_seg.tif", "dish_1_threshold.tif",
        "dish_2_ch0.tif", "dish_2_ch1.tif",
        "dish_2_seg.tif", "dish_2_threshold.tif",
    }
    actual_files = {p.name for p in out.iterdir()}
    assert expected_files <= actual_files


def test_two_d_intensity_single_channel_exports_one_file(tmp_path: Path):
    """A 2D /intensity (single-channel dataset) exports one TIFF."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "single.h5",
        intensity_shape=(4, 4),
        channel_names=["only"],
    )

    report = batch_export_images([h5], output_dir=out)
    item = report.items[0]
    assert item.status == "succeeded"
    assert item.files_written == 1
    assert (out / "single_only.tif").exists()


def test_channel_name_fallback_when_metadata_short(tmp_path: Path):
    """Three intensity channels but only two names -> third file is ch2."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=(3, 4, 4),
        channel_names=["A", "B"],  # third name missing
    )

    batch_export_images([h5], output_dir=out)
    files = {p.name for p in out.iterdir()}
    assert files == {"ds_A.tif", "ds_B.tif", "ds_ch2.tif"}


def test_tiff_content_matches_per_channel_data(tmp_path: Path):
    """Per-channel TIFFs carry the per-channel slice, not the whole stack."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=(2, 4, 4),
        channel_names=["A", "B"],
    )

    batch_export_images([h5], output_dir=out)

    a = tifffile.imread(out / "ds_A.tif")
    b = tifffile.imread(out / "ds_B.tif")
    assert a.shape == (4, 4)
    assert b.shape == (4, 4)
    # _make_h5 stamps channel index*10 into each slice.
    assert np.all(a == 10)
    assert np.all(b == 20)


# ── Empty / no-layer datasets ───────────────────────────────────────────


def test_empty_dataset_is_skipped_no_changes(tmp_path: Path):
    """A .h5 with /metadata only (no intensity, labels, or masks) yields
    skipped_no_changes and writes nothing."""
    out = tmp_path / "out"
    h5 = tmp_path / "empty.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    report = batch_export_images([h5], output_dir=out)
    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.files_written == 0
    # output_dir may or may not exist; either way, no files inside.
    if out.exists():
        assert list(out.iterdir()) == []


def test_dataset_with_only_labels_succeeds(tmp_path: Path):
    """No intensity but a label layer -> the label TIFF lands."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=None,  # no /intensity
        labels={"seg": np.zeros((4, 4), dtype=np.int32)},
    )

    report = batch_export_images([h5], output_dir=out)
    item = report.items[0]
    assert item.status == "succeeded"
    assert item.files_written == 1
    assert (out / "ds_seg.tif").exists()


# ── Per-dataset error isolation ─────────────────────────────────────────


def test_nonexistent_h5_isolates_as_failed(tmp_path: Path):
    """A missing .h5 fails its own item but the batch continues."""
    out = tmp_path / "out"
    h5_ok = _make_h5(tmp_path / "ok.h5", channel_names=["ch0"])
    h5_missing = tmp_path / "missing.h5"

    report = batch_export_images([h5_missing, h5_ok], output_dir=out)

    assert report.items[0].status == "failed"
    assert report.items[0].error is not None
    assert report.items[1].status == "succeeded"
    assert report.total_files_written == 2  # ch0 + ch1 from ok.h5


# ── Overwrite behavior ──────────────────────────────────────────────────


def test_existing_tif_in_output_dir_is_overwritten_silently(tmp_path: Path):
    """tifffile.imwrite overwrites existing files -- the batch inherits
    that behavior. No error, no skip, the file's contents change."""
    out = tmp_path / "out"
    out.mkdir()
    # Pre-plant a file at the target path with arbitrary content.
    pre_existing = out / "ds_only.tif"
    tifffile.imwrite(str(pre_existing), np.full((4, 4), 99, dtype=np.uint16))

    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=(4, 4),
        channel_names=["only"],
    )

    report = batch_export_images([h5], output_dir=out)
    assert report.items[0].status == "succeeded"
    # The file was replaced; its contents now match the .h5's
    # intensity slice (all zeros from _make_h5's 2D case), not the
    # pre-planted 99s.
    written = tifffile.imread(pre_existing)
    assert np.all(written == 0)


# ── Progress callback ──────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset_in_order(tmp_path: Path):
    """One BatchExportItemResult per input path, in input order."""
    out = tmp_path / "out"
    h5_a = _make_h5(tmp_path / "a.h5", channel_names=["ch0"])
    h5_b = _make_h5(tmp_path / "b.h5", channel_names=["ch0"])

    captured: list[BatchExportItemResult] = []
    batch_export_images(
        [h5_a, h5_b], output_dir=out, progress_callback=captured.append,
    )

    assert len(captured) == 2
    assert captured[0].h5_path == h5_a
    assert captured[1].h5_path == h5_b


# ── Output directory creation ──────────────────────────────────────────


def test_output_dir_created_if_missing(tmp_path: Path):
    """ExportImages.execute creates output_folder; the batch inherits
    that behavior."""
    out = tmp_path / "deeply" / "nested" / "out"
    h5 = _make_h5(tmp_path / "ds.h5", channel_names=["ch0"])

    batch_export_images([h5], output_dir=out)
    assert out.is_dir()
    assert (out / "ds_ch0.tif").exists()


# ── Empty input list ───────────────────────────────────────────────────


def test_empty_input_list_returns_empty_report(tmp_path: Path):
    """No paths -> empty report, no error, output_dir not created."""
    out = tmp_path / "out"
    report = batch_export_images([], output_dir=out)
    assert report.items == ()
    assert report.total_succeeded == 0
    assert report.total_files_written == 0
    assert not out.exists()


# ── Report aggregate properties ────────────────────────────────────────


def test_report_aggregate_properties_sum_correctly(tmp_path: Path):
    """A mixed batch (success + skipped + failed) lands the right counts."""
    out = tmp_path / "out"
    h5_ok = _make_h5(tmp_path / "ok.h5", channel_names=["ch0"])
    h5_empty = tmp_path / "empty.h5"
    with h5py.File(h5_empty, "w") as f:
        f.create_group("metadata")
    h5_missing = tmp_path / "missing.h5"

    report = batch_export_images(
        [h5_ok, h5_empty, h5_missing], output_dir=out,
    )

    assert report.total_succeeded == 1
    assert report.total_skipped == 1
    assert report.total_failed == 1
    # ok.h5 has 2 intensity channels (the _make_h5 default 3D shape),
    # so 2 TIFFs land. The other two datasets write none.
    assert report.total_files_written == 2


# ── view_bin parameter ──────────────────────────────────────────────


def test_default_view_bin_one_writes_native_shape(tmp_path: Path):
    """R7: omitting view_bin keeps the native-resolution behavior.
    A (2, 32, 32) intensity → two 32x32 TIFFs."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=(2, 32, 32),
        channel_names=["A", "B"],
    )

    batch_export_images([h5], output_dir=out)

    a = tifffile.imread(out / "ds_A.tif")
    b = tifffile.imread(out / "ds_B.tif")
    assert a.shape == (32, 32)
    assert b.shape == (32, 32)


def test_view_bin_two_produces_half_shape_tiffs(tmp_path: Path):
    """view_bin=2 on a (2, 32, 32) intensity → two 16x16 TIFFs."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=(2, 32, 32),
        channel_names=["A", "B"],
    )

    batch_export_images([h5], output_dir=out, view_bin=2)

    a = tifffile.imread(out / "ds_A.tif")
    b = tifffile.imread(out / "ds_B.tif")
    assert a.shape == (16, 16)
    assert b.shape == (16, 16)


def test_view_bin_threads_to_labels_and_masks(tmp_path: Path):
    """view_bin applies to /labels and /masks too, not just intensity."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        intensity_shape=(2, 32, 32),
        channel_names=["A", "B"],
        labels={"seg": np.zeros((32, 32), dtype=np.int32)},
        masks={"threshold": np.zeros((32, 32), dtype=np.uint8)},
    )

    batch_export_images([h5], output_dir=out, view_bin=2)

    seg = tifffile.imread(out / "ds_seg.tif")
    mask = tifffile.imread(out / "ds_threshold.tif")
    assert seg.shape == (16, 16)
    assert mask.shape == (16, 16)


def test_view_bin_per_dataset_isolation(tmp_path: Path):
    """Per-dataset error isolation still works under view_bin > 1."""
    out = tmp_path / "out"
    h5_ok = _make_h5(
        tmp_path / "ok.h5",
        intensity_shape=(2, 32, 32),
        channel_names=["ch0", "ch1"],
    )
    h5_missing = tmp_path / "missing.h5"

    report = batch_export_images(
        [h5_missing, h5_ok], output_dir=out, view_bin=2,
    )

    assert report.items[0].status == "failed"
    assert report.items[1].status == "succeeded"
    # Two TIFFs (ch0, ch1) at 16x16 from ok.h5.
    assert report.total_files_written == 2
    assert tifffile.imread(out / "ok_ch0.tif").shape == (16, 16)
