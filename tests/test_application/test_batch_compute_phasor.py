"""Tests for the batch phasor + wavelet orchestrator.

The orchestrator is exercised against real HDF5 files (so existing-phasor
detection and calibration-metadata reads are observable) but
``ComputePhasor.execute`` and ``ApplyWavelet.execute`` themselves are
monkeypatched -- the goal is to verify orchestration shape, classification,
and view_bin caller-wiring, not re-test the compute/wavelet engines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from percell4.application.use_cases import batch_compute_phasor as bcp
from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    BatchPhasorReport,
    batch_compute_phasor,
)
from percell4.domain.flim.wavelet_filter import MAX_FILTER_LEVEL


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channels: list[str],
    with_calibration: bool = True,
    with_phasor_for: list[str] | None = None,
    missing_freq: bool = False,
) -> Path:
    """Create a minimal .h5 with /decay/<ch> groups + calibration metadata.

    ``with_phasor_for`` plants ``/phasor/<ch>/g`` for the given channels so
    the existing-phasor skip path can be exercised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        if with_calibration:
            if not missing_freq:
                meta.attrs["flim_frequency_mhz"] = 80.0
            for ch in channels:
                meta.attrs[f"flim_cal_phase_{ch}"] = 0.1
                meta.attrs[f"flim_cal_mod_{ch}"] = 0.9
        decay_grp = f.create_group("decay")
        for ch in channels:
            decay_grp.create_dataset(
                ch, data=np.zeros((4, 4, 8), dtype=np.float32)
            )
        if with_phasor_for:
            phasor_grp = f.create_group("phasor")
            for ch in with_phasor_for:
                ch_grp = phasor_grp.create_group(ch)
                ch_grp.create_dataset(
                    "g", data=np.zeros((4, 4), dtype=np.float32)
                )
                ch_grp.create_dataset(
                    "s", data=np.zeros((4, 4), dtype=np.float32)
                )
    return path


def _stub_use_cases(
    monkeypatch: pytest.MonkeyPatch,
    *,
    compute_raises: dict[str, Exception] | None = None,
    wavelet_raises: dict[str, Exception] | None = None,
) -> dict[str, list[tuple[str, dict]]]:
    """Replace ComputePhasor.execute and ApplyWavelet.execute with stubs
    that record (channel, kwargs) per call. Optional raises override
    behavior for specific channels."""
    compute_raises = compute_raises or {}
    wavelet_raises = wavelet_raises or {}
    calls: dict[str, list[tuple[str, dict]]] = {
        "compute": [], "wavelet": [],
    }

    def fake_compute(self, channel, harmonic=1, view_bin=1):  # noqa: ARG001
        calls["compute"].append(
            (channel, {"harmonic": harmonic, "view_bin": view_bin})
        )
        if channel in compute_raises:
            raise compute_raises[channel]
        return None

    def fake_wavelet(self, channel, filter_level=9, view_bin=1):  # noqa: ARG001
        calls["wavelet"].append(
            (channel, {"filter_level": filter_level, "view_bin": view_bin})
        )
        if channel in wavelet_raises:
            raise wavelet_raises[channel]
        return None

    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ComputePhasor.execute",
        fake_compute,
    )
    monkeypatch.setattr(
        "percell4.application.use_cases.batch_compute_phasor.ApplyWavelet.execute",
        fake_wavelet,
    )
    return calls


# ── Happy path ──────────────────────────────────────────────────────────


def test_two_datasets_two_channels_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean run: every channel of every dataset lands."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0", "ch1"])
    _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5_a, h5_b])

    assert len(report.items) == 2
    assert report.total_succeeded == 2
    assert report.total_failed == 0
    assert report.total_skipped == 0
    for item in report.items:
        assert item.status == "succeeded"
        assert set(item.processed) == {"ch0", "ch1"}
        assert item.skipped == {}
        assert item.errors == {}


# ── Skip on existing phasor ─────────────────────────────────────────────


def test_skip_existing_phasor_when_not_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Channels with /phasor/<ch>/g already on disk are skipped by default."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_phasor_for=["ch0"],  # ch0 already has phasor; ch1 doesn't
    )
    _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5])

    item = report.items[0]
    assert item.status == "partial"  # ch1 processed, ch0 skipped
    assert item.processed == ("ch1",)
    assert "ch0" in item.skipped
    assert "phasor exists" in item.skipped["ch0"]


def test_overwrite_flag_recomputes_existing_phasor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--overwrite forces recompute even when /phasor/<ch>/g exists."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_phasor_for=["ch0"],
    )
    calls = _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5], overwrite=True)

    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"ch0", "ch1"}
    assert item.skipped == {}
    # Both channels reached ComputePhasor.
    processed_channels = {c for c, _ in calls["compute"]}
    assert processed_channels == {"ch0", "ch1"}


# ── Skip on missing calibration ─────────────────────────────────────────


def test_skip_missing_per_channel_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If flim_cal_phase_<ch> is absent, that channel is skipped with a
    reason naming the missing key."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_calibration=True,
    )
    # Remove ch0's phase attr to simulate a partially-calibrated dataset.
    with h5py.File(h5, "a") as f:
        del f["metadata"].attrs["flim_cal_phase_ch0"]
    _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5])

    item = report.items[0]
    assert item.status == "partial"
    assert item.processed == ("ch1",)
    assert "ch0" in item.skipped
    assert "flim_cal_phase_ch0" in item.skipped["ch0"]


def test_skip_missing_frequency_skips_every_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """flim_frequency_mhz applies to all channels; missing it skips
    everything in the dataset."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        missing_freq=True,
    )
    _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5])

    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.processed == ()
    for ch in ("ch0", "ch1"):
        assert "flim_frequency_mhz" in item.skipped[ch]


# ── Per-channel error isolation ─────────────────────────────────────────


def test_per_channel_wavelet_error_yields_partial_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wavelet failure on one channel doesn't affect the others."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])
    _stub_use_cases(
        monkeypatch,
        wavelet_raises={"ch1": RuntimeError("DTCWT crashed")},
    )

    report = batch_compute_phasor([h5])

    item = report.items[0]
    assert item.status == "partial"
    assert item.processed == ("ch0",)
    assert "ch1" in item.errors
    assert "apply_wavelet" in item.errors["ch1"]
    assert "DTCWT crashed" in item.errors["ch1"]


def test_per_channel_compute_error_skips_wavelet_for_that_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ComputePhasor fails, ApplyWavelet is NOT called for that channel."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])
    calls = _stub_use_cases(
        monkeypatch,
        compute_raises={"ch0": RuntimeError("compute failed")},
    )

    report = batch_compute_phasor([h5])

    item = report.items[0]
    assert item.status == "partial"
    assert item.processed == ("ch1",)
    assert "compute_phasor" in item.errors["ch0"]
    # Wavelet was NOT called for ch0.
    wavelet_channels = {c for c, _ in calls["wavelet"]}
    assert "ch0" not in wavelet_channels
    assert "ch1" in wavelet_channels


# ── Dataset-level errors ────────────────────────────────────────────────


def test_nonexistent_h5_path_isolates_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing .h5 fails its own item but doesn't abort the batch."""
    h5_ok = _make_h5(tmp_path / "ok.h5", channels=["ch0"])
    h5_missing = tmp_path / "does_not_exist.h5"
    _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5_missing, h5_ok])

    assert report.items[0].status == "failed"
    assert report.items[0].error is not None
    assert report.items[1].status == "succeeded"


def test_empty_decay_group_yields_skipped_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dataset with no /decay/<ch> children is reported as skipped."""
    h5 = tmp_path / "no_decay.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")
    _stub_use_cases(monkeypatch)

    report = batch_compute_phasor([h5])

    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.processed == ()


# ── Progress callback ───────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback receives one BatchPhasorItemResult per input path,
    in order, after each dataset is fully classified."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    _stub_use_cases(monkeypatch)

    captured: list[BatchPhasorItemResult] = []
    batch_compute_phasor(
        [h5_a, h5_b], progress_callback=captured.append,
    )

    assert len(captured) == 2
    assert captured[0].h5_path == h5_a
    assert captured[1].h5_path == h5_b
    # Each captured result is fully classified by the time the callback
    # fires (not None / placeholder).
    for item in captured:
        assert item.status in {"succeeded", "partial", "failed", "skipped_no_changes"}


# ── view_bin caller-wiring (per U14 learning) ────────────────────────────


def test_compute_phasor_called_with_explicit_view_bin_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The U14 caller-wiring discipline: view_bin=1 must appear in every
    ComputePhasor.execute call, not be left to default."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])
    calls = _stub_use_cases(monkeypatch)

    batch_compute_phasor([h5])

    for channel, kwargs in calls["compute"]:
        assert kwargs["view_bin"] == 1, (
            f"compute_phasor for {channel} got view_bin={kwargs['view_bin']}, "
            "expected explicit 1"
        )


def test_apply_wavelet_called_with_explicit_view_bin_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same caller-wiring check for ApplyWavelet."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])
    calls = _stub_use_cases(monkeypatch)

    batch_compute_phasor([h5])

    for channel, kwargs in calls["wavelet"]:
        assert kwargs["view_bin"] == 1, (
            f"apply_wavelet for {channel} got view_bin={kwargs['view_bin']}, "
            "expected explicit 1"
        )


def test_apply_wavelet_receives_configured_filter_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filter_level argument flows through unchanged to every channel."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])
    calls = _stub_use_cases(monkeypatch)

    batch_compute_phasor([h5], filter_level=5)

    for _, kwargs in calls["wavelet"]:
        assert kwargs["filter_level"] == 5


# ── Input validation ────────────────────────────────────────────────────


def test_filter_level_out_of_range_raises(tmp_path: Path) -> None:
    """filter_level outside [1, MAX_FILTER_LEVEL] is rejected upfront."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"filter_level"):
        batch_compute_phasor([h5], filter_level=0)
    with pytest.raises(ValueError, match=r"filter_level"):
        batch_compute_phasor([h5], filter_level=MAX_FILTER_LEVEL + 1)


def test_filter_level_above_legacy_cap_is_accepted(tmp_path: Path) -> None:
    """A level above the old 9/15 caps (but within MAX_FILTER_LEVEL) passes
    validation and returns a report instead of raising."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    # 20 was rejected under the old [1, 9] cap; it must be accepted now.
    report = batch_compute_phasor([h5], filter_level=20)
    assert isinstance(report, BatchPhasorReport)
    assert MAX_FILTER_LEVEL >= 20


def test_empty_input_list_returns_empty_report() -> None:
    """An empty paths list returns an empty report (no error)."""
    report = batch_compute_phasor([])
    assert report.items == ()
    assert report.total_succeeded == 0
    assert report.total_failed == 0


# ── batch_remove_phasor ─────────────────────────────────────────────────


def _has_phasor_group(h5_path: Path, channel: str) -> bool:
    with h5py.File(h5_path, "r") as f:
        return f"phasor/{channel}" in f


def test_remove_deletes_phasor_group_for_every_channel_with_data(
    tmp_path: Path,
) -> None:
    """All channels with /phasor/<ch>/ on disk are deleted."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_phasor_for=["ch0", "ch1"],
    )

    report = bcp.batch_remove_phasor([h5])

    assert len(report.items) == 1
    item = report.items[0]
    assert item.status == "succeeded"
    assert set(item.processed) == {"ch0", "ch1"}
    assert item.skipped == {}
    assert item.errors == {}
    assert not _has_phasor_group(h5, "ch0")
    assert not _has_phasor_group(h5, "ch1")


def test_remove_channels_without_phasor_are_skipped_not_failed(
    tmp_path: Path,
) -> None:
    """Channels with no /phasor/<ch>/ on disk are reported as skipped."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_phasor_for=["ch0"],  # ch1 has no phasor
    )

    report = bcp.batch_remove_phasor([h5])

    item = report.items[0]
    assert item.status == "partial"
    assert item.processed == ("ch0",)
    assert "ch1" in item.skipped
    assert "no phasor" in item.skipped["ch1"].lower()


def test_remove_all_channels_with_no_phasor_is_skipped_no_changes(
    tmp_path: Path,
) -> None:
    """When nothing exists to delete, the dataset's status is skipped."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_phasor_for=None,
    )

    report = bcp.batch_remove_phasor([h5])

    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.processed == ()
    assert set(item.skipped.keys()) == {"ch0", "ch1"}


def test_remove_calls_progress_callback_per_dataset(tmp_path: Path) -> None:
    """progress_callback fires once per dataset, in order."""
    h5_a = _make_h5(
        tmp_path / "a.h5", channels=["ch0"], with_phasor_for=["ch0"],
    )
    h5_b = _make_h5(
        tmp_path / "b.h5", channels=["ch0"], with_phasor_for=["ch0"],
    )
    seen: list[Path] = []

    def cb(item: BatchPhasorItemResult) -> None:
        seen.append(item.h5_path)

    bcp.batch_remove_phasor([h5_a, h5_b], progress_callback=cb)
    assert seen == [h5_a, h5_b]


def test_remove_isolates_per_dataset_failures(
    tmp_path: Path,
) -> None:
    """A missing file fails one dataset without halting the batch."""
    real = _make_h5(
        tmp_path / "real.h5",
        channels=["ch0"],
        with_phasor_for=["ch0"],
    )
    missing = tmp_path / "nope.h5"

    report = bcp.batch_remove_phasor([missing, real])

    assert len(report.items) == 2
    assert report.items[0].status == "failed"
    assert "open failed" in (report.items[0].error or "")
    assert report.items[1].status == "succeeded"
    assert not _has_phasor_group(real, "ch0")


def test_remove_does_not_touch_decay_or_metadata(tmp_path: Path) -> None:
    """Deletion is scoped to /phasor/; /decay and /metadata are untouched."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0"],
        with_phasor_for=["ch0"],
    )

    bcp.batch_remove_phasor([h5])

    with h5py.File(h5, "r") as f:
        assert "decay/ch0" in f
        assert "metadata" in f
        assert "flim_frequency_mhz" in dict(f["metadata"].attrs)
        assert "phasor/ch0" not in f
        # /phasor parent group may or may not survive (h5py keeps empty
        # groups by default). We only require the targeted children gone.
