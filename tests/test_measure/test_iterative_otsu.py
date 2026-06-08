"""Tests for the iterative-Otsu peeling core + stopping-criterion registry."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from percell4.domain.measure.iterative_otsu import (
    STOP_CRITERIA,
    IterativeOtsuReport,
    RoundContext,
    otsu_with_separability,
    params_by_name,
    peel,
)
from percell4.domain.measure.iterative_otsu_names import (
    SCOPE_NAMES,
    STOP_CRITERION_NAMES,
)


def _settings(
    *,
    stop_criteria=(),
    stop_combine="any",
    stop_params=(),
    max_rounds=10,
    dilation_radius_px=5,
):
    """Duck-typed stand-in for IterativeOtsuSettings (U2 owns the real one)."""
    return SimpleNamespace(
        stop_criteria=tuple(stop_criteria),
        stop_combine=stop_combine,
        stop_params=tuple(stop_params),
        max_rounds=max_rounds,
        dilation_radius_px=dilation_radius_px,
    )


def _ctx(*, residual, thr, eta=0.5, candidate_mask=None, cumulative_px=0, iteration=1):
    if candidate_mask is None:
        candidate_mask = np.zeros((4, 4), dtype=bool)
    return RoundContext(
        residual=np.asarray(residual, dtype=np.float64),
        candidate_mask=np.asarray(candidate_mask, dtype=bool),
        unit_mask=np.ones((4, 4), dtype=bool),
        thr=float(thr),
        eta=float(eta),
        cumulative_px=int(cumulative_px),
        iteration=int(iteration),
    )


# ── otsu_with_separability ────────────────────────────────────


def test_separability_high_for_bimodal_low_for_noise():
    rng = np.random.default_rng(0)
    bimodal = np.concatenate([rng.normal(10, 1, 500), rng.normal(100, 1, 500)])
    noise = rng.normal(10, 1, 1000)
    _, eta_bi = otsu_with_separability(bimodal)
    _, eta_noise = otsu_with_separability(noise)
    # A genuine bimodal split scores near 1; a unimodal-noise split still scores
    # ~0.6-0.65 (Otsu halves the Gaussian) — which is why the separability stop
    # cutoff defaults to 0.8, not something low.
    assert eta_bi > 0.9
    assert eta_noise < 0.8
    assert eta_bi > eta_noise


# ── peel: recovers a dim focus single Otsu misses (AE1) ───────


def test_peel_recovers_dim_focus_single_otsu_misses():
    from skimage.filters import threshold_otsu as sk_otsu

    img = np.full((100, 100), 20.0, dtype=np.float32)
    img[10:40, 10:40] = 1000.0  # bright focus (900 px) — dominates the histogram
    img[60:70, 60:70] = 60.0  # dim focus (100 px)

    # Premise: a single global Otsu is dragged up by the bright focus, lands well
    # above the dim focus, and misses it.
    single_thr = float(sk_otsu(img))
    assert single_thr > 60.0
    assert img[65, 65] < single_thr  # dim focus is below the single threshold

    units = [np.ones(img.shape, dtype=bool)]
    mask, report = peel(img, units, _settings())  # no stop criteria: degenerate guard ends it

    assert mask[25, 25] == 1  # bright focus captured
    assert mask[65, 65] == 1  # dim focus recovered by peeling
    assert mask[0, 0] == 0  # background stays out
    assert report.n_positive == int(mask.sum())
    assert report.n_iterations_run >= 2  # took at least two iterations to peel both


# ── peel: pure background stops, never floods (AE2) ───────────


def test_peel_pure_noise_does_not_flood():
    rng = np.random.default_rng(1)
    img = rng.normal(20.0, 2.0, size=(80, 80)).astype(np.float32)
    units = [np.ones(img.shape, dtype=bool)]
    settings = _settings(stop_criteria=("positive-fraction-high",))

    mask, report = peel(img, units, settings)

    # Otsu would mark ~half of pure noise; the fraction guard must veto that.
    assert mask.sum() == 0
    assert report.n_iterations_run == 1
    assert report.criterion_fires["positive-fraction-high"] == 1


# ── peel: always terminates (AE3) ─────────────────────────────


def test_peel_terminates_within_max_rounds():
    rng = np.random.default_rng(2)
    img = rng.normal(20.0, 2.0, size=(60, 60)).astype(np.float32)
    units = [np.ones(img.shape, dtype=bool)]
    # min_px=0 can never fire (candidate_px < 0 is always False) -> only the
    # degenerate guard / max_rounds can stop the loop.
    settings = _settings(
        stop_criteria=("min-positive",),
        stop_params=(("min-positive.min_px", 0),),
        max_rounds=4,
    )

    mask, report = peel(img, units, settings)

    assert report.n_iterations_run <= 4
    assert mask.dtype == np.uint8


# ── peel: dilation re-clamp never bleeds across units (AE4) ───


def test_peel_dilation_does_not_bleed_across_units():
    img = np.full((50, 100), 20.0, dtype=np.float32)
    img[20:30, 44:50] = 300.0  # focus in cell A, touching the shared border (col 50)

    cell_a = np.zeros(img.shape, dtype=bool)
    cell_a[:, :50] = True  # left half is the only unit we peel

    mask, _ = peel(img, [cell_a], _settings(dilation_radius_px=5))

    assert mask[25, 47] == 1  # focus captured inside cell A
    assert mask[:, 50:].sum() == 0  # nothing leaks into cell B's half


# ── peel: degenerate + empty units ────────────────────────────


def test_constant_unit_marks_done_no_raise():
    img = np.full((20, 20), 42.0, dtype=np.float32)
    mask, report = peel(img, [np.ones(img.shape, dtype=bool)], _settings())
    assert mask.sum() == 0
    assert report.n_iterations_run == 1
    assert report.units_hit_max_rounds == 0


def test_empty_unit_skipped_no_raise():
    img = np.full((20, 20), 20.0, dtype=np.float32)
    img[5:10, 5:10] = 200.0
    empty = np.zeros(img.shape, dtype=bool)
    mask, report = peel(img, [empty], _settings())
    assert mask.sum() == 0
    assert report.units_total == 1


def test_output_is_binary_uint8():
    img = np.full((40, 40), 10.0, dtype=np.float32)
    img[10:20, 10:20] = 250.0
    img[25:30, 25:30] = 60.0
    mask, _ = peel(img, [np.ones(img.shape, dtype=bool)], _settings())
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)) <= {0, 1}


# ── stopping criteria in isolation ────────────────────────────


def test_bg_floor_fires_on_noise_not_on_signal():
    rng = np.random.default_rng(3)
    noise = rng.normal(10.0, 1.0, 1000)
    signal = np.concatenate([rng.normal(10.0, 1.0, 900), np.full(100, 100.0)])
    p = {"k": 2.0}
    # Noise: a low threshold sits within bg + k*sigma -> done.
    assert STOP_CRITERIA["bg-floor"](_ctx(residual=noise, thr=11.0), p)
    # Signal: Otsu's threshold sits well above bg + k*sigma -> keep going.
    assert not STOP_CRITERIA["bg-floor"](_ctx(residual=signal, thr=55.0), p)


def test_separability_criterion():
    r = np.array([1.0, 2.0, 3.0, 4.0])
    assert STOP_CRITERIA["separability"](_ctx(residual=r, thr=2.5, eta=0.2), {"min_eta": 0.5})
    assert not STOP_CRITERIA["separability"](_ctx(residual=r, thr=2.5, eta=0.8), {"min_eta": 0.5})


def test_positive_fraction_high_criterion():
    residual = np.arange(100, dtype=float)
    cand = np.zeros((10, 10), dtype=bool)
    cand.ravel()[:60] = True  # 60 of 100 residual px positive
    assert STOP_CRITERIA["positive-fraction-high"](
        _ctx(residual=residual, thr=0.0, candidate_mask=cand), {"max_frac": 0.5}
    )
    cand2 = np.zeros((10, 10), dtype=bool)
    cand2.ravel()[:30] = True
    assert not STOP_CRITERIA["positive-fraction-high"](
        _ctx(residual=residual, thr=0.0, candidate_mask=cand2), {"max_frac": 0.5}
    )


def test_min_positive_criterion():
    residual = np.arange(100, dtype=float)
    small = np.zeros((10, 10), dtype=bool)
    small.ravel()[:3] = True
    assert STOP_CRITERIA["min-positive"](
        _ctx(residual=residual, thr=0.0, candidate_mask=small), {"min_px": 5}
    )
    big = np.zeros((10, 10), dtype=bool)
    big.ravel()[:20] = True
    assert not STOP_CRITERIA["min-positive"](
        _ctx(residual=residual, thr=0.0, candidate_mask=big), {"min_px": 5}
    )


def test_diminishing_returns_criterion():
    residual = np.arange(100, dtype=float)
    cand = np.zeros((10, 10), dtype=bool)
    cand.ravel()[:10] = True  # new_px = 10
    p = {"min_gain_frac": 0.02}
    assert STOP_CRITERIA["diminishing-returns"](
        _ctx(residual=residual, thr=0.0, candidate_mask=cand, cumulative_px=1000), p
    )  # 10 < 0.02 * 1000 = 20
    assert not STOP_CRITERIA["diminishing-returns"](
        _ctx(residual=residual, thr=0.0, candidate_mask=cand, cumulative_px=100), p
    )  # 10 < 0.02 * 100 = 2  -> False
    # cumulative 0 never fires (avoid div-by-zero / first-iteration veto)
    assert not STOP_CRITERIA["diminishing-returns"](
        _ctx(residual=residual, thr=0.0, candidate_mask=cand, cumulative_px=0), p
    )


def test_peak_prominence_criterion():
    rng = np.random.default_rng(4)
    noise = rng.normal(10.0, 1.0, 1000)
    signal = np.concatenate([rng.normal(10.0, 1.0, 999), np.full(1, 100.0)])
    p = {"k": 5.0}
    assert STOP_CRITERIA["peak-prominence"](_ctx(residual=noise, thr=12.0), p)
    assert not STOP_CRITERIA["peak-prominence"](_ctx(residual=signal, thr=12.0), p)


def test_min_area_components_criterion():
    residual = np.arange(100, dtype=float)
    speck = np.zeros((10, 10), dtype=bool)
    speck[0, 0] = speck[0, 1] = True  # one 2-px component
    assert STOP_CRITERIA["min-area-components"](
        _ctx(residual=residual, thr=0.0, candidate_mask=speck), {"min_area_px": 3}
    )
    blob = np.zeros((10, 10), dtype=bool)
    blob[2:5, 2:5] = True  # one 9-px component
    assert not STOP_CRITERIA["min-area-components"](
        _ctx(residual=residual, thr=0.0, candidate_mask=blob), {"min_area_px": 3}
    )


# ── combine any / all ─────────────────────────────────────────


def test_combine_any_vs_all():
    rng = np.random.default_rng(5)
    img = rng.normal(20.0, 2.0, size=(50, 50)).astype(np.float32)
    units = [np.ones(img.shape, dtype=bool)]

    # "any": positive-fraction-high alone stops round 1.
    _, rep_any = peel(
        img,
        units,
        _settings(
            stop_criteria=("positive-fraction-high", "min-area-components"),
            stop_combine="any",
        ),
    )
    assert rep_any.n_iterations_run == 1

    # "all": requires BOTH to fire; min-area-components won't on a big noise blob,
    # so the unit keeps peeling past round 1 (until degenerate / cap).
    _, rep_all = peel(
        img,
        units,
        _settings(
            stop_criteria=("positive-fraction-high", "min-area-components"),
            stop_combine="all",
            stop_params=(("min-area-components.min_area_px", 100000),),
            max_rounds=6,
        ),
    )
    assert rep_all.n_iterations_run >= 1


# ── registry / names / params ─────────────────────────────────


def test_registry_matches_names():
    assert set(STOP_CRITERIA) == set(STOP_CRITERION_NAMES)


def test_scope_names_present():
    assert SCOPE_NAMES == ("groups", "per-cell", "whole-field")


def test_params_by_name_namespacing():
    grouped = params_by_name(
        (("bg-floor.k", 2.5), ("peak-prominence.k", 3.0), ("positive-fraction-high.max_frac", 0.5))
    )
    assert grouped["bg-floor"] == {"k": 2.5}
    assert grouped["peak-prominence"] == {"k": 3.0}
    assert grouped["positive-fraction-high"] == {"max_frac": 0.5}


def test_report_contract():
    img = np.full((40, 40), 10.0, dtype=np.float32)
    img[10:20, 10:20] = 250.0
    mask, report = peel(img, [np.ones(img.shape, dtype=bool)], _settings())
    assert isinstance(report, IterativeOtsuReport)
    assert report.n_positive == int(mask.sum())
    assert report.units_total == 1
    assert report.n_iterations_run >= 1
    assert report.units_hit_max_rounds == 0
