"""Unit tests for the whole_field_intensity pure core (U6)."""
from __future__ import annotations

import numpy as np

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
