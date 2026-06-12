"""Tests for the per-cell contact-sheet sweep harness."""

from __future__ import annotations

import numpy as np
from skimage.draw import disk

from percell4.store import DatasetStore
from percell4.workflows.per_cell_sweep import (
    CellSweep,
    compute_display_range,
    normalize_grid,
    render_contact_sheet,
    run_per_cell_sweep,
    select_cell_ids,
    sweep_one_cell,
)
from percell4.workflows.window_k_sweep import FixedSettings


def _two_cell_dataset(shape=(160, 160)):
    """A (1,H,W) Channel + a 2-instance Cellpose segmentation, blobs in each."""
    rng = np.random.default_rng(0)
    img = (50.0 + rng.normal(0, 2.0, size=shape)).astype(np.float32)
    labels = np.zeros(shape, dtype=np.int32)
    # Cell 1: left block; Cell 2: right block.
    labels[20:140, 10:70] = 1
    labels[20:140, 90:150] = 2
    for cy, cx in [(60, 40), (100, 40)]:  # blobs in cell 1
        rr, cc = disk((cy, cx), 6, shape=shape)
        img[rr, cc] = 220.0
    for cy, cx in [(70, 120)]:  # blob in cell 2
        rr, cc = disk((cy, cx), 6, shape=shape)
        img[rr, cc] = 220.0
    return img, labels


def _make_store(path, *, with_seg=True):
    img, labels = _two_cell_dataset()
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["Channel"], "pixel_size_um": 0.1})
    store.write_array("intensity", img[None], attrs={"dims": ["C", "H", "W"]})
    if with_seg:
        store.write_labels("Cellpose", labels)
    return store


def _fixed():
    return FixedSettings(gaussian_sigma=1.0, min_spot_px=3)


# ── normalize_grid ────────────────────────────────────────────────────────


def test_normalize_grid_forces_odd_and_dedups():
    w, k = normalize_grid([30, 31, 50], [2.0, 2.0, 3.0])
    assert w == (31, 51)  # 30|1==31 dedups with 31; 50|1==51
    assert k == (2.0, 3.0)


# ── select_cell_ids ───────────────────────────────────────────────────────


def test_select_cell_ids_filters_and_caps():
    _img, labels = _two_cell_dataset()
    assert select_cell_ids(labels) == [1, 2]
    # min area larger than any cell -> none
    assert select_cell_ids(labels, min_cell_px=10_000_000) == []
    # cap to the single largest, but returned sorted by id
    capped = select_cell_ids(labels, max_cells=1)
    assert len(capped) == 1


# ── sweep_one_cell (pure) ─────────────────────────────────────────────────


def test_sweep_one_cell_grid_shape_and_restriction():
    img, labels = _two_cell_dataset()
    windows, ks = (15, 31), (2.0, 3.0)
    cell = sweep_one_cell(img, labels, 1, windows, ks, _fixed())

    assert isinstance(cell, CellSweep)
    assert set(cell.masks) == {(w, k) for w in windows for k in ks}
    rmin, cmin, rmax, cmax = cell.bbox
    assert cell.crop.shape == (rmax - rmin, cmax - cmin)
    assert cell.cell_mask.shape == cell.crop.shape
    # Every detection is confined to the cell, and excludes cell 2's blob.
    for mask in cell.masks.values():
        assert mask.dtype == np.uint8
        assert int(mask[~cell.cell_mask].sum()) == 0


def test_sweep_one_cell_isolates_from_neighbor_cell():
    # Cell 2's bright blob must never appear in cell 1's masks even though the
    # padded crop may overlap cell 2's territory.
    img, labels = _two_cell_dataset()
    cell1 = sweep_one_cell(img, labels, 1, (31,), (2.0,), _fixed(), padding=30)
    for mask in cell1.masks.values():
        # No positives outside cell 1's own mask.
        assert int(mask[~cell1.cell_mask].sum()) == 0


def test_sweep_one_cell_unknown_id_raises():
    img, labels = _two_cell_dataset()
    try:
        sweep_one_cell(img, labels, 99, (31,), (2.0,), _fixed())
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── render_contact_sheet ──────────────────────────────────────────────────


def test_compute_display_range_is_global_and_overridable():
    img, labels = _two_cell_dataset()
    lo, hi = compute_display_range(img, labels, low_pct=1.0, high_pct=99.5)
    # Range is taken over in-cell pixels of the whole image.
    inside = img[labels > 0]
    assert lo == float(np.percentile(inside, 1.0))
    assert hi == float(np.percentile(inside, 99.5))
    assert hi > lo
    # Absolute overrides win.
    assert compute_display_range(img, labels, vmin=10.0, vmax=200.0) == (10.0, 200.0)


def test_render_contact_sheet_writes_png(tmp_path):
    img, labels = _two_cell_dataset()
    windows, ks = (15, 31), (2.0, 3.0)
    cell = sweep_one_cell(img, labels, 1, windows, ks, _fixed())
    out = tmp_path / "cell001.png"
    render_contact_sheet(cell, windows, ks, out, vmin=45.0, vmax=230.0)
    assert out.exists() and out.stat().st_size > 0


# ── run_per_cell_sweep (end-to-end) ───────────────────────────────────────


def test_run_per_cell_sweep_end_to_end(tmp_path):
    store = _make_store(tmp_path / "Test1.h5")
    out = tmp_path / "sheets"
    report = run_per_cell_sweep(
        store, "Channel", "Cellpose", (15, 31), (2.0, 3.0), _fixed(), out, min_cell_px=50
    )
    assert report.failure is None
    assert {r.cell_id for r in report.rows} == {1, 2}
    # A contact sheet per cell + the index/template CSVs.
    assert (out / "cell001_contactsheet.png").exists()
    assert (out / "cell002_contactsheet.png").exists()
    assert (out / "cells.csv").exists()
    assert (out / "labels.csv").exists()
    # labels.csv has a blank row per cell for the user to fill in.
    lines = (out / "labels.csv").read_text().splitlines()
    assert lines[0] == "cell_id,best_window,best_k,none_acceptable,notes"
    assert len(lines) == 3  # header + 2 cells


def test_run_per_cell_sweep_never_clobbers_filled_labels(tmp_path):
    store = _make_store(tmp_path / "Test1.h5")
    out = tmp_path / "sheets"
    run_per_cell_sweep(store, "Channel", "Cellpose", (15,), (2.0,), _fixed(), out, min_cell_px=50)
    labels = out / "labels.csv"
    assert labels.exists() and (out / "labels_template.csv").exists()

    # User fills it in.
    filled = labels.read_text().replace("1,,,,", "1,31,1.5,,my pick")
    labels.write_text(filled)

    # Re-running the sweep into the same dir must NOT overwrite the filled labels.
    run_per_cell_sweep(
        store, "Channel", "Cellpose", (15, 31), (2.0,), _fixed(), out, min_cell_px=50
    )
    assert "my pick" in labels.read_text()  # preserved
    # The blank template still refreshes on every run.
    assert (out / "labels_template.csv").read_text().splitlines()[1].endswith(",,,,")


def test_run_per_cell_sweep_missing_segmentation_is_failure(tmp_path):
    store = _make_store(tmp_path / "T.h5", with_seg=False)
    out = tmp_path / "sheets"
    report = run_per_cell_sweep(
        store, "Channel", "Cellpose", (15,), (2.0,), _fixed(), out
    )
    assert report.failure is not None
    assert report.rows == []
