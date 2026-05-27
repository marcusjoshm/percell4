"""Tests for the batch delete orchestrator.

Symmetric to test_batch_rename_resource.py. Verifies orchestration
shape, per-dataset isolation, dry-run behavior, and the kind-specific
domain-method routing (delete_channel for channels, delete_item for
masks and segmentations).
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.application.use_cases.batch_delete_resource import (
    batch_delete_resource,
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
    masks: list[str] | None = None,
    segs: list[str] | None = None,
    with_phasor: list[str] | None = None,
) -> Path:
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


def test_delete_channel_across_two_files(tmp_path):
    """Both files lose /decay/ch0, /phasor/ch0/*, the channel_names entry,
    and the per-channel FLIM calibration attrs."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], with_phasor=["ch0"])
    b = _make_h5(tmp_path / "b.h5", channels=["ch0"], with_phasor=["ch0"])

    report = batch_delete_resource(
        [a, b], kind="channel", name="ch0",
    )

    for item in report.items:
        assert item.status == "succeeded"
        assert item.processed == ("ch0",)
    for p in (a, b):
        assert not _h5_has(p, "decay/ch0")
        assert not _h5_has(p, "phasor/ch0")
        with h5py.File(p, "r") as f:
            names = list(f["metadata"].attrs["channel_names"])
            assert names == []
            attrs = dict(f["metadata"].attrs)
            assert "flim_cal_phase_ch0" not in attrs
            assert "flim_cal_mod_ch0" not in attrs


def test_delete_mask_across_two_files(tmp_path):
    a = _make_h5(tmp_path / "a.h5", masks=["thresh_488"])
    b = _make_h5(tmp_path / "b.h5", masks=["thresh_488"])

    report = batch_delete_resource(
        [a, b], kind="mask", name="thresh_488",
    )

    for item in report.items:
        assert item.status == "succeeded"
    for p in (a, b):
        assert not _h5_has(p, "masks/thresh_488")


def test_delete_segmentation_across_two_files(tmp_path):
    a = _make_h5(tmp_path / "a.h5", segs=["cellpose_qc"])
    b = _make_h5(tmp_path / "b.h5", segs=["cellpose_qc"])

    report = batch_delete_resource(
        [a, b], kind="segmentation", name="cellpose_qc",
    )

    for item in report.items:
        assert item.status == "succeeded"
    for p in (a, b):
        assert not _h5_has(p, "labels/cellpose_qc")


# ── Mixed-state ─────────────────────────────────────────────────────────


def test_mixed_present_and_absent_yields_partial_batch(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    b = _make_h5(tmp_path / "b.h5", channels=["other"])

    report = batch_delete_resource(
        [a, b], kind="channel", name="ch0",
    )

    a_item, b_item = report.items
    assert a_item.status == "succeeded"
    assert a_item.processed == ("ch0",)
    assert b_item.status == "skipped_no_changes"
    assert not _h5_has(a, "decay/ch0")
    assert _h5_has(b, "decay/other")


# ── Channel-delete preserves unrelated channels ─────────────────────────


def test_channel_delete_leaves_other_channels_intact(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1"], with_phasor=["ch0", "ch1"])

    batch_delete_resource([a], kind="channel", name="ch0")

    assert _h5_has(a, "decay/ch1")
    assert _h5_has(a, "phasor/ch1/g")
    with h5py.File(a, "r") as f:
        names = list(f["metadata"].attrs["channel_names"])
        assert names == ["ch1"]
        attrs = dict(f["metadata"].attrs)
        assert "flim_cal_phase_ch1" in attrs
        assert "flim_cal_mod_ch1" in attrs


# ── Progress callback ──────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset_in_order(tmp_path):
    a = _make_h5(tmp_path / "a.h5", masks=["m"])
    b = _make_h5(tmp_path / "b.h5", masks=["m"])
    seen: list[Path] = []
    batch_delete_resource(
        [a, b], kind="mask", name="m",
        progress_callback=lambda item: seen.append(item.h5_path),
    )
    assert seen == [a, b]


# ── Dry run ────────────────────────────────────────────────────────────


def test_dry_run_does_not_mutate_any_file(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], with_phasor=["ch0"])

    report = batch_delete_resource(
        [a], kind="channel", name="ch0", dry_run=True,
    )

    assert report.items[0].status == "succeeded"
    assert report.items[0].processed == ("ch0",)
    # Nothing changed on disk.
    assert _h5_has(a, "decay/ch0")
    assert _h5_has(a, "phasor/ch0/g")
    with h5py.File(a, "r") as f:
        names = list(f["metadata"].attrs["channel_names"])
        assert names == ["ch0"]


def test_dry_run_skips_absent_resources(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["other"])

    report = batch_delete_resource(
        [a], kind="channel", name="ch0", dry_run=True,
    )

    assert report.items[0].status == "skipped_no_changes"


# ── Error paths ────────────────────────────────────────────────────────


def test_missing_file_marked_failed_batch_continues(tmp_path):
    missing = tmp_path / "nope.h5"
    real = _make_h5(tmp_path / "real.h5", channels=["ch0"])

    report = batch_delete_resource(
        [missing, real], kind="channel", name="ch0",
    )

    miss, real_item = report.items
    assert miss.status == "failed"
    assert miss.error is not None and "open" in miss.error.lower()
    assert real_item.status == "succeeded"
    assert not _h5_has(real, "decay/ch0")


# ── Empty input ────────────────────────────────────────────────────────


def test_empty_input_list_returns_empty_report():
    report = batch_delete_resource([], kind="channel", name="ch0")
    assert isinstance(report, BatchOperationReport)
    assert report.items == ()
    assert report.total_succeeded == 0


# ── Input validation ───────────────────────────────────────────────────


def test_invalid_kind_raises_value_error(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"kind"):
        batch_delete_resource(
            [a], kind="bogus", name="ch0",  # type: ignore[arg-type]
        )
