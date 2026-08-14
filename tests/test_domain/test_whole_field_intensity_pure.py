"""Unit tests for the whole_field_intensity pure core (U6)."""
from __future__ import annotations

import numpy as np
from skimage import measure

from percell4.domain.analysis._impl.whole_field_intensity import (
    compute_bg_value,
    parse_bg_mode,
    run_one_image_set,
)

H = W = 48


def _block(r0, r1, c0, c1):
    m = np.zeros((H, W), np.uint8)
    m[r0:r1, c0:c1] = 1
    return m


def _field():
    dilute = _block(4, 44, 4, 44)
    pbody = np.zeros((H, W), np.uint8)
    pbody[8:12, 8:12] = 1
    pbody[30:34, 30:34] = 1
    dcp2 = _block(10, 30, 10, 30)
    dcp2_2 = _block(14, 20, 14, 20)
    interaction = _block(8, 32, 8, 32)
    interaction_2 = _block(15, 19, 15, 19)
    cp = np.zeros((H, W), np.int32)
    cp[4:24, 4:44] = 1
    cp[24:44, 4:44] = 2
    mng = np.full((H, W), 50.0, np.float32)
    mng[dcp2 > 0] = 400.0
    mng[dcp2_2 > 0] = 900.0
    halo = np.full((H, W), 30.0, np.float32)
    halo[interaction > 0] = 300.0
    halo[interaction_2 > 0] = 700.0
    return dict(pbody=pbody, dilute=dilute, dcp2=dcp2, dcp2_2=dcp2_2,
                interaction=interaction, interaction_2=interaction_2,
                cp=cp, mng=mng, halo=halo)


def _field_3blobs():
    """Like ``_field`` but the condensate mask has THREE discrete blobs.

    blob1 (rows 8:12) and blob3 (rows 18:22) fall in cell 1 (rows 4:24);
    blob2 (rows 30:34) falls in cell 2 (rows 24:44). Used for the
    per-particle export tests, which need >2 well-separated particles spread
    across both cells. Left separate from ``_field`` so the existing
    two-blob area assertions stay intact.
    """
    f = _field()
    pbody = f["pbody"].copy()
    pbody[18:22, 18:22] = 1  # 3rd blob (16 px) inside cell 1
    f["pbody"] = pbody
    return f


# ── parse_bg_mode / compute_bg_value rounding ─────────────────────


def test_parse_bg_mode_keyword_and_int():
    assert parse_bg_mode("mean") == "mean"
    assert parse_bg_mode("mng-nan-mode") == "mng-nan-mode"
    assert parse_bg_mode("7") == 7
    assert isinstance(parse_bg_mode("7"), int)


def test_compute_bg_manual_is_unrounded_including_zero():
    img = np.full((8, 8), 3.5, np.float64)
    mask = np.ones((8, 8), bool)
    # Manual integer path returns the value verbatim — including 0.
    assert compute_bg_value(img, mask, 0) == 0
    assert compute_bg_value(img, mask, 5) == 5


def test_compute_bg_keyword_modes_round_up():
    img = np.array([[10.0, 20.0, 30.0, 41.0]], np.float64)
    mask = np.ones((1, 4), bool)
    # mean = 25.25 -> ceil 26; median = 25.0 -> ceil 25.
    assert compute_bg_value(img, mask, "mean") == 26
    assert compute_bg_value(img, mask, "median") == 25


# ── two-region whole field ────────────────────────────────────────


def test_two_region_whole_field():
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
    )
    rows = res["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["pbody_area_px"] == 32  # two 16px blobs
    for col in ("mNG_pbody_mean", "mNG_dilute_mean", "halo_pbody_mean",
                "mng_bg_value", "halo_bg_value"):
        assert col in r
    assert r["mng_bg_value"] == 0 and r["halo_bg_value"] == 0


# ── three-region (v4 / v5) ─────────────────────────────────────────


def test_three_region_v4_columns():
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        dcp2_mask=f["dcp2"], interaction_mask=f["interaction"],
        dcp2_mask_2=f["dcp2_2"], interaction_mask_2=f["interaction_2"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
        mng_filter_mode="NaN", flim_filter_mode="zero",
        compute_percent=True, intermediate_assemblies=True,
    )
    r = res["rows"][0]
    for col in ("intermediate_area_px", "mNG_intermediate_mean",
                "halo_intermediate_mean", "pct_halo_in_mNG_intermediate"):
        assert col in r
    assert r["intermediate_area_px"] == 36  # 6x6 inner Dcp2


def test_v4_v5_differ_only_in_halo_means():
    f = _field()
    common = dict(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"], halo=f["halo"],
        mng=f["mng"], dcp2_mask=f["dcp2"], interaction_mask=f["interaction"],
        dcp2_mask_2=f["dcp2_2"], interaction_mask_2=f["interaction_2"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
        mng_filter_mode="NaN", flim_filter_mode="zero",
        intermediate_assemblies=True,
    )
    v4 = run_one_image_set(**common, intermediate_zero_fill=False)["rows"][0]
    v5 = run_one_image_set(**common, intermediate_zero_fill=True)["rows"][0]
    # mNG areas/means identical; halo means differ (zero-fill drags down).
    assert v4["mNG_pbody_mean"] == v5["mNG_pbody_mean"]
    assert v4["intermediate_area_px"] == v5["intermediate_area_px"]


# ── single-cell ────────────────────────────────────────────────────


def test_single_cell_two_region_rows():
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
    )
    rows = res["rows"]
    assert len(rows) == 2  # two cells
    for r in rows:
        assert "cell_id" in r and "particle_count" in r
        assert "mNG_cell_mean" in r


def test_single_cell_default_has_both_cell_means():
    """Default (channel_cell_mean=True): rows carry BOTH mNG_cell_mean AND
    Halo_cell_mean per cell."""
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
    )
    for r in res["rows"]:
        assert "mNG_cell_mean" in r
        assert "Halo_cell_mean" in r


def test_single_cell_channel_cell_mean_both():
    """The default (channel_cell_mean=True, no param passed) emits both cell
    means, with Halo_cell_mean immediately after mNG_cell_mean, each equal to
    np.nanmean of the bg-subtracted channel over each cell's region."""
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
    )
    # With bg=0 and no FLIM/SiR filters, halo_sub == halo (all >= 0).
    halo_sub = np.maximum(f["halo"].astype(np.float64), 0)
    mng_sub = f["mng"].astype(np.float64)
    mng_sub[mng_sub <= 0] = np.nan
    for r in res["rows"]:
        keys = list(r.keys())
        # Position: Halo_cell_mean directly after mNG_cell_mean.
        assert keys[keys.index("mNG_cell_mean") + 1] == "Halo_cell_mean"
        cell_region = f["cp"] == r["cell_id"]
        assert np.isclose(r["mNG_cell_mean"], np.nanmean(mng_sub[cell_region]))
        assert np.isclose(r["Halo_cell_mean"],
                          np.nanmean(halo_sub[cell_region]))


def test_channel_cell_mean_no_op_without_single_cell():
    """channel_cell_mean=True (the default) is a no-op (no cell-mean columns,
    no raise) without single_cell or without a cp_mask."""
    f = _field()
    # No single_cell.
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, channel_cell_mean=True,
    )
    assert "mNG_cell_mean" not in res["rows"][0]
    assert "Halo_cell_mean" not in res["rows"][0]
    # single_cell=True but no cp_mask -> whole-field fall-through, still no col.
    res2 = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=None,
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
        channel_cell_mean=True,
    )
    assert "mNG_cell_mean" not in res2["rows"][0]
    assert "Halo_cell_mean" not in res2["rows"][0]


def test_single_cell_channel_cell_mean_nan_for_all_nan_cell():
    """A cell whose Halo pixels are all NaN (FLIM-NaN'd) -> Halo_cell_mean is
    NaN, no error. (Default channel_cell_mean=True surfaces Halo_cell_mean.)"""
    f = _field()
    # interaction covers only cell 1 (cp rows 4:24); cell 2 (rows 24:44) is
    # entirely outside, so FLIM-NaN makes its Halo all-NaN.
    interaction = np.zeros((H, W), np.uint8)
    interaction[4:24, 4:44] = 1
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        interaction_mask=interaction,
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
        flim_filter_mode="NaN",
    )
    by_cell = {r["cell_id"]: r for r in res["rows"]}
    assert np.isnan(by_cell[2]["Halo_cell_mean"])
    assert not np.isnan(by_cell[1]["Halo_cell_mean"])


def test_v4_single_cell_channel_cell_mean_both():
    """The three-region (v4) single-cell path also carries Halo_cell_mean right
    after mNG_cell_mean by default (channel_cell_mean=True)."""
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        dcp2_mask=f["dcp2"], interaction_mask=f["interaction"],
        dcp2_mask_2=f["dcp2_2"], interaction_mask_2=f["interaction_2"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
        mng_filter_mode="NaN", flim_filter_mode="zero",
        intermediate_assemblies=True, single_cell=True,
    )
    for r in res["rows"]:
        keys = list(r.keys())
        assert "mNG_cell_mean" in r and "Halo_cell_mean" in r
        assert keys[keys.index("mNG_cell_mean") + 1] == "Halo_cell_mean"


def test_single_cell_channel_cell_mean_false_emits_neither():
    """channel_cell_mean=False in single-cell mode emits NEITHER mNG_cell_mean
    nor Halo_cell_mean."""
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
        channel_cell_mean=False,
    )
    for r in res["rows"]:
        assert "mNG_cell_mean" not in r
        assert "Halo_cell_mean" not in r


def test_single_cell_without_cp_mask_falls_through_to_whole_field():
    """Faithful to the original: single_cell without cp_mask silently does the
    whole-field path (one row, no cell_id). The CLI's _check_channels and the
    framework's BoolParam.requires=('cp_mask',) are what reject this upstream."""
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=None,
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
    )
    assert len(res["rows"]) == 1
    assert "cell_id" not in res["rows"][0]


# ── halo_bg_override + does not mutate caller arrays ──────────────


def test_halo_bg_override_used_verbatim():
    f = _field()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        mng_bg_mode=0, halo_bg_mode="median", min_size=2,
        halo_bg_override=42,
    )
    assert res["rows"][0]["halo_bg_value"] == 42


def test_input_arrays_not_mutated():
    f = _field()
    halo_before = f["halo"].copy()
    mng_before = f["mng"].copy()
    run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        dcp2_mask=f["dcp2"], interaction_mask=f["interaction"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
        mng_filter_mode="NaN", flim_filter_mode="zero",
    )
    np.testing.assert_array_equal(f["halo"], halo_before)
    np.testing.assert_array_equal(f["mng"], mng_before)


# ── export_particles: per-condensate-particle rows ────────────────


_PARTICLE_COLS = (
    "particle_id", "particle_area_px",
    "mNG_particle_mean", "mNG_particle_integ",
    "halo_particle_mean", "halo_particle_integ",
    "halo_over_mNG_particle",
)


def _expected_channels(f):
    """Reproduce the core's bg=0, no-filter mng_sub / halo_sub / mng_valid."""
    mng_sub = f["mng"].astype(np.float64)
    mng_sub[mng_sub <= 0] = np.nan
    halo_sub = np.maximum(f["halo"].astype(np.float64), 0)
    return mng_sub, halo_sub, ~np.isnan(mng_sub)


def test_export_particles_default_off_no_key():
    """Default (export_particles=False) → no ``particle_rows`` key, in both
    whole-field and single-cell modes (so the default path is unchanged)."""
    f = _field_3blobs()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
    )
    assert "particle_rows" not in res
    res_sc = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
    )
    assert "particle_rows" not in res_sc


def test_export_particles_whole_field_values_and_columns():
    """Whole-field (no cp_mask) export: one row per blob with the exact column
    set/order (no cell_id), values mirroring _measure_region's pbody logic."""
    f = _field_3blobs()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, export_particles=True,
    )
    rows = res["particle_rows"]
    assert len(rows) == 3  # three discrete blobs

    mng_sub, halo_sub, mng_valid = _expected_channels(f)
    labels = measure.label(f["pbody"] > 0)
    for r in rows:
        assert tuple(r.keys()) == _PARTICLE_COLS  # exact order, no cell_id
        pmask = labels == r["particle_id"]
        halo_pix = halo_sub[pmask & mng_valid]
        assert r["particle_area_px"] == int(pmask.sum())
        assert np.isclose(r["mNG_particle_mean"], np.nanmean(mng_sub[pmask]))
        assert np.isclose(r["mNG_particle_integ"], np.nansum(mng_sub[pmask]))
        assert np.isclose(r["halo_particle_mean"], np.nanmean(halo_pix))
        assert np.isclose(r["halo_particle_integ"], np.nansum(halo_pix))
        assert np.isclose(
            r["halo_over_mNG_particle"],
            r["halo_particle_mean"] / r["mNG_particle_mean"],
        )


def test_export_particles_single_cell_assigns_cell_id_by_majority():
    """Single-cell export: each particle gains a ``cell_id`` (right after
    ``particle_id``) by the same majority-pixel rule as particle_count, and the
    row total equals the summed per-cell particle_count (1:1 correspondence)."""
    f = _field_3blobs()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"], cp_mask=f["cp"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2, single_cell=True,
        export_particles=True,
    )
    rows = res["particle_rows"]
    assert len(rows) == 3

    labels = measure.label(f["pbody"] > 0)
    for r in rows:
        keys = list(r.keys())
        assert keys[0] == "particle_id"
        assert keys[1] == "cell_id"
        assert keys[2] == "particle_area_px"
        pmask = labels == r["particle_id"]
        vals, counts = np.unique(f["cp"][pmask], return_counts=True)
        assert r["cell_id"] == int(vals[np.argmax(counts)])

    # blob1 + blob3 → cell 1, blob2 → cell 2.
    assert sum(r["cell_id"] == 1 for r in rows) == 2
    assert sum(r["cell_id"] == 2 for r in rows) == 1

    # 1:1 with the per-cell particle_count column on the main rows.
    pc = {row["cell_id"]: row["particle_count"] for row in res["rows"]}
    assert pc == {1: 2, 2: 1}
    assert sum(pc.values()) == len(rows)


def test_export_particles_v4_three_region_path():
    """The v4 three-region whole-field path also exports particles (the
    condensate mask is pbody either way; particles measured on mng_sub/halo_sub
    with the mng_valid Halo masking)."""
    f = _field_3blobs()
    res = run_one_image_set(
        pbody_mask=f["pbody"], dilute_mask=f["dilute"],
        halo=f["halo"], mng=f["mng"],
        dcp2_mask=f["dcp2"], interaction_mask=f["interaction"],
        dcp2_mask_2=f["dcp2_2"], interaction_mask_2=f["interaction_2"],
        mng_bg_mode=0, halo_bg_mode=0, min_size=2,
        mng_filter_mode="NaN", flim_filter_mode="zero",
        intermediate_assemblies=True, export_particles=True,
    )
    rows = res["particle_rows"]
    assert len(rows) == 3
    # Three-region main row is unaffected by the particle export.
    assert "mNG_intermediate_mean" in res["rows"][0]
    for r in rows:
        assert tuple(r.keys()) == _PARTICLE_COLS
        assert "dilute" not in " ".join(r.keys())  # condensate only
