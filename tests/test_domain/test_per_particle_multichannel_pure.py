"""Unit tests for the per_particle_multichannel pure core (U3)."""
from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.analysis._impl.per_particle_multichannel import (
    run_one_image_set,
)

DEFAULTS = dict(buffer=5, donut=5, min_size=4)


def _mask_two_particles(h=64, w=64) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    m[8:14, 8:14] = 1     # 36 px
    m[40:45, 40:45] = 1   # 25 px
    return m


def _channel(h=64, w=64, bg=100.0, blob=900.0) -> np.ndarray:
    a = np.full((h, w), bg, dtype=np.float32)
    a[8:14, 8:14] = blob
    a[40:45, 40:45] = blob * 0.7
    return a


# ── per-particle happy path ───────────────────────────────────────


def test_per_particle_rows_and_columns():
    res = run_one_image_set(
        mask=_mask_two_particles(),
        channels={"mNG": _channel(), "CA-SiR": _channel(blob=300.0)},
        single_cell=False, export_donuts=False, **DEFAULTS,
    )
    rows = res["particle_rows"]
    assert res["cell_rows"] is None
    assert len(rows) == 2
    r = rows[0]
    for col in ("particle_id", "particle_area_px", "donut_area_px",
                "condensed_mNG_mean", "dilute_mNG_mean",
                "mNG_condensed_over_dilute", "condensed_mNG_integ",
                "dilute_mNG_integ", "condensed_CA-SiR_mean"):
        assert col in r
    # condensed > dilute for the blob channel
    assert r["condensed_mNG_mean"] > r["dilute_mNG_mean"]
    # ratio == condensed/dilute
    assert r["mNG_condensed_over_dilute"] == pytest.approx(
        r["condensed_mNG_mean"] / r["dilute_mNG_mean"]
    )


def test_raw_means_no_background_subtraction():
    """Condensed mean equals the raw blob value (no bg subtraction)."""
    res = run_one_image_set(
        mask=_mask_two_particles(),
        channels={"mNG": _channel(bg=100.0, blob=900.0)},
        single_cell=False, export_donuts=False, **DEFAULTS,
    )
    p1 = [r for r in res["particle_rows"] if r["particle_id"] == 1][0]
    assert p1["condensed_mNG_mean"] == pytest.approx(900.0)


# ── cp_mask without single_cell → cell_id column ──────────────────


def test_cp_mask_without_single_cell_adds_cell_id():
    cp = np.zeros((64, 64), np.int32)
    cp[:32, :] = 1
    cp[32:, :] = 2
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=cp, single_cell=False, export_donuts=False, **DEFAULTS,
    )
    rows = res["particle_rows"]
    assert all("cell_id" in r for r in rows)
    # particle at row 8 → cell 1, particle at row 40 → cell 2
    assert sorted(r["cell_id"] for r in rows) == [1, 2]
    # particle_id is per-cell sequential: the only particle in each cell is 1
    assert {(r["cell_id"], r["particle_id"]) for r in rows} == {(1, 1), (2, 1)}


def test_no_cells_yields_no_particles():
    """With a cp_mask present but empty, no particle is in any cell — so the
    per-particle table is empty, matching single-cell (which measures only
    particles inside cells)."""
    cp = np.zeros((64, 64), np.int32)  # no cells anywhere
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=cp, single_cell=False, export_donuts=False, **DEFAULTS,
    )
    assert res["particle_rows"] == []


def test_per_cell_ids_and_clipped_area_match_single_cell():
    """The per-particle table must share (cell_id, particle_id, area) with the
    single-cell thresholding detail table — this is the bug this analysis was
    fixed for. Cross-checks against the canonical single-cell extractor."""
    from percell4.domain.measure.particle import analyze_particles_detail

    h = w = 40
    cp = np.zeros((h, w), np.int32)
    cp[0:20, :] = 1       # cell 1 (top)
    cp[20:35, :] = 2      # cell 2 (middle); rows 35:40 are background (0)

    mask = np.zeros((h, w), np.uint8)
    mask[5:8, 5:8] = 1    # A: fully inside cell 1 (9 px)
    mask[18:23, 25:28] = 1  # B: straddles cell1/cell2 boundary → split
    mask[33:38, 5:8] = 1  # C: straddles cell2/background → clipped to cell2

    chan = np.random.default_rng(0).random((h, w)).astype(np.float64) * 100

    min_v = 1
    pp = run_one_image_set(
        mask=mask, channels={"ch": chan}, cp_mask=cp, single_cell=False,
        export_donuts=False, buffer=2, donut=3, min_size=min_v,
    )["particle_rows"]
    sc = analyze_particles_detail(
        images={"ch": chan}, labels=cp, mask=mask, min_area=min_v,
    )

    pp_keys = sorted((r["cell_id"], r["particle_id"], r["particle_area_px"])
                     for r in pp)
    sc_keys = sorted((int(r.cell_id), int(r.particle_id), int(r.area))
                     for r in sc.itertuples())
    assert pp_keys == sc_keys
    # The straddling particle B is split into two rows (one per cell), and the
    # clipped particle C reports only its in-cell pixels — both consequences
    # of per-cell labeling that the keys above encode.
    assert len(pp_keys) == len(sc_keys) >= 4


# ── single-cell ───────────────────────────────────────────────────


def test_single_cell_rows_and_whole_cell_stats():
    cp = np.zeros((64, 64), np.int32)
    cp[:32, :] = 1
    cp[32:, :] = 2
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=cp, single_cell=True, export_donuts=False, **DEFAULTS,
    )
    assert res["particle_rows"] is None
    cells = res["cell_rows"]
    assert len(cells) == 2  # one row per non-zero cell id
    c = cells[0]
    for col in ("cell_id", "cell_area_px", "n_particles",
                "cell_mNG_mean", "cell_mNG_median", "cell_mNG_mode",
                "cell_mNG_min", "cell_mNG_max", "cell_mNG_integ",
                "condensed_mNG_mean", "dilute_mNG_mean"):
        assert col in c


def test_single_cell_empty_cell_still_emitted():
    """A cell with no particles still gets a row (whole-cell stats, NaN metrics)."""
    cp = np.zeros((64, 64), np.int32)
    cp[:32, :] = 1   # cell 1 holds both particles
    cp[55:60, 0:5] = 3  # cell 3 holds no particle
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=cp, single_cell=True, export_donuts=False, **DEFAULTS,
    )
    cells = {c["cell_id"]: c for c in res["cell_rows"]}
    assert 3 in cells
    assert cells[3]["n_particles"] == 0
    assert np.isnan(cells[3]["condensed_mNG_mean"])
    assert not np.isnan(cells[3]["cell_mNG_mean"])  # whole-cell stat present


def test_single_cell_requires_cp_mask():
    with pytest.raises(ValueError, match="cp_mask"):
        run_one_image_set(
            mask=_mask_two_particles(), channels={"mNG": _channel()},
            cp_mask=None, single_cell=True, export_donuts=False, **DEFAULTS,
        )


# ── per-particle cell_<ch>_mean enrichment (U2) ───────────────────


def _cp_two_cells(h=64, w=64) -> np.ndarray:
    cp = np.zeros((h, w), np.int32)
    cp[:32, :] = 1   # holds particle at rows 8:14
    cp[32:, :] = 2   # holds particle at rows 40:45
    return cp


def test_cell_mean_channels_adds_only_requested_columns():
    res = run_one_image_set(
        mask=_mask_two_particles(),
        channels={"mNG": _channel(), "CA-SiR": _channel(blob=300.0)},
        cp_mask=_cp_two_cells(), single_cell=False, export_donuts=False,
        cell_mean_channels=["mNG"], **DEFAULTS,
    )
    rows = res["particle_rows"]
    assert len(rows) == 2  # one row per particle (R2)
    for r in rows:
        assert "cell_mNG_mean" in r
        assert "cell_CA-SiR_mean" not in r


def test_cell_mean_matches_cell_table_value():
    """R3: per-particle cell_<ch>_mean == single-cell cell_table value."""
    mask = _mask_two_particles()
    chans = {"mNG": _channel()}
    cp = _cp_two_cells()
    pp = run_one_image_set(
        mask=mask, channels=chans, cp_mask=cp, single_cell=False,
        export_donuts=False, cell_mean_channels=["mNG"], **DEFAULTS,
    )["particle_rows"]
    sc = run_one_image_set(
        mask=mask, channels=chans, cp_mask=cp, single_cell=True,
        export_donuts=False, **DEFAULTS,
    )["cell_rows"]
    cell_table_mean = {c["cell_id"]: c["cell_mNG_mean"] for c in sc}
    for r in pp:
        if r["cell_id"] != 0:  # cell 0 has no cell_table row
            assert r["cell_mNG_mean"] == pytest.approx(
                cell_table_mean[r["cell_id"]]
            )


def test_cell_mean_channels_none_leaves_rows_unchanged():
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=_cp_two_cells(), single_cell=False, export_donuts=False,
        cell_mean_channels=None, **DEFAULTS,
    )
    assert all("cell_mNG_mean" not in r for r in res["particle_rows"])
    res_empty = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=_cp_two_cells(), single_cell=False, export_donuts=False,
        cell_mean_channels=[], **DEFAULTS,
    )
    assert all("cell_mNG_mean" not in r for r in res_empty["particle_rows"])


def test_cell_mean_channels_without_cp_mask_is_noop():
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=None, single_cell=False, export_donuts=False,
        cell_mean_channels=["mNG"], **DEFAULTS,
    )
    assert all("cell_mNG_mean" not in r for r in res["particle_rows"])


def test_cell_mean_no_cells_yields_no_rows():
    cp = np.zeros((64, 64), np.int32)  # no cells → no in-cell particles
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=cp, single_cell=False, export_donuts=False,
        cell_mean_channels=["mNG"], **DEFAULTS,
    )
    assert res["particle_rows"] == []


def test_cell_mean_unknown_channel_skipped():
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        cp_mask=_cp_two_cells(), single_cell=False, export_donuts=False,
        cell_mean_channels=["mNG", "does-not-exist"], **DEFAULTS,
    )
    rows = res["particle_rows"]
    assert all("cell_mNG_mean" in r for r in rows)
    assert all("cell_does-not-exist_mean" not in r for r in rows)


def test_cell_mean_generalizes_to_arbitrary_channel_names():
    """R6: nothing hardcodes the example channel names."""
    res = run_one_image_set(
        mask=_mask_two_particles(),
        channels={"GFP": _channel(), "foo": _channel(blob=300.0)},
        cp_mask=_cp_two_cells(), single_cell=False, export_donuts=False,
        cell_mean_channels=["foo"], **DEFAULTS,
    )
    rows = res["particle_rows"]
    for r in rows:
        assert "cell_foo_mean" in r
        assert "cell_GFP_mean" not in r
        assert "condensed_GFP_mean" in r and "condensed_foo_mean" in r


# ── edge cases ────────────────────────────────────────────────────


def test_export_donuts_returns_uint8_union_mask():
    res = run_one_image_set(
        mask=_mask_two_particles(), channels={"mNG": _channel()},
        single_cell=False, export_donuts=True, **DEFAULTS,
    )
    dm = res["donut_mask"]
    assert dm is not None
    assert dm.dtype == np.uint8
    assert set(np.unique(dm)).issubset({0, 255})
    assert dm.sum() > 0


def test_no_particles_after_min_size_filter():
    m = np.zeros((64, 64), np.uint8)
    m[2:4, 2:4] = 1  # 4 px, not > min_size=4
    res = run_one_image_set(
        mask=m, channels={"mNG": _channel()},
        single_cell=False, export_donuts=False, **DEFAULTS,
    )
    assert res["particle_rows"] == []


def test_channel_shape_mismatch_raises():
    with pytest.raises(ValueError, match="does not match mask"):
        run_one_image_set(
            mask=_mask_two_particles(),
            channels={"mNG": np.zeros((10, 10), np.float32)},
            single_cell=False, export_donuts=False, **DEFAULTS,
        )
