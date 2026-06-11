"""Tests for the Qt-free window×k sweep harness (plan U2)."""

from __future__ import annotations

import h5py
import numpy as np
from skimage.draw import disk

from percell4.domain.measure.adaptive_clip import auto_window
from percell4.store import DatasetStore
from percell4.workflows import window_k_sweep as wks
from percell4.workflows.window_k_sweep import (
    DEFAULT_KS,
    DEFAULT_WINDOWS,
    FixedSettings,
    SweepReport,
    SweepRow,
    base_settings,
    mask_name,
    parse_mask_name,
    run_sweep,
)


def _granule_image(centers, radius, *, dilute=50.0, fg=220.0, shape=(160, 160), seed=0):
    rng = np.random.default_rng(seed)
    img = dilute + rng.normal(0.0, 2.0, size=shape).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


def _make_store(
    path,
    *,
    channel="Channel",
    seg="Cellpose",
    shape=(160, 160),
    pixel_size_um=0.1,
    centers=None,
    cell_box=(20, 140),
    extra_centers=None,
    empty_cell=False,
    with_seg=True,
):
    """A single-channel dataset with a single-cell Cellpose segmentation."""
    centers = centers or [(80, 80), (80, 40), (40, 110)]
    all_centers = list(centers) + list(extra_centers or [])
    store = DatasetStore(path)
    store.create(metadata={"channel_names": [channel], "pixel_size_um": pixel_size_um})
    img = _granule_image(all_centers, 8, shape=shape)
    # Single channel stored as (1, H, W) — the proven read_channel path.
    store.write_array("intensity", img[None], attrs={"dims": ["C", "H", "W"]})
    if with_seg:
        labels = np.zeros(shape, dtype=np.int32)
        if not empty_cell:
            lo, hi = cell_box
            labels[lo:hi, lo:hi] = 1
        store.write_labels(seg, labels)
    return store


def _fixed():
    return FixedSettings(gaussian_sigma=1.0, min_spot_px=3)


# ── mask name round-trip (pure) ──────────────────────────────────────────


def test_mask_name_is_sortable_and_round_trips():
    assert mask_name("sweep", 51, 2.5) == "sweep_w051_k25"
    assert mask_name("sweep", 15, 1.5) == "sweep_w015_k15"
    # Zero-padded window keeps names lexically sortable across the clamp range.
    assert mask_name("sweep", 9, 3.0) < mask_name("sweep", 101, 1.5)
    assert parse_mask_name("sweep_w051_k25") == (51, 2.5)
    assert parse_mask_name("sweep_w015_k15") == (15, 1.5)
    assert parse_mask_name("not-a-sweep-mask") is None


# ── run_sweep core ────────────────────────────────────────────────────────


def test_run_sweep_writes_full_grid_with_provenance(tmp_path):
    store = _make_store(tmp_path / "Test.h5")
    windows, ks = (15, 31), (2.0, 3.0)
    report = run_sweep(store, "Channel", "Cellpose", windows, ks, _fixed())

    assert isinstance(report, SweepReport)
    assert report.failure is None
    assert report.dataset == "Test"
    assert report.shape == (160, 160)
    assert report.pixel_size_um == 0.1
    assert len(report.rows) == 4

    written = set(store.list_masks())
    for w in windows:
        for k in ks:
            name = mask_name("sweep", w, k)
            assert name in written
            mask = store.read_mask(name)
            assert mask.dtype == np.uint8
            assert set(np.unique(mask)).issubset({0, 1})
            assert parse_mask_name(name) == (w, k)

    # Provenance attrs stamped on each mask.
    with h5py.File(store.path, "r") as f:
        a = f["masks/sweep_w031_k30"].attrs
        assert int(a["window_px"]) == 31
        assert float(a["k"]) == 3.0
        assert str(a["sweep_prefix"]) == "sweep"


def test_run_sweep_restricts_detection_to_cell(tmp_path):
    # A bright blob at (150, 10) lies outside the [20:140] cell box.
    store = _make_store(tmp_path / "T.h5", extra_centers=[(150, 10)])
    report = run_sweep(store, "Channel", "Cellpose", (31,), (2.0,), _fixed())
    assert report.failure is None
    cell = store.read_labels("Cellpose") > 0
    for row in report.rows:
        mask = store.read_mask(row.name)
        assert int(mask[~cell].sum()) == 0  # nothing outside the cell
        assert mask[150, 10] == 0  # the out-of-cell blob is never detected


def test_run_sweep_stats_match_written_mask(tmp_path):
    store = _make_store(tmp_path / "T.h5")
    report = run_sweep(store, "Channel", "Cellpose", (31, 51), (2.0,), _fixed())
    cell_px = int((store.read_labels("Cellpose") > 0).sum())
    for row in report.rows:
        mask = store.read_mask(row.name)
        assert row.in_cell_positive_px == int(mask.sum())
        assert 0.0 <= row.in_cell_fraction <= 1.0
        if cell_px:
            assert abs(row.in_cell_fraction - row.in_cell_positive_px / cell_px) < 1e-9
        assert row.particle_count >= 0


def test_run_sweep_computes_seeds_once(tmp_path, monkeypatch):
    store = _make_store(tmp_path / "T.h5")
    calls = {"n": 0}
    real = wks.compute_seeds

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(wks, "compute_seeds", _counting)
    run_sweep(store, "Channel", "Cellpose", (15, 31, 51), (2.0, 3.0), _fixed())
    # One pass-1 for the whole grid, not one per (window, k) point.
    assert calls["n"] == 1


def test_run_sweep_records_auto_finder_picks(tmp_path):
    store = _make_store(tmp_path / "T.h5")
    windows = (15, 31, 51, 71)
    report = run_sweep(store, "Channel", "Cellpose", windows, (2.5,), _fixed())
    methods = {p.method for p in report.auto_picks}
    assert methods == {"otsu-mean", "granule-size"}

    img = np.asarray(store.read_channel("intensity", 0), dtype=np.float32)
    group = store.read_labels("Cellpose") > 0
    base = base_settings(_fixed())
    for pick in report.auto_picks:
        expected = auto_window(img, 1.0, base, method=pick.method, cp_mask=group)
        assert pick.raw_window == expected
        assert pick.nearest_grid_window in windows
        # nearest is the grid window closest to the raw pick
        assert pick.nearest_grid_window == min(windows, key=lambda w: abs(w - expected))


def test_run_sweep_idempotent_and_clear(tmp_path):
    store = _make_store(tmp_path / "T.h5")
    # A non-sweep mask the clear must not touch.
    store.write_mask("keep", np.ones((160, 160), dtype=np.uint8))

    run_sweep(store, "Channel", "Cellpose", (15, 31), (2.0, 3.0), _fixed())
    first = set(store.list_masks())
    # Re-running overwrites same names — mask set is unchanged.
    run_sweep(store, "Channel", "Cellpose", (15, 31), (2.0, 3.0), _fixed())
    assert set(store.list_masks()) == first
    assert "keep" in store.list_masks()

    # clear=True with a smaller grid removes the stale wider-grid masks.
    run_sweep(store, "Channel", "Cellpose", (15,), (2.0,), _fixed(), clear=True)
    remaining = set(store.list_masks())
    assert "keep" in remaining  # non-sweep mask preserved
    sweep_masks = {m for m in remaining if m.startswith("sweep_")}
    assert sweep_masks == {mask_name("sweep", 15, 2.0)}


def test_run_sweep_dry_run_writes_nothing(tmp_path):
    store = _make_store(tmp_path / "T.h5")
    report = run_sweep(
        store, "Channel", "Cellpose", (15, 31), (2.0,), _fixed(), dry_run=True
    )
    assert store.list_masks() == []  # nothing written
    # Report still enumerates the intended masks.
    assert {r.name for r in report.rows} == {
        mask_name("sweep", 15, 2.0),
        mask_name("sweep", 31, 2.0),
    }


def test_run_sweep_empty_cell_is_all_zero(tmp_path):
    store = _make_store(tmp_path / "T.h5", empty_cell=True)
    report = run_sweep(store, "Channel", "Cellpose", (15, 31), (2.0,), _fixed())
    assert report.failure is None
    for row in report.rows:
        assert row.in_cell_positive_px == 0
        assert int(store.read_mask(row.name).sum()) == 0


def test_run_sweep_missing_segmentation_is_failure_row(tmp_path):
    store = _make_store(tmp_path / "T.h5")
    report = run_sweep(store, "Channel", "DoesNotExist", (15,), (2.0,), _fixed())
    assert report.failure is not None
    assert report.rows == []
    # No masks written on a failed load.
    assert store.list_masks() == []


def test_default_grid_constants():
    assert DEFAULT_WINDOWS == (15, 31, 51, 71, 101, 151)
    assert DEFAULT_KS == (1.5, 2.0, 2.5, 3.0, 3.5)
