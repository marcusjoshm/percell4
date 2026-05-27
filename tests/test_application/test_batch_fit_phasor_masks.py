"""Tests for the batch phasor-mask fit orchestrator.

The orchestrator is exercised against real HDF5 files. Unlike
``test_batch_compute_phasor``, the underlying domain helper
(``fit_phasor_ellipse_and_apply_masks``) is NOT monkeypatched — the
goal is to verify end-to-end orchestration including the actual mask
writes, the cross-layer alignment rule (``intensity = decay.sum(axis=-1)``),
and the per-channel error isolation against real ellipse fits.

Phasor + decay arrays are synthesized in-place so the fits are
deterministic and the mask cardinality is predictable.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.application.use_cases import batch_fit_phasor_masks as bfpm
from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    BatchPhasorReport,
)
from percell4.application.use_cases.batch_fit_phasor_masks import (
    batch_fit_phasor_masks,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _build_decay_for_phasor(
    shape: tuple[int, int],
    *,
    total_intensity: float,
    n_bins: int = 8,
) -> np.ndarray:
    """Build a (H, W, n_bins) decay so ``decay.sum(axis=-1) == total_intensity``
    for every pixel. The decay shape itself doesn't drive the fit — the
    use case computes intensity from the decay sum and reads g/s from
    the pre-populated /phasor maps.
    """
    h, w = shape
    decay = np.full((h, w, n_bins), total_intensity / n_bins, dtype=np.float32)
    return decay


def _make_h5(
    path: Path,
    *,
    channels: list[str],
    shape: tuple[int, int] = (10, 10),
    decay_intensity: float = 100.0,
    g_centers: dict[str, float] | None = None,
    s_centers: dict[str, float] | None = None,
    g_sigma: float = 0.01,
    s_sigma: float = 0.01,
    with_phasor_for: list[str] | None = None,
    with_filtered_phasor_for: list[str] | None = None,
    with_decay_for: list[str] | None = None,
    with_calibration: bool = True,
    seed: int = 0,
) -> Path:
    """Create a minimal .h5 with /decay/<ch> and /phasor/<ch>/{g,s,...}.

    ``g_centers`` / ``s_centers`` (default 0.5 / 0.3 per channel) drive
    the synthesized Gaussian blob centers. ``with_phasor_for`` defaults
    to ``channels`` — opt-out by passing ``[]``. Same for ``with_decay_for``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    g_centers = g_centers or {ch: 0.5 for ch in channels}
    s_centers = s_centers or {ch: 0.3 for ch in channels}
    if with_phasor_for is None:
        with_phasor_for = list(channels)
    if with_filtered_phasor_for is None:
        with_filtered_phasor_for = []
    if with_decay_for is None:
        with_decay_for = list(channels)

    rng = np.random.default_rng(seed=seed)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channels
        if with_calibration:
            meta.attrs["flim_frequency_mhz"] = 80.0
            for ch in channels:
                meta.attrs[f"flim_cal_phase_{ch}"] = 0.0
                meta.attrs[f"flim_cal_mod_{ch}"] = 1.0

        decay_grp = f.create_group("decay")
        for ch in with_decay_for:
            decay = _build_decay_for_phasor(
                shape, total_intensity=decay_intensity,
            )
            decay_grp.create_dataset(ch, data=decay)

        if with_phasor_for or with_filtered_phasor_for:
            phasor_grp = f.create_group("phasor")
            for ch in set(with_phasor_for) | set(with_filtered_phasor_for):
                ch_grp = phasor_grp.create_group(ch)
                if ch in with_phasor_for:
                    g = rng.normal(
                        g_centers[ch], g_sigma, size=shape,
                    ).astype(np.float32)
                    s = rng.normal(
                        s_centers[ch], s_sigma, size=shape,
                    ).astype(np.float32)
                    ch_grp.create_dataset("g", data=g)
                    ch_grp.create_dataset("s", data=s)
                if ch in with_filtered_phasor_for:
                    g_f = rng.normal(
                        g_centers[ch], g_sigma, size=shape,
                    ).astype(np.float32)
                    s_f = rng.normal(
                        s_centers[ch], s_sigma, size=shape,
                    ).astype(np.float32)
                    ch_grp.create_dataset("g_filtered", data=g_f)
                    ch_grp.create_dataset("s_filtered", data=s_f)
    return path


def _mask_exists(h5_path: Path, name: str) -> bool:
    with h5py.File(h5_path, "r") as f:
        return f"masks/{name}" in f


def _read_mask(h5_path: Path, name: str) -> np.ndarray:
    with h5py.File(h5_path, "r") as f:
        return f[f"masks/{name}"][()]


# ── Happy path ──────────────────────────────────────────────────────────


def test_two_datasets_two_channels_succeed(tmp_path: Path) -> None:
    """Clean run: every channel of every dataset lands two masks."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0", "ch1"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0", "ch1"])

    report = batch_fit_phasor_masks(
        [h5_a, h5_b],
        channels=["ch0", "ch1"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    assert isinstance(report, BatchPhasorReport)
    assert len(report.items) == 2
    assert report.total_succeeded == 2
    assert report.total_partial == 0
    assert report.total_failed == 0
    for item in report.items:
        assert item.status == "succeeded"
        assert set(item.processed) == {"ch0", "ch1"}
        assert item.skipped == {}
        assert item.errors == {}

    for h5 in (h5_a, h5_b):
        for ch in ("ch0", "ch1"):
            assert _mask_exists(h5, f"{ch}_phasor_1")
            assert _mask_exists(h5, f"{ch}_phasor_2")


def test_uses_unfiltered_g_s_even_when_filtered_is_present(tmp_path: Path) -> None:
    """The manual recipe is explicit: unfiltered phasor for both fit AND
    masks. Even when ``g_filtered`` / ``s_filtered`` are on disk, the use
    case must read the unfiltered pair. Verified by making the filtered
    pair uniformly NaN (which would crash any fit) and the unfiltered
    pair well-formed — the call succeeds iff unfiltered was used."""
    h5 = tmp_path / "ds.h5"
    h5.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(h5, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = ["ch0"]
        meta.attrs["flim_frequency_mhz"] = 80.0
        meta.attrs["flim_cal_phase_ch0"] = 0.0
        meta.attrs["flim_cal_mod_ch0"] = 1.0
        decay_grp = f.create_group("decay")
        decay_grp.create_dataset(
            "ch0",
            data=_build_decay_for_phasor((10, 10), total_intensity=100.0),
        )
        ph = f.create_group("phasor")
        ch_grp = ph.create_group("ch0")
        # Unfiltered: well-formed Gaussian blob.
        rng = np.random.default_rng(seed=7)
        g_u = rng.normal(0.5, 0.01, size=(10, 10)).astype(np.float32)
        s_u = rng.normal(0.3, 0.01, size=(10, 10)).astype(np.float32)
        ch_grp.create_dataset("g", data=g_u)
        ch_grp.create_dataset("s", data=s_u)
        # Filtered: all NaN — would crash any fit that used it.
        nan_arr = np.full((10, 10), np.nan, dtype=np.float32)
        ch_grp.create_dataset("g_filtered", data=nan_arr)
        ch_grp.create_dataset("s_filtered", data=nan_arr)

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "succeeded", (
        f"expected unfiltered to be used, got errors={item.errors}"
    )
    assert item.processed == ("ch0",)


def test_reads_unfiltered_g_s_when_only_unfiltered_present(tmp_path: Path) -> None:
    """Only ``g`` / ``s`` on disk → use case reads them directly and
    produces masks. No fallback machinery: the unfiltered pair is the
    one and only source."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0"],
        with_filtered_phasor_for=[],  # explicit: no filtered phasor
    )

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "succeeded"
    assert item.processed == ("ch0",)
    assert _mask_exists(h5, "ch0_phasor_1")
    assert _mask_exists(h5, "ch0_phasor_2")


# ── Channel-level skips ─────────────────────────────────────────────────


def test_partial_only_one_requested_channel_present(tmp_path: Path) -> None:
    """Two channels requested, one is absent in the dataset → partial."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0", "ch1"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "partial"
    assert item.processed == ("ch0",)
    assert "ch1" in item.skipped


def test_skip_channel_with_metadata_but_no_decay(tmp_path: Path) -> None:
    """Channel name in metadata.channel_names but /decay/<ch> missing
    → channel-level skip with `"channel not present"` reason."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0", "ch1"],
        with_decay_for=["ch0"],  # ch1 missing decay
    )

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0", "ch1"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "partial"
    assert item.processed == ("ch0",)
    assert "ch1" in item.skipped


def test_all_channels_absent_yields_skipped_no_changes(tmp_path: Path) -> None:
    """When every requested channel is missing from the dataset, the item
    is `skipped_no_changes` (no errors, no processed)."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["other"])

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0", "ch1"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.processed == ()
    assert set(item.skipped.keys()) == {"ch0", "ch1"}


# ── Dataset-level errors ────────────────────────────────────────────────


def test_nonexistent_h5_path_isolates_as_failed(tmp_path: Path) -> None:
    """A missing .h5 fails its own item but doesn't abort the batch."""
    h5_ok = _make_h5(tmp_path / "ok.h5", channels=["ch0"])
    h5_missing = tmp_path / "does_not_exist.h5"

    report = batch_fit_phasor_masks(
        [h5_missing, h5_ok],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    assert len(report.items) == 2
    assert report.items[0].status == "failed"
    assert report.items[0].error is not None
    assert "open" in report.items[0].error
    assert report.items[1].status == "succeeded"


def test_existing_mask_is_silently_overwritten(tmp_path: Path) -> None:
    """Pre-existing /masks/<ch><suffix_a> is overwritten by the new fit."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    # Write a sentinel mask so we can detect overwrite.
    sentinel = np.full((10, 10), 0, dtype=np.uint8)
    sentinel[0, 0] = 1  # exactly one pixel hot
    with h5py.File(h5, "a") as f:
        masks_grp = f.create_group("masks")
        ds = masks_grp.create_dataset("ch0_phasor_1", data=sentinel)
        ds.attrs["dims"] = ["H", "W"]

    sentinel_sum_before = int(_read_mask(h5, "ch0_phasor_1").sum())
    assert sentinel_sum_before == 1

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    assert report.items[0].status == "succeeded"
    sum_after = int(_read_mask(h5, "ch0_phasor_1").sum())
    # The fit should produce a mask covering many more pixels than the
    # single-pixel sentinel.
    assert sum_after != sentinel_sum_before


def test_degenerate_phasor_routes_to_errors_no_mask_written(
    tmp_path: Path,
) -> None:
    """A single-pixel-above-t_fit phasor → U1 raises degenerate-fit;
    the channel lands in `errors`, no mask is written on disk for it,
    and OTHER channels in the same dataset still succeed → `partial`."""
    h5 = tmp_path / "ds.h5"
    h5.parent.mkdir(parents=True, exist_ok=True)
    shape = (10, 10)
    with h5py.File(h5, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = ["ch_degen", "ch_ok"]
        meta.attrs["flim_frequency_mhz"] = 80.0
        for ch in ("ch_degen", "ch_ok"):
            meta.attrs[f"flim_cal_phase_{ch}"] = 0.0
            meta.attrs[f"flim_cal_mod_{ch}"] = 1.0
        decay_grp = f.create_group("decay")
        # ch_degen: every-pixel decay sums to 0 except (3,3) which sums to 100.
        degen_decay = np.zeros((10, 10, 8), dtype=np.float32)
        degen_decay[3, 3, :] = 100.0 / 8
        decay_grp.create_dataset("ch_degen", data=degen_decay)
        # ch_ok: uniform 100 everywhere.
        decay_grp.create_dataset(
            "ch_ok",
            data=_build_decay_for_phasor(shape, total_intensity=100.0),
        )
        ph = f.create_group("phasor")
        # ch_degen phasor: NaN everywhere except (3,3) — single fit point.
        degen_grp = ph.create_group("ch_degen")
        nan_g = np.full(shape, np.nan, dtype=np.float32)
        nan_s = np.full(shape, np.nan, dtype=np.float32)
        nan_g[3, 3] = 0.5
        nan_s[3, 3] = 0.3
        degen_grp.create_dataset("g", data=nan_g)
        degen_grp.create_dataset("s", data=nan_s)
        # ch_ok phasor: well-formed.
        rng = np.random.default_rng(seed=11)
        ok_grp = ph.create_group("ch_ok")
        ok_grp.create_dataset(
            "g", data=rng.normal(0.5, 0.01, size=shape).astype(np.float32),
        )
        ok_grp.create_dataset(
            "s", data=rng.normal(0.3, 0.01, size=shape).astype(np.float32),
        )

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch_degen", "ch_ok"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "partial"
    assert "ch_degen" in item.errors
    assert "ch_ok" in item.processed
    # No mask was written for the degenerate channel.
    assert not _mask_exists(h5, "ch_degen_phasor_1")
    assert not _mask_exists(h5, "ch_degen_phasor_2")
    # The other channel got its pair.
    assert _mask_exists(h5, "ch_ok_phasor_1")
    assert _mask_exists(h5, "ch_ok_phasor_2")


def test_write_mask_failure_on_second_write_leaves_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``store.write_mask`` raises on the second mask write, the first
    is already on disk, the channel is in `errors`, and the dataset's
    status is `partial`."""
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0", "ch1"])

    # Patch DatasetStore.write_mask to raise specifically when writing
    # the _phasor_2 mask of ch0.
    from percell4.store import DatasetStore

    real_write_mask = DatasetStore.write_mask
    call_log: list[str] = []

    def patched_write_mask(self, name, array, attrs=None):
        call_log.append(name)
        if name == "ch0_phasor_2":
            raise RuntimeError("disk full")
        return real_write_mask(self, name, array, attrs=attrs)

    monkeypatch.setattr(DatasetStore, "write_mask", patched_write_mask)

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0", "ch1"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "partial"
    assert "ch0" in item.errors
    assert "write failed" in item.errors["ch0"]
    assert "ch0" not in item.processed
    assert "ch1" in item.processed
    # First mask of ch0 was written before the failure.
    assert _mask_exists(h5, "ch0_phasor_1")
    # Second mask of ch0 was NOT written.
    assert not _mask_exists(h5, "ch0_phasor_2")


# ── Cancel ──────────────────────────────────────────────────────────────


def test_cancel_check_breaks_loop_between_datasets(tmp_path: Path) -> None:
    """``cancel_check`` returning True after dataset 2 of 3 → report has
    items for datasets 1 and 2 only."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    h5_c = _make_h5(tmp_path / "c.h5", channels=["ch0"])

    seen_paths: list[Path] = []
    call_count = {"n": 0}

    def cancel():
        # Cancel before dataset 3 is opened — but only AFTER datasets 1, 2
        # have been processed. cancel_check is called at the top of each
        # loop iteration, so it fires before dataset 1 (returns False),
        # before dataset 2 (False), before dataset 3 (True).
        call_count["n"] += 1
        return call_count["n"] == 3

    def cb(item):
        seen_paths.append(item.h5_path)

    report = batch_fit_phasor_masks(
        [h5_a, h5_b, h5_c],
        channels=["ch0"],
        t_fit=10.0,
        t_mask_a=0.0,
        t_mask_b=5.0,
        suffix_a="_phasor_1",
        suffix_b="_phasor_2",
        cancel_check=cancel,
        progress_callback=cb,
    )

    assert len(report.items) == 2
    assert seen_paths == [h5_a, h5_b]


# ── Input validation ────────────────────────────────────────────────────


def test_empty_channels_raises(tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"channels"):
        batch_fit_phasor_masks(
            [h5],
            channels=(),
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="_phasor_2",
        )


def test_empty_suffix_a_raises(tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"suffix"):
        batch_fit_phasor_masks(
            [h5],
            channels=["ch0"],
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="", suffix_b="_phasor_2",
        )


def test_empty_suffix_b_raises(tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"suffix"):
        batch_fit_phasor_masks(
            [h5],
            channels=["ch0"],
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="",
        )


def test_identical_suffixes_raise(tmp_path: Path) -> None:
    h5 = _make_h5(tmp_path / "ds.h5", channels=["ch0"])
    with pytest.raises(ValueError, match=r"differ"):
        batch_fit_phasor_masks(
            [h5],
            channels=["ch0"],
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="_phasor_1",
        )


def test_input_validation_runs_before_any_io(tmp_path: Path) -> None:
    """Validation must raise before opening any .h5. A non-existent path
    in the list should not produce any failure item — validation
    short-circuits the whole call."""
    h5_missing = tmp_path / "does_not_exist.h5"
    with pytest.raises(ValueError):
        batch_fit_phasor_masks(
            [h5_missing],
            channels=(),
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="_phasor_2",
        )


# ── Progress callback ───────────────────────────────────────────────────


def test_progress_callback_fires_once_per_dataset_in_order(
    tmp_path: Path,
) -> None:
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    h5_c = _make_h5(tmp_path / "c.h5", channels=["ch0"])

    captured: list[BatchPhasorItemResult] = []
    batch_fit_phasor_masks(
        [h5_a, h5_b, h5_c],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        progress_callback=captured.append,
    )

    assert len(captured) == 3
    assert [c.h5_path for c in captured] == [h5_a, h5_b, h5_c]
    for item in captured:
        assert item.status in {
            "succeeded", "partial", "failed", "skipped_no_changes",
        }


# ── ensure_phasor toggle ────────────────────────────────────────────────


def test_ensure_phasor_true_computes_on_the_fly(tmp_path: Path) -> None:
    """When phasor maps are absent but ``ensure_phasor=True`` (default),
    the use case calls ComputePhasor + ApplyWavelet inline and proceeds
    to fit + mask. Verified by building a .h5 with /decay but no /phasor
    and asserting /phasor/<ch>/{g, s} land on disk after the run.

    The synthesized decay is constant across the spatial axes, which
    makes ComputePhasor produce a constant phasor (singular covariance)
    and the fit raises a degenerate-fit ValueError. That's still a
    successful exercise of the on-the-fly path — the channel goes to
    ``errors`` (not ``skipped``) and /phasor/<ch>/{g, s} are visibly
    on disk. This test verifies the path was taken, not the fit
    outcome (which would require non-trivial decay data).
    """
    # Build a varied decay: a Gaussian-ish blob in (G, S) emerges from
    # giving each spatial pixel a *different* exponential decay profile.
    h5 = tmp_path / "ds.h5"
    h5.parent.mkdir(parents=True, exist_ok=True)
    h, w, n_bins = 10, 10, 16
    rng = np.random.default_rng(seed=99)
    # Per-pixel lifetime drawn from a tight Gaussian, then converted to
    # a discrete exponential decay. Total intensity is ~100 per pixel.
    tau_per_pixel = rng.normal(3.0, 0.05, size=(h, w))
    decay = np.zeros((h, w, n_bins), dtype=np.float32)
    t = np.arange(n_bins, dtype=np.float64)
    for r in range(h):
        for c in range(w):
            curve = np.exp(-t / tau_per_pixel[r, c])
            decay[r, c, :] = (curve / curve.sum() * 100.0).astype(np.float32)

    with h5py.File(h5, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = ["ch0"]
        meta.attrs["flim_frequency_mhz"] = 80.0
        meta.attrs["flim_cal_phase_ch0"] = 0.0
        meta.attrs["flim_cal_mod_ch0"] = 1.0
        f.create_group("decay").create_dataset("ch0", data=decay)
    # Sanity: phasor truly absent before the run.
    with h5py.File(h5, "r") as f:
        assert "phasor" not in f

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        ensure_phasor=True,
    )

    item = report.items[0]
    # Whether the fit succeeded or raised a domain error depends on the
    # variance of the synthesized phasor cloud — the key claim is that
    # ensure_phasor=True did its job and computed /phasor/<ch>/{g, s}
    # before attempting the fit. We assert that side effect directly.
    with h5py.File(h5, "r") as f:
        assert "phasor/ch0/g" in f, (
            "ensure_phasor=True must compute /phasor/ch0/g on the fly"
        )
        assert "phasor/ch0/s" in f
    # The channel reached the fit — it's either processed or errored,
    # but it is NOT skipped with `"phasor not computed"`.
    assert "ch0" not in item.skipped
    # If the fit landed both masks, that's a stronger success signal.
    if item.processed == ("ch0",):
        assert _mask_exists(h5, "ch0_phasor_1")
        assert _mask_exists(h5, "ch0_phasor_2")


def test_ensure_phasor_false_skips_when_phasor_missing(tmp_path: Path) -> None:
    """With ``ensure_phasor=False``, a channel that lacks phasor maps is
    skipped with the canonical ``"phasor not computed"`` reason."""
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["ch0"],
        with_phasor_for=[],
        with_filtered_phasor_for=[],
    )

    report = batch_fit_phasor_masks(
        [h5],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        ensure_phasor=False,
    )

    item = report.items[0]
    assert item.status == "skipped_no_changes"
    assert item.processed == ()
    assert "ch0" in item.skipped
    assert "phasor not computed" in item.skipped["ch0"]


# ── Mask collision ──────────────────────────────────────────────────────


def test_mask_name_collides_with_existing_channel(tmp_path: Path) -> None:
    """If ``<channel><suffix_a>`` or ``<channel><suffix_b>`` matches an
    existing channel name in the dataset, that channel goes to `errors`
    with a collision message — other channels proceed normally."""
    # Dataset has channels [mNG, mNG_phasor_1]; running with suffix_a="_phasor_1"
    # collides on mNG → errors[mNG].
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels=["mNG", "mNG_phasor_1", "mOK"],
    )

    report = batch_fit_phasor_masks(
        [h5],
        channels=["mNG", "mOK"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
    )

    item = report.items[0]
    assert item.status == "partial"
    assert "mNG" in item.errors
    assert "collides" in item.errors["mNG"]
    assert "mNG_phasor_1" in item.errors["mNG"]
    assert "mOK" in item.processed
    # No mask was written for mNG.
    assert not _mask_exists(h5, "mNG_phasor_1")
    assert not _mask_exists(h5, "mNG_phasor_2")


# ── Shared-ROI (U2): roi_sources kwarg ─────────────────────────────────


def test_shared_roi_one_source_one_target(tmp_path: Path) -> None:
    """``roi_sources={target: source}``: both succeed; target's mask
    coverage using source's ROI differs materially from a self-fit
    because the two datasets have distinct phasor distributions."""
    h5_src = _make_h5(
        tmp_path / "src.h5",
        channels=["ch0"],
        g_centers={"ch0": 0.5}, s_centers={"ch0": 0.3},
        seed=11,
    )
    h5_tgt = _make_h5(
        tmp_path / "tgt.h5",
        channels=["ch0"],
        g_centers={"ch0": 0.7}, s_centers={"ch0": 0.4},
        seed=22,
    )

    # Run with shared ROI (target borrows source's fit).
    report_shared = batch_fit_phasor_masks(
        [h5_src, h5_tgt],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_tgt: h5_src},
    )

    assert len(report_shared.items) == 2
    for item in report_shared.items:
        assert item.status == "succeeded", item.errors
        assert item.processed == ("ch0",)
    # All four masks exist on disk.
    for h5 in (h5_src, h5_tgt):
        for suffix in ("_phasor_1", "_phasor_2"):
            assert _mask_exists(h5, f"ch0{suffix}")

    shared_target_mask = _read_mask(h5_tgt, "ch0_phasor_1")
    shared_target_sum = int(shared_target_mask.sum())

    # Rebuild target h5 fresh and run again WITHOUT shared ROI (self-fit).
    h5_tgt_alone = _make_h5(
        tmp_path / "tgt_alone.h5",
        channels=["ch0"],
        g_centers={"ch0": 0.7}, s_centers={"ch0": 0.4},
        seed=22,
    )
    report_self = batch_fit_phasor_masks(
        [h5_tgt_alone],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
    )
    assert report_self.items[0].status == "succeeded"
    self_target_mask = _read_mask(h5_tgt_alone, "ch0_phasor_1")
    self_target_sum = int(self_target_mask.sum())

    # Cache must actually be consulted: the source-ROI mask must differ
    # materially from what the target would produce self-fitting.
    assert shared_target_sum != self_target_sum, (
        "shared-ROI target mask is identical to self-fit; "
        "ROI cache was not consulted"
    )


def test_shared_roi_one_source_two_targets_fits_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``paths=[src, t1, t2]``, ``roi_sources={t1: src, t2: src}`` →
    ``fit_phasor_ellipse`` is called exactly once per channel (the
    source's); both targets and the source have masks on disk."""
    h5_src = _make_h5(tmp_path / "src.h5", channels=["ch0", "ch1"])
    h5_t1 = _make_h5(tmp_path / "t1.h5", channels=["ch0", "ch1"])
    h5_t2 = _make_h5(tmp_path / "t2.h5", channels=["ch0", "ch1"])

    from percell4.domain.segmentation import phasor_masks as pm_mod

    call_log: list[tuple[float, float]] = []
    real_fit = pm_mod.fit_phasor_ellipse

    def counting_fit(g_map, s_map, intensity_map, *, t_fit):
        call_log.append((float(np.nanmean(g_map)), float(np.nanmean(s_map))))
        return real_fit(g_map, s_map, intensity_map, t_fit=t_fit)

    monkeypatch.setattr(bfpm, "fit_phasor_ellipse", counting_fit)

    report = batch_fit_phasor_masks(
        [h5_src, h5_t1, h5_t2],
        channels=["ch0", "ch1"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_t1: h5_src, h5_t2: h5_src},
    )

    assert len(report.items) == 3
    for item in report.items:
        assert item.status == "succeeded", item.errors
    # Exactly N_channels fit calls (one per channel of the source).
    assert len(call_log) == 2

    # All three datasets carry their masks.
    for h5 in (h5_src, h5_t1, h5_t2):
        for ch in ("ch0", "ch1"):
            assert _mask_exists(h5, f"{ch}_phasor_1")
            assert _mask_exists(h5, f"{ch}_phasor_2")


def test_shared_roi_mixed_batch_interleaved_order(tmp_path: Path) -> None:
    """``paths=[s1, t1, s2, t2]``, ``roi_sources={t1: s1, t2: s2}`` →
    progress callback fires in interleaved order [s1, t1, s2, t2]
    (NOT [s1, s2, t1, t2])."""
    h5_s1 = _make_h5(tmp_path / "s1.h5", channels=["ch0"])
    h5_t1 = _make_h5(tmp_path / "t1.h5", channels=["ch0"])
    h5_s2 = _make_h5(tmp_path / "s2.h5", channels=["ch0"])
    h5_t2 = _make_h5(tmp_path / "t2.h5", channels=["ch0"])

    seen: list[Path] = []
    batch_fit_phasor_masks(
        [h5_s1, h5_t1, h5_s2, h5_t2],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_t1: h5_s1, h5_t2: h5_s2},
        progress_callback=lambda item: seen.append(item.h5_path),
    )

    assert seen == [h5_s1, h5_t1, h5_s2, h5_t2]


def test_shared_roi_per_source_channel_partial(tmp_path: Path) -> None:
    """``roi_sources={target: source}``, channels=[mNG, Halo]. Source's
    mNG fits cleanly; source's Halo is degenerate. Expected:
      * Source: partial, processed=(mNG,), errors={Halo: degenerate ...}.
      * Target: partial, processed=(mNG,), errors={Halo: <ROI-source-failed>}.
    Target's mNG masks exist; target's Halo masks do not."""
    shape = (10, 10)

    def _build_mixed_h5(path: Path, seed: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(seed=seed)
        with h5py.File(path, "w") as f:
            meta = f.create_group("metadata")
            meta.attrs["channel_names"] = ["mNG", "Halo"]
            meta.attrs["flim_frequency_mhz"] = 80.0
            for ch in ("mNG", "Halo"):
                meta.attrs[f"flim_cal_phase_{ch}"] = 0.0
                meta.attrs[f"flim_cal_mod_{ch}"] = 1.0
            decay_grp = f.create_group("decay")
            # mNG: well-formed (uniform intensity 100).
            decay_grp.create_dataset(
                "mNG",
                data=_build_decay_for_phasor(shape, total_intensity=100.0),
            )
            # Halo: degenerate — only one pixel has intensity above t_fit.
            halo_decay = np.zeros((10, 10, 8), dtype=np.float32)
            halo_decay[3, 3, :] = 100.0 / 8
            decay_grp.create_dataset("Halo", data=halo_decay)
            ph = f.create_group("phasor")
            # mNG: well-formed Gaussian phasor blob.
            mng_grp = ph.create_group("mNG")
            mng_grp.create_dataset(
                "g",
                data=rng.normal(0.5, 0.01, size=shape).astype(np.float32),
            )
            mng_grp.create_dataset(
                "s",
                data=rng.normal(0.3, 0.01, size=shape).astype(np.float32),
            )
            # Halo: NaN everywhere except (3,3) — single-pixel fit subset.
            halo_grp = ph.create_group("Halo")
            nan_g = np.full(shape, np.nan, dtype=np.float32)
            nan_s = np.full(shape, np.nan, dtype=np.float32)
            nan_g[3, 3] = 0.5
            nan_s[3, 3] = 0.3
            halo_grp.create_dataset("g", data=nan_g)
            halo_grp.create_dataset("s", data=nan_s)
        return path

    h5_src = _build_mixed_h5(tmp_path / "src.h5", seed=33)
    h5_tgt = _build_mixed_h5(tmp_path / "tgt.h5", seed=44)

    report = batch_fit_phasor_masks(
        [h5_src, h5_tgt],
        channels=["mNG", "Halo"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_tgt: h5_src},
    )

    assert len(report.items) == 2
    src_item, tgt_item = report.items[0], report.items[1]

    # Source: partial — mNG processed, Halo errored (degenerate).
    assert src_item.h5_path == h5_src
    assert src_item.status == "partial"
    assert src_item.processed == ("mNG",)
    assert "Halo" in src_item.errors
    assert "degenerate" in src_item.errors["Halo"]

    # Target: partial — mNG processed (cache hit), Halo errored (cache miss).
    assert tgt_item.h5_path == h5_tgt
    assert tgt_item.status == "partial"
    assert tgt_item.processed == ("mNG",)
    assert "Halo" in tgt_item.errors
    # Source-failed message mentions the source path.
    assert "src.h5" in tgt_item.errors["Halo"]
    assert "ROI source" in tgt_item.errors["Halo"]

    # Disk state: target's mNG masks exist; target's Halo masks do NOT.
    assert _mask_exists(h5_tgt, "mNG_phasor_1")
    assert _mask_exists(h5_tgt, "mNG_phasor_2")
    assert not _mask_exists(h5_tgt, "Halo_phasor_1")
    assert not _mask_exists(h5_tgt, "Halo_phasor_2")


def test_shared_roi_source_fit_fails_on_only_channel(tmp_path: Path) -> None:
    """Source's only requested channel degenerate → source ``failed``;
    target also ``failed`` because every channel falls through to the
    cache-miss path."""
    shape = (10, 10)

    def _build_degen_h5(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(path, "w") as f:
            meta = f.create_group("metadata")
            meta.attrs["channel_names"] = ["mNG"]
            meta.attrs["flim_frequency_mhz"] = 80.0
            meta.attrs["flim_cal_phase_mNG"] = 0.0
            meta.attrs["flim_cal_mod_mNG"] = 1.0
            decay_grp = f.create_group("decay")
            # Single pixel above t_fit → degenerate.
            degen_decay = np.zeros((10, 10, 8), dtype=np.float32)
            degen_decay[3, 3, :] = 100.0 / 8
            decay_grp.create_dataset("mNG", data=degen_decay)
            ph = f.create_group("phasor")
            mng_grp = ph.create_group("mNG")
            nan_g = np.full(shape, np.nan, dtype=np.float32)
            nan_s = np.full(shape, np.nan, dtype=np.float32)
            nan_g[3, 3] = 0.5
            nan_s[3, 3] = 0.3
            mng_grp.create_dataset("g", data=nan_g)
            mng_grp.create_dataset("s", data=nan_s)
        return path

    h5_src = _build_degen_h5(tmp_path / "src.h5")
    h5_tgt = _build_degen_h5(tmp_path / "tgt.h5")

    report = batch_fit_phasor_masks(
        [h5_src, h5_tgt],
        channels=["mNG"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_tgt: h5_src},
    )

    src_item, tgt_item = report.items[0], report.items[1]
    # Source: zero processed → failed.
    assert src_item.status == "failed"
    assert src_item.processed == ()
    assert "mNG" in src_item.errors
    # Target: zero processed → failed; mNG errors with source-failed message.
    assert tgt_item.status == "failed"
    assert tgt_item.processed == ()
    assert "mNG" in tgt_item.errors
    assert "src.h5" in tgt_item.errors["mNG"]


def test_shared_roi_source_not_in_paths_raises(tmp_path: Path) -> None:
    """A SOURCE not in ``paths`` → ``ValueError`` before any I/O. Error
    names the missing source path."""
    h5_tgt = _make_h5(tmp_path / "tgt.h5", channels=["ch0"])
    h5_missing = tmp_path / "missing.h5"  # not created, not in paths

    with pytest.raises(ValueError, match=r"missing\.h5"):
        batch_fit_phasor_masks(
            [h5_tgt],
            channels=["ch0"],
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="_phasor_2",
            roi_sources={h5_tgt: h5_missing},
        )

    # No mask was written — validation short-circuited the call.
    assert not _mask_exists(h5_tgt, "ch0_phasor_1")


def test_shared_roi_target_not_in_paths_raises(tmp_path: Path) -> None:
    """A TARGET key not in ``paths`` → ``ValueError`` naming the missing
    target path."""
    h5_src = _make_h5(tmp_path / "src.h5", channels=["ch0"])
    h5_missing = tmp_path / "ghost_target.h5"  # not in paths

    with pytest.raises(ValueError, match=r"ghost_target\.h5"):
        batch_fit_phasor_masks(
            [h5_src],
            channels=["ch0"],
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="_phasor_2",
            roi_sources={h5_missing: h5_src},
        )


def test_shared_roi_chain_rejected(tmp_path: Path) -> None:
    """``{a: b, b: c}``: ``b`` is both a target and a source → chain
    detection raises ``ValueError``."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0"])
    h5_b = _make_h5(tmp_path / "b.h5", channels=["ch0"])
    h5_c = _make_h5(tmp_path / "c.h5", channels=["ch0"])

    with pytest.raises(ValueError, match=r"chain"):
        batch_fit_phasor_masks(
            [h5_a, h5_b, h5_c],
            channels=["ch0"],
            t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
            suffix_a="_phasor_1", suffix_b="_phasor_2",
            roi_sources={h5_a: h5_b, h5_b: h5_c},
        )


def test_shared_roi_self_reference_normalized_to_self_fit(
    tmp_path: Path,
) -> None:
    """``roi_sources={a: a}`` → silently treated as ``{a: None}``
    (self-fitting). No ``ValueError``; the dataset processes normally."""
    h5_a = _make_h5(tmp_path / "a.h5", channels=["ch0"])

    report = batch_fit_phasor_masks(
        [h5_a],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_a: h5_a},
    )

    assert len(report.items) == 1
    assert report.items[0].status == "succeeded"
    assert report.items[0].processed == ("ch0",)
    assert _mask_exists(h5_a, "ch0_phasor_1")
    assert _mask_exists(h5_a, "ch0_phasor_2")


def test_shared_roi_cancel_mid_source_group(tmp_path: Path) -> None:
    """``paths=[s1, t1, s2, t2]``, ``roi_sources={t1: s1, t2: s2}``.
    cancel_check returns True after t1 has been processed; the loop
    breaks before s2 starts. Report contains items for [s1, t1] only."""
    h5_s1 = _make_h5(tmp_path / "s1.h5", channels=["ch0"])
    h5_t1 = _make_h5(tmp_path / "t1.h5", channels=["ch0"])
    h5_s2 = _make_h5(tmp_path / "s2.h5", channels=["ch0"])
    h5_t2 = _make_h5(tmp_path / "t2.h5", channels=["ch0"])

    seen: list[Path] = []

    def cb(item):
        seen.append(item.h5_path)

    cancel_state = {"fire": False}

    def cancel():
        return cancel_state["fire"]

    def cb_with_cancel(item):
        seen.append(item.h5_path)
        if item.h5_path == h5_t1:
            cancel_state["fire"] = True

    report = batch_fit_phasor_masks(
        [h5_s1, h5_t1, h5_s2, h5_t2],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_t1: h5_s1, h5_t2: h5_s2},
        progress_callback=cb_with_cancel,
        cancel_check=cancel,
    )

    # Only s1 and t1 made it.
    assert seen == [h5_s1, h5_t1]
    assert [item.h5_path for item in report.items] == [h5_s1, h5_t1]
    # s2 and t2 did NOT produce masks.
    assert not _mask_exists(h5_s2, "ch0_phasor_1")
    assert not _mask_exists(h5_t2, "ch0_phasor_1")


def test_shared_roi_cancel_between_source_groups(tmp_path: Path) -> None:
    """Cancel fires after both s1 and t1 are done, before s2 starts.
    Report contains [s1, t1]."""
    h5_s1 = _make_h5(tmp_path / "s1.h5", channels=["ch0"])
    h5_t1 = _make_h5(tmp_path / "t1.h5", channels=["ch0"])
    h5_s2 = _make_h5(tmp_path / "s2.h5", channels=["ch0"])
    h5_t2 = _make_h5(tmp_path / "t2.h5", channels=["ch0"])

    cancel_after_t1 = {"flag": False}

    def cb(item):
        if item.h5_path == h5_t1:
            cancel_after_t1["flag"] = True

    def cancel():
        return cancel_after_t1["flag"]

    report = batch_fit_phasor_masks(
        [h5_s1, h5_t1, h5_s2, h5_t2],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_t1: h5_s1, h5_t2: h5_s2},
        progress_callback=cb,
        cancel_check=cancel,
    )

    assert [item.h5_path for item in report.items] == [h5_s1, h5_t1]
    assert not _mask_exists(h5_s2, "ch0_phasor_1")
    assert not _mask_exists(h5_t2, "ch0_phasor_1")


def test_shared_roi_path_normalization(tmp_path: Path) -> None:
    """``paths=[Path('a.h5')]`` (relative-ish) and a ``roi_sources``
    key whose form differs (resolved absolute) but resolves to the same
    canonical path → no ``ValueError`` for target-not-in-paths; the
    dataset processes as self-fitting."""
    h5_a_rel = _make_h5(tmp_path / "a.h5", channels=["ch0"])

    # Construct an alternative form pointing at the same file. Path.resolve()
    # collapses both to the same canonical absolute path.
    h5_a_resolved = h5_a_rel.resolve()
    assert h5_a_resolved == h5_a_rel.resolve()

    # roi_sources uses self-reference (which after normalization collapses
    # to None). The key path is `h5_a_resolved` while the input list has
    # `h5_a_rel` — both must resolve to the same canonical path so this
    # call should succeed, not raise.
    report = batch_fit_phasor_masks(
        [h5_a_rel],
        channels=["ch0"],
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_a_resolved: h5_a_resolved},
    )

    assert len(report.items) == 1
    assert report.items[0].status == "succeeded"


def test_shared_roi_channels_frozen_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fit_phasor_ellipse`` is called with the channels that were
    passed at call time and only those — the use case does not re-read
    or mutate its ``channels`` argument mid-run."""
    h5_src = _make_h5(tmp_path / "src.h5", channels=["mNG", "Halo"])
    h5_tgt = _make_h5(tmp_path / "tgt.h5", channels=["mNG", "Halo"])

    fit_calls: list[str] = []
    from percell4.domain.segmentation import phasor_masks as pm_mod

    real_fit = pm_mod.fit_phasor_ellipse

    # We cannot directly get the channel from the kwargs/args of the
    # raw fit call. Instead patch the use-case-side helper that resolves
    # which channel is currently being processed: wrap the read_array
    # accessor on DatasetStore to log channel reads, OR patch the fit
    # function and observe its argument intensities (which are unique
    # per-channel in this fixture). Simpler: monkeypatch
    # ``apply_ellipse_masks`` and observe the call sequence — but the
    # cleanest pin is to wrap ``fit_phasor_ellipse`` and assert it was
    # only ever called on the source for the channels we requested.

    requested_channels = ["mNG"]

    def watched_fit(g_map, s_map, intensity_map, *, t_fit):
        # Distinguish channels by mean of g_map — the helper builds
        # different phasor distributions in _make_h5 only when given
        # custom centers, but the default values are the same per channel.
        # So we just record that a fit happened.
        fit_calls.append("fit")
        return real_fit(g_map, s_map, intensity_map, t_fit=t_fit)

    monkeypatch.setattr(bfpm, "fit_phasor_ellipse", watched_fit)

    batch_fit_phasor_masks(
        [h5_src, h5_tgt],
        channels=requested_channels,
        t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0,
        suffix_a="_phasor_1", suffix_b="_phasor_2",
        roi_sources={h5_tgt: h5_src},
    )

    # Only mNG was requested → exactly one fit call (the source's mNG).
    # If the use case "re-read" channels mid-run and added Halo, this
    # would be 2.
    assert len(fit_calls) == 1
