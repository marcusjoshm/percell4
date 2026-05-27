"""Tests for the whole-field segmentation batch orchestrator.

Writes ``/labels/whole_field`` (int32, every pixel = 1) into each input
``.h5``. Shape inferred from ``metadata.native_shape`` when present,
otherwise from any ``/decay/<channel>``. Always-overwrite per the v1
contract.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.application.use_cases.batch_create_whole_field_segmentation import (
    batch_create_whole_field_segmentation,
)
from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    BatchOperationReport,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channels: list[str] | None = None,
    native_shape: tuple[int, int] | None = (4, 4),
    n_timepoints: int = 1,
    with_existing_whole_field: bool = False,
) -> Path:
    """Build a minimal .h5 fixture. Decay arrays have shape (H, W, 8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = native_shape if native_shape is not None else (0, 0)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels or []
        meta.attrs["flim_frequency_mhz"] = 80.0
        if native_shape is not None:
            meta.attrs["native_shape"] = list(native_shape)
        if n_timepoints > 1:
            meta.attrs["n_timepoints"] = n_timepoints
        if channels and native_shape is not None:
            decay = f.create_group("decay")
            for ch in channels:
                decay.create_dataset(
                    ch, data=np.zeros((h, w, 8), dtype=np.float32),
                )
        if with_existing_whole_field and native_shape is not None:
            lgrp = f.create_group("labels")
            # Pre-existing labels are zeros — the use case should
            # overwrite with ones.
            lgrp.create_dataset(
                "whole_field", data=np.zeros((h, w), dtype=np.int32),
            )
    return path


def _read_whole_field(path: Path) -> np.ndarray:
    with h5py.File(path, "r") as f:
        return np.asarray(f["labels/whole_field"][:])


def _has_whole_field(path: Path) -> bool:
    with h5py.File(path, "r") as f:
        return "labels/whole_field" in f


# ── Happy paths ─────────────────────────────────────────────────────────


def test_writes_whole_field_with_correct_shape_and_dtype(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], native_shape=(6, 8))

    report = batch_create_whole_field_segmentation([a])

    item = report.items[0]
    assert item.status == "succeeded"
    assert item.processed == ("whole_field",)

    arr = _read_whole_field(a)
    assert arr.shape == (6, 8)
    assert arr.dtype == np.int32
    assert (arr == 1).all()


def test_processes_multiple_files(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], native_shape=(4, 4))
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"], native_shape=(5, 7))

    report = batch_create_whole_field_segmentation([a, b])

    a_item, b_item = report.items
    assert a_item.status == "succeeded"
    assert b_item.status == "succeeded"
    assert _read_whole_field(a).shape == (4, 4)
    assert _read_whole_field(b).shape == (5, 7)


def test_silently_overwrites_existing_whole_field(tmp_path):
    """Pre-existing /labels/whole_field is replaced — every pixel = 1
    after the run regardless of what was there before."""
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["ch0"],
        native_shape=(4, 4),
        with_existing_whole_field=True,
    )
    # Sanity: starts as zeros.
    assert (_read_whole_field(a) == 0).all()

    report = batch_create_whole_field_segmentation([a])

    item = report.items[0]
    assert item.status == "succeeded"
    assert (_read_whole_field(a) == 1).all()


# ── Shape inference fallbacks ───────────────────────────────────────────


def test_shape_inferred_from_decay_when_native_shape_missing(tmp_path):
    """Dataset has decay but no /metadata.native_shape → use case reads
    shape from the first /decay/<channel> group."""
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["ch0"],
        native_shape=None,
    )
    # Manually add decay since _make_h5 skips it when native_shape is None.
    with h5py.File(a, "a") as f:
        decay = f.create_group("decay")
        decay.create_dataset(
            "ch0", data=np.zeros((3, 5, 8), dtype=np.float32),
        )

    report = batch_create_whole_field_segmentation([a])

    item = report.items[0]
    assert item.status == "succeeded"
    assert _read_whole_field(a).shape == (3, 5)


# ── Dry run ─────────────────────────────────────────────────────────────


def test_dry_run_does_not_mutate(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], native_shape=(4, 4))
    assert not _has_whole_field(a)

    report = batch_create_whole_field_segmentation([a], dry_run=True)

    item = report.items[0]
    assert item.status == "succeeded"
    assert item.processed == ("whole_field",)
    # Nothing on disk.
    assert not _has_whole_field(a)


def test_dry_run_reports_overwrite_for_existing_whole_field(tmp_path):
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["ch0"],
        native_shape=(4, 4),
        with_existing_whole_field=True,
    )

    report = batch_create_whole_field_segmentation([a], dry_run=True)

    # Still succeeded — the run would overwrite — but the existing
    # mask stays untouched in dry-run mode.
    item = report.items[0]
    assert item.status == "succeeded"
    assert (_read_whole_field(a) == 0).all()  # unchanged


# ── Failure modes ───────────────────────────────────────────────────────


def test_dataset_without_shape_or_decay_is_failed(tmp_path):
    """Empty dataset with no native_shape and no /decay/ → can't infer
    shape → item.status == 'failed' with a clear error message."""
    a = tmp_path / "empty.h5"
    with h5py.File(a, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = []
        # No native_shape, no decay group.

    report = batch_create_whole_field_segmentation([a])

    item = report.items[0]
    assert item.status == "failed"
    assert item.error is not None
    assert "shape" in item.error.lower()


def test_missing_file_marked_failed_batch_continues(tmp_path):
    missing = tmp_path / "nope.h5"
    real = _make_h5(tmp_path / "real.h5", channels=["ch0"], native_shape=(4, 4))

    report = batch_create_whole_field_segmentation([missing, real])

    miss_item, real_item = report.items
    assert miss_item.status == "failed"
    assert miss_item.error is not None
    assert real_item.status == "succeeded"
    assert _has_whole_field(real)


# ── Progress callback ──────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset_in_order(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], native_shape=(4, 4))
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"], native_shape=(4, 4))
    seen: list[Path] = []
    batch_create_whole_field_segmentation(
        [a, b],
        progress_callback=lambda item: seen.append(item.h5_path),
    )
    assert seen == [a, b]


# ── Empty input ────────────────────────────────────────────────────────


def test_empty_input_list_returns_empty_report():
    report = batch_create_whole_field_segmentation([])
    assert isinstance(report, BatchOperationReport)
    assert report.items == ()
    assert report.total_succeeded == 0
