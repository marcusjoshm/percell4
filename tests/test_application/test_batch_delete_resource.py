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
    with_wavelet: list[str] | None = None,
    wavelet_no_lifetime: list[str] | None = None,
) -> Path:
    """Build a minimal .h5.

    ``with_phasor`` channels get base ``/phasor/<ch>/{g,s}``. Channels also
    listed in ``with_wavelet`` get the full filtered triple
    ``{g_filtered, s_filtered, lifetime_filtered}``; channels in
    ``wavelet_no_lifetime`` get only ``{g_filtered, s_filtered}`` (the
    frequency-absent case readers tolerate).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with_wavelet = with_wavelet or []
    wavelet_no_lifetime = wavelet_no_lifetime or []
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
                g.create_dataset("s", data=np.zeros((4, 4), dtype=np.float32))
                if ch in with_wavelet or ch in wavelet_no_lifetime:
                    g.create_dataset(
                        "g_filtered", data=np.zeros((4, 4), dtype=np.float32)
                    )
                    g.create_dataset(
                        "s_filtered", data=np.zeros((4, 4), dtype=np.float32)
                    )
                if ch in with_wavelet:
                    g.create_dataset(
                        "lifetime_filtered", data=np.zeros((4, 4), dtype=np.float32)
                    )
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


def test_neither_name_nor_all_raises_value_error(tmp_path):
    """Caller must specify exactly one of ``name`` or ``all_resources``."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"name.*all_resources|all_resources.*name"):
        batch_delete_resource([a], kind="channel")  # type: ignore[call-arg]


def test_both_name_and_all_raises_value_error(tmp_path):
    """Both ``name`` and ``all_resources`` together is rejected."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"name.*all_resources|all_resources.*name"):
        batch_delete_resource(
            [a], kind="channel", name="ch0", all_resources=True,
        )


# ── --all behavior ──────────────────────────────────────────────────────


def test_all_channels_deletes_every_channel(tmp_path):
    """``all_resources=True`` for channels removes every entry in
    ``channel_names`` plus its ``/decay/<name>`` and per-channel FLIM
    calibration attrs."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1", "ch2"])

    report = batch_delete_resource([a], kind="channel", all_resources=True)

    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"ch0", "ch1", "ch2"}
    with h5py.File(a, "r") as f:
        assert list(f["metadata"].attrs["channel_names"]) == []
        assert "decay/ch0" not in f
        assert "decay/ch1" not in f
        assert "decay/ch2" not in f
        attrs = dict(f["metadata"].attrs)
        for ch in ("ch0", "ch1", "ch2"):
            assert f"flim_cal_phase_{ch}" not in attrs
            assert f"flim_cal_mod_{ch}" not in attrs


def test_all_masks_deletes_every_mask(tmp_path):
    a = _make_h5(tmp_path / "a.h5", masks=["m0", "m1", "m2"])

    report = batch_delete_resource([a], kind="mask", all_resources=True)

    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"m0", "m1", "m2"}
    with h5py.File(a, "r") as f:
        assert "masks/m0" not in f
        assert "masks/m1" not in f
        assert "masks/m2" not in f


def test_all_segmentations_deletes_every_label(tmp_path):
    a = _make_h5(tmp_path / "a.h5", segs=["cp_mask", "manual_qc"])

    report = batch_delete_resource(
        [a], kind="segmentation", all_resources=True,
    )

    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"cp_mask", "manual_qc"}
    with h5py.File(a, "r") as f:
        assert "labels/cp_mask" not in f
        assert "labels/manual_qc" not in f


def test_all_on_dataset_with_no_resources_is_skipped(tmp_path):
    """A dataset with zero resources of the given kind → skipped, not failed."""
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"])  # no masks

    report = batch_delete_resource([a], kind="mask", all_resources=True)

    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.processed == ()


def test_all_dry_run_does_not_mutate(tmp_path):
    a = _make_h5(tmp_path / "a.h5", masks=["m0", "m1"])

    report = batch_delete_resource(
        [a], kind="mask", all_resources=True, dry_run=True,
    )

    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"m0", "m1"}
    # On-disk state unchanged.
    with h5py.File(a, "r") as f:
        assert "masks/m0" in f
        assert "masks/m1" in f


def test_all_across_two_files_aggregates(tmp_path):
    a = _make_h5(tmp_path / "a.h5", masks=["m0", "m1"])
    b = _make_h5(tmp_path / "b.h5", masks=["other"])

    report = batch_delete_resource(
        [a, b], kind="mask", all_resources=True,
    )

    a_item, b_item = report.items
    assert a_item.status == "succeeded"
    assert set(a_item.processed) == {"m0", "m1"}
    assert b_item.status == "succeeded"
    assert b_item.processed == ("other",)


# ── FLIM phasor / wavelet kinds ─────────────────────────────────────────


def test_delete_phasor_removes_whole_group_keeps_decay(tmp_path):
    """`--kind phasor` drops the entire /phasor/<ch> group (base + wavelet)
    but leaves /decay, channel_names, and FLIM cal attrs untouched."""
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["mNG"],
        with_phasor=["mNG"],
        with_wavelet=["mNG"],
    )

    report = batch_delete_resource([a], kind="phasor", name="mNG")

    item = report.items[0]
    assert item.status == "succeeded"
    assert item.processed == ("mNG",)
    assert not _h5_has(a, "phasor/mNG")
    # The channel itself survives — phasor is recomputable from decay.
    assert _h5_has(a, "decay/mNG")
    with h5py.File(a, "r") as f:
        assert list(f["metadata"].attrs["channel_names"]) == ["mNG"]
        attrs = dict(f["metadata"].attrs)
        assert "flim_cal_phase_mNG" in attrs
        assert "flim_cal_mod_mNG" in attrs


def test_delete_wavelet_removes_triple_keeps_base_phasor(tmp_path):
    """`--kind wavelet` drops only g_filtered/s_filtered/lifetime_filtered;
    base g/s remain."""
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["mNG"],
        with_phasor=["mNG"],
        with_wavelet=["mNG"],
    )

    report = batch_delete_resource([a], kind="wavelet", name="mNG")

    item = report.items[0]
    assert item.status == "succeeded"
    assert item.processed == ("mNG",)
    assert not _h5_has(a, "phasor/mNG/g_filtered")
    assert not _h5_has(a, "phasor/mNG/s_filtered")
    assert not _h5_has(a, "phasor/mNG/lifetime_filtered")
    # Base phasor preserved.
    assert _h5_has(a, "phasor/mNG/g")
    assert _h5_has(a, "phasor/mNG/s")


def test_delete_wavelet_partial_triple_no_lifetime(tmp_path):
    """A channel with only g_filtered + s_filtered (no lifetime) still
    counts as present and is swept cleanly."""
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["mNG"],
        with_phasor=["mNG"],
        wavelet_no_lifetime=["mNG"],
    )

    report = batch_delete_resource([a], kind="wavelet", name="mNG")

    assert report.items[0].status == "succeeded"
    assert not _h5_has(a, "phasor/mNG/g_filtered")
    assert not _h5_has(a, "phasor/mNG/s_filtered")
    assert _h5_has(a, "phasor/mNG/g")


def test_delete_wavelet_absent_when_only_base_is_skipped(tmp_path):
    """Base phasor present but no wavelet triple → skipped, base untouched."""
    a = _make_h5(tmp_path / "a.h5", channels=["mNG"], with_phasor=["mNG"])

    report = batch_delete_resource([a], kind="wavelet", name="mNG")

    assert report.items[0].status == "skipped_no_changes"
    assert _h5_has(a, "phasor/mNG/g")


def test_delete_phasor_absent_is_skipped(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["mNG"])  # no /phasor

    report = batch_delete_resource([a], kind="phasor", name="mNG")

    assert report.items[0].status == "skipped_no_changes"


def test_delete_phasor_leaves_other_channel_phasor_intact(tmp_path):
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["ch0", "ch1"],
        with_phasor=["ch0", "ch1"],
        with_wavelet=["ch0", "ch1"],
    )

    batch_delete_resource([a], kind="phasor", name="ch0")

    assert not _h5_has(a, "phasor/ch0")
    assert _h5_has(a, "phasor/ch1/g")
    assert _h5_has(a, "phasor/ch1/g_filtered")


def test_all_phasor_deletes_every_phasor_channel(tmp_path):
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["ch0", "ch1"],
        with_phasor=["ch0", "ch1"],
        with_wavelet=["ch0"],
    )

    report = batch_delete_resource([a], kind="phasor", all_resources=True)

    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"ch0", "ch1"}
    assert not _h5_has(a, "phasor/ch0")
    assert not _h5_has(a, "phasor/ch1")
    # Channels remain, phasor recomputable.
    assert _h5_has(a, "decay/ch0")
    assert _h5_has(a, "decay/ch1")


def test_all_wavelet_only_enumerates_channels_with_triple(tmp_path):
    """`--kind wavelet --all` processes only channels that actually carry a
    wavelet triple; base-only channels are not enumerated."""
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["ch0", "ch1"],
        with_phasor=["ch0", "ch1"],
        with_wavelet=["ch0"],  # ch1 has base phasor only
    )

    report = batch_delete_resource([a], kind="wavelet", all_resources=True)

    item = report.items[0]
    assert item.status == "succeeded"
    assert item.processed == ("ch0",)
    assert not _h5_has(a, "phasor/ch0/g_filtered")
    assert _h5_has(a, "phasor/ch0/g")  # base kept
    assert _h5_has(a, "phasor/ch1/g")  # untouched


def test_all_wavelet_no_triples_present_is_skipped(tmp_path):
    a = _make_h5(tmp_path / "a.h5", channels=["ch0"], with_phasor=["ch0"])

    report = batch_delete_resource([a], kind="wavelet", all_resources=True)

    assert report.items[0].status == "skipped_no_changes"
    assert _h5_has(a, "phasor/ch0/g")


def test_delete_phasor_dry_run_does_not_mutate(tmp_path):
    a = _make_h5(
        tmp_path / "a.h5",
        channels=["mNG"],
        with_phasor=["mNG"],
        with_wavelet=["mNG"],
    )

    report = batch_delete_resource(
        [a], kind="phasor", name="mNG", dry_run=True,
    )

    assert report.items[0].status == "succeeded"
    assert _h5_has(a, "phasor/mNG/g")
    assert _h5_has(a, "phasor/mNG/g_filtered")
