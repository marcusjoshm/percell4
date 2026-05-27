"""Tests for the batch rename orchestrator.

Mirrors the structure of test_batch_compute_phasor.py — exercises the
orchestrator against real HDF5 files (so existence checks and
domain-method calls are observable) without monkeypatching the
DatasetStore methods themselves. The goal is orchestration shape,
per-dataset isolation, and dry-run behavior, not re-testing
rename_channel / rename_item.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.application.use_cases.batch_rename_resource import (
    BatchOperationItemResult,
    BatchOperationReport,
    batch_rename_resource,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channels: list[str] | None = None,
    masks: list[str] | None = None,
    segs: list[str] | None = None,
    with_phasor: list[str] | None = None,
) -> Path:
    """Build a minimal .h5 fixture with the requested per-kind contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels or []
        meta.attrs["flim_frequency_mhz"] = 80.0
        if channels:
            decay = f.create_group("decay")
            for ch in channels:
                decay.create_dataset(ch, data=np.zeros((4, 4, 8), dtype=np.float32))
                meta.attrs[f"flim_cal_phase_{ch}"] = 0.1
                meta.attrs[f"flim_cal_mod_{ch}"] = 0.9
        if with_phasor:
            phasor = f.create_group("phasor")
            for ch in with_phasor:
                g = phasor.create_group(ch)
                g.create_dataset("g", data=np.zeros((4, 4), dtype=np.float32))
        if masks:
            mgrp = f.create_group("masks")
            for m in masks:
                mgrp.create_dataset(m, data=np.zeros((4, 4), dtype=np.uint8))
        if segs:
            lgrp = f.create_group("labels")
            for s in segs:
                lgrp.create_dataset(s, data=np.zeros((4, 4), dtype=np.int32))
    return path


def _h5_has(path: Path, hdf5_path: str) -> bool:
    with h5py.File(path, "r") as f:
        return hdf5_path in f


# ── Happy path per kind ─────────────────────────────────────────────────


def test_rename_channel_across_two_files(tmp_path):
    """Two files, both have the channel — both get renamed end-to-end."""
    a = _make_h5(tmp_path / "a.h5", channels=["mScar"], with_phasor=["mScar"])
    b = _make_h5(tmp_path / "b.h5", channels=["mScar"], with_phasor=["mScar"])

    report = batch_rename_resource(
        [a, b], kind="channel", old_name="mScar", new_name="mScarlet",
    )

    assert len(report.items) == 2
    for item in report.items:
        assert item.status == "succeeded"
        assert item.processed == ("mScar",)
    for p in (a, b):
        assert _h5_has(p, "decay/mScarlet")
        assert not _h5_has(p, "decay/mScar")
        assert _h5_has(p, "phasor/mScarlet/g")
        with h5py.File(p, "r") as f:
            names = list(f["metadata"].attrs["channel_names"])
            assert names == ["mScarlet"]


def test_rename_mask_across_two_files(tmp_path):
    a = _make_h5(tmp_path / "a.h5", masks=["thresh_old"])
    b = _make_h5(tmp_path / "b.h5", masks=["thresh_old"])

    report = batch_rename_resource(
        [a, b], kind="mask", old_name="thresh_old", new_name="thresh_new",
    )

    for item in report.items:
        assert item.status == "succeeded"
    for p in (a, b):
        assert _h5_has(p, "masks/thresh_new")
        assert not _h5_has(p, "masks/thresh_old")


def test_rename_segmentation_across_two_files(tmp_path):
    a = _make_h5(tmp_path / "a.h5", segs=["cellpose_qc"])
    b = _make_h5(tmp_path / "b.h5", segs=["cellpose_qc"])

    report = batch_rename_resource(
        [a, b], kind="segmentation",
        old_name="cellpose_qc", new_name="cp_mask",
    )

    for item in report.items:
        assert item.status == "succeeded"
    for p in (a, b):
        assert _h5_has(p, "labels/cp_mask")
        assert not _h5_has(p, "labels/cellpose_qc")


# ── Progress callback ──────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset_in_order(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    seen: list[Path] = []
    batch_rename_resource(
        [a, b], kind="channel", old_name="ch0", new_name="ch1",
        progress_callback=lambda item: seen.append(item.h5_path),
    )
    assert seen == [a, b]


# ── Mixed-state batches ────────────────────────────────────────────────


def test_mixed_present_and_absent_yields_partial_batch(tmp_path):
    """A has the channel, B doesn't. A renamed, B skipped — both succeed in
    their own way and the batch keeps going."""
    a = _make_h5(tmp_path / "a.h5", channels=["mScar"])
    b = _make_h5(tmp_path / "b.h5", channels=["other"])

    report = batch_rename_resource(
        [a, b], kind="channel", old_name="mScar", new_name="mScarlet",
    )

    a_item, b_item = report.items
    assert a_item.status == "succeeded"
    assert a_item.processed == ("mScar",)
    assert b_item.status == "skipped_no_changes"
    assert "mScar" in b_item.skipped
    assert _h5_has(a, "decay/mScarlet")
    assert _h5_has(b, "decay/other")  # untouched


def test_target_name_collision_is_per_dataset_error(tmp_path):
    """rename_channel raises when /decay/<new> already exists. Recorded as a
    per-dataset error; the batch continues."""
    a = _make_h5(tmp_path / "a.h5", channels=["mScar", "mScarlet"])  # collision
    b = _make_h5(tmp_path / "b.h5", channels=["mScar"])  # clean

    report = batch_rename_resource(
        [a, b], kind="channel", old_name="mScar", new_name="mScarlet",
    )

    a_item, b_item = report.items
    assert a_item.status == "failed"
    assert "mScar" in a_item.errors
    # a.h5 unchanged on collision (rename_channel raised before mutating)
    assert _h5_has(a, "decay/mScar")
    assert _h5_has(a, "decay/mScarlet")
    assert b_item.status == "succeeded"
    assert _h5_has(b, "decay/mScarlet")


# ── Dry run ────────────────────────────────────────────────────────────


def test_dry_run_does_not_mutate_any_file(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["mScar"], with_phasor=["mScar"])
    b = _make_h5(tmp_path / "b.h5", channels=["other"])

    report = batch_rename_resource(
        [a, b], kind="channel", old_name="mScar", new_name="mScarlet",
        dry_run=True,
    )

    a_item, b_item = report.items
    assert a_item.status == "succeeded"
    assert a_item.processed == ("mScar",)
    assert b_item.status == "skipped_no_changes"

    # Nothing changed on disk.
    assert _h5_has(a, "decay/mScar")
    assert not _h5_has(a, "decay/mScarlet")
    assert _h5_has(a, "phasor/mScar/g")
    assert _h5_has(b, "decay/other")


# ── Error paths ────────────────────────────────────────────────────────


def test_missing_file_marked_failed_batch_continues(tmp_path):
    """Non-existent .h5 → 'failed' with open-failure error string; batch
    continues to the next path."""
    missing = tmp_path / "nope.h5"
    real = _make_h5(tmp_path / "real.h5", channels=["ch0"])

    report = batch_rename_resource(
        [missing, real], kind="channel", old_name="ch0", new_name="ch1",
    )

    miss_item, real_item = report.items
    assert miss_item.status == "failed"
    assert miss_item.error is not None and "open" in miss_item.error.lower()
    assert real_item.status == "succeeded"
    assert _h5_has(real, "decay/ch1")


def test_same_old_and_new_name_is_skipped(tmp_path):
    """rename_channel is an early-return no-op when old == new. The batch
    classifies the dataset as skipped (no work to do)."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])

    report = batch_rename_resource(
        [a], kind="channel", old_name="ch0", new_name="ch0",
    )

    assert report.items[0].status == "skipped_no_changes"
    assert _h5_has(a, "decay/ch0")


# ── Empty input ────────────────────────────────────────────────────────


def test_empty_input_list_returns_empty_report():
    report = batch_rename_resource(
        [], kind="channel", old_name="a", new_name="b",
    )
    assert isinstance(report, BatchOperationReport)
    assert report.items == ()
    assert report.total_succeeded == 0


# ── Input validation ───────────────────────────────────────────────────


def test_invalid_kind_raises_value_error(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"kind"):
        batch_rename_resource(
            [a], kind="bogus", old_name="ch0", new_name="ch1",  # type: ignore[arg-type]
        )
