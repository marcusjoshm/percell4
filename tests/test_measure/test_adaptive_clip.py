"""Tests for the whole-frame adaptive runner + auto-window estimator (U1)."""

from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import disk

from percell4.domain.measure.adaptive_clip import (
    AUTO_WINDOW_MAX,
    AUTO_WINDOW_MIN,
    PARTICLE_WINDOW_MIN,
    assess_particle_sizes_per_cell,
    auto_window,
    detect_adaptive_by_particle_size,
    detect_adaptive_multiscale,
    detect_adaptive_per_cell,
    detect_adaptive_whole_frame,
    detect_smallest_particle_um,
    estimate_adaptive_window,
    multiscale_windows,
    otsu_first_pass,
    otsu_smallest_particle,
    per_cell_sigma,
    pooled_sigma,
    resolve_min_area_px,
    resolve_window_px,
    window_min_spot_for_particle,
)
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.workflows.models import PunctaDetectorSettings


def _adaptive_settings(window_px: int = 15, k: float = 2.25) -> PunctaDetectorSettings:
    return PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name="otsu",
        background_estimator_name="gaussian-peak",
        detector_params={"window_px": window_px, "k": k},
        min_spot_px=3,
        spot_scale_prior=(1.0, 4.0),
    )


def _blobs_image(centers, radius, *, bg=10.0, fg=200.0, shape=(200, 200), seed=0):
    rng = np.random.default_rng(seed)
    img = bg + rng.normal(0.0, 1.5, size=shape).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


def _disk_mask(centers, radius, shape=(300, 300)) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        mask[rr, cc] = True
    return mask


# ── detect_adaptive_whole_frame ──────────────────────────────────────────

def test_detect_whole_frame_marks_blobs_and_is_binary():
    centers = [(50, 50), (50, 150), (150, 100)]
    img = _blobs_image(centers, radius=6)
    mask = detect_adaptive_whole_frame(img, 1.0, _adaptive_settings())

    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    assert mask.shape == img.shape
    # Each blob center is detected; a far background corner is not.
    for cy, cx in centers:
        assert mask[cy, cx] == 1
    assert mask[2, 2] == 0
    # Foreground is a small fraction of the frame (blobs, not the whole image).
    assert mask.mean() < 0.2


def test_detect_whole_frame_constant_image_is_empty():
    img = np.full((64, 64), 12.0, dtype=np.float32)
    mask = detect_adaptive_whole_frame(img, 1.0, _adaptive_settings())
    assert mask.dtype == np.uint8
    assert int(mask.sum()) == 0


# ── otsu_first_pass ──────────────────────────────────────────────────────

def test_otsu_first_pass_marks_bright_regions():
    img = _blobs_image([(40, 40), (40, 90)], radius=8)
    mask = otsu_first_pass(img)
    assert mask.dtype == bool
    assert mask[40, 40] and mask[40, 90]
    assert not mask[2, 2]


def test_otsu_first_pass_constant_returns_empty():
    assert otsu_first_pass(np.full((32, 32), 5.0, dtype=np.float32)).sum() == 0


# ── estimate_adaptive_window ─────────────────────────────────────────────

def test_estimate_window_is_odd_and_in_range():
    win = estimate_adaptive_window(_disk_mask([(60, 60), (60, 180), (180, 120)], radius=8))
    assert win % 2 == 1
    assert AUTO_WINDOW_MIN <= win <= AUTO_WINDOW_MAX


def test_estimate_window_scales_with_granule_size():
    small = estimate_adaptive_window(_disk_mask([(50, 50), (50, 150)], radius=4))
    large = estimate_adaptive_window(_disk_mask([(80, 80), (80, 200)], radius=16))
    assert large > small  # bigger granules -> bigger window
    assert small % 2 == 1 and large % 2 == 1


def test_estimate_window_empty_mask_returns_floor():
    win = estimate_adaptive_window(np.zeros((50, 50), dtype=bool))
    assert win == (AUTO_WINDOW_MIN | 1)


def test_estimate_window_only_noise_returns_floor():
    # A few single-pixel specks below the noise floor -> no usable granules.
    mask = np.zeros((50, 50), dtype=bool)
    mask[10, 10] = mask[20, 30] = True
    assert estimate_adaptive_window(mask, noise_floor_px=3) == (AUTO_WINDOW_MIN | 1)


def test_estimate_window_clamped_to_max():
    # One huge granule would exceed the cap; result is clamped and odd.
    win = estimate_adaptive_window(_disk_mask([(300, 300)], radius=160, shape=(640, 640)))
    assert win == AUTO_WINDOW_MAX
    assert win % 2 == 1


# ── auto_window orchestrator (registry dispatch) ─────────────────────────


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_auto_window_otsu_mean_matches_legacy_estimator(seed):
    """The 'otsu-mean' finder via auto_window reproduces the legacy estimator.

    Characterization parity (registered-analysis Rule 2): the bake-off baseline
    must be byte-identical to today's estimate_adaptive_window(otsu_first_pass)
    so the bake-off measures a faithful baseline, not a re-implementation drift.
    """
    img = _blobs_image([(50, 50), (50, 150), (150, 100)], radius=7, seed=seed)
    gs = 1.0
    smoothed = apply_gaussian_smoothing(img.astype(np.float32), gs)
    legacy = estimate_adaptive_window(otsu_first_pass(smoothed))
    new = auto_window(img, gs, _adaptive_settings(), method="otsu-mean")
    assert new == legacy


def test_auto_window_is_odd_and_in_range():
    img = _blobs_image([(60, 60), (60, 180), (180, 120)], radius=8)
    win = auto_window(img, 1.0, _adaptive_settings(), method="otsu-mean")
    assert win % 2 == 1
    assert AUTO_WINDOW_MIN <= win <= AUTO_WINDOW_MAX


def test_auto_window_constant_image_returns_floor():
    img = np.full((64, 64), 12.0, dtype=np.float32)
    assert auto_window(img, 1.0, _adaptive_settings(), method="otsu-mean") == (AUTO_WINDOW_MIN | 1)


def test_auto_window_respects_clamp_args():
    img = _blobs_image([(50, 50), (50, 150)], radius=8)
    # A tight hi clamps the result; still odd.
    win = auto_window(img, 1.0, _adaptive_settings(), method="otsu-mean", lo=11, hi=21)
    assert win <= 21 and win % 2 == 1


# ── resolve_min_area_px ──────────────────────────────────────────────────

def test_resolve_min_area_px_passthrough():
    assert resolve_min_area_px(9, "px", None) == 9
    assert resolve_min_area_px(9.6, "px", 0.5) == 10  # rounds


def test_resolve_min_area_px_um2_conversion():
    # 1 µm² at 0.5 µm/px -> 1 / 0.25 = 4 px.
    assert resolve_min_area_px(1.0, "um2", 0.5) == 4


def test_resolve_min_area_px_um2_without_calibration_raises():
    with pytest.raises(ValueError):
        resolve_min_area_px(1.0, "um2", None)
    with pytest.raises(ValueError):
        resolve_min_area_px(1.0, "um2", 0.0)


def test_resolve_min_area_px_unknown_unit_raises():
    with pytest.raises(ValueError):
        resolve_min_area_px(1.0, "inches", 0.5)


# ── particle-size knob: window_min_spot_for_particle ─────────────────────

def test_window_min_spot_for_particle_eye_validated_values():
    # The two condensate types validated 2026-06-12 @ 0.120369 µm/px.
    # Stress granules: d_min 0.40 µm -> 21 px window, 9 px size filter (ON).
    assert window_min_spot_for_particle(0.40, 0.120369) == (21, 9)
    # P-bodies: d_min 0.14 µm -> 7 px window, 1 px size filter (OFF: keeps all).
    assert window_min_spot_for_particle(0.14, 0.120369) == (7, 1)


def test_window_min_spot_for_particle_scales_with_pixel_size():
    # Same physical d_min at a 2x coarser pixel -> ~half the pixel window.
    w_fine, _ = window_min_spot_for_particle(0.40, 0.120369)
    w_coarse, ms_coarse = window_min_spot_for_particle(0.40, 0.240738)
    assert w_coarse < w_fine
    assert w_coarse % 2 == 1  # always odd
    # min_spot scales with area (1/px^2): ~4x smaller at 2x coarser px.
    assert ms_coarse == 2


def test_window_min_spot_for_particle_window_floor_and_oddness():
    # A sub-resolution d_min cannot drive the window below the self-subtraction
    # floor, and the window is always odd.
    w, ms = window_min_spot_for_particle(0.01, 0.120369)
    assert w == PARTICLE_WINDOW_MIN
    assert w % 2 == 1
    assert ms == 1  # area of a 0.01 µm particle is sub-pixel -> filter OFF


def test_window_min_spot_for_particle_rejects_bad_inputs():
    with pytest.raises(ValueError):
        window_min_spot_for_particle(0.40, 0.0)
    with pytest.raises(ValueError):
        window_min_spot_for_particle(0.0, 0.12)


# ── particle-size knob: detect_adaptive_by_particle_size ─────────────────

def _two_cells_different_scales():
    """Two disk cells whose intensity scale differs ~6x, each with one blob.

    The per-cell σ is the whole point: a single k must light up the blob in the
    dim cell AND the bright cell despite their different noise scales.
    """
    shape = (160, 200)
    rng = np.random.default_rng(7)
    img = np.zeros(shape, dtype=np.float32)
    labels = np.zeros(shape, dtype=np.int32)
    # Cell 1 (dim): bg 20, noise 2, blob +40.
    c1 = _disk_mask([(80, 55)], 45, shape=shape)
    img[c1] = 20.0 + rng.normal(0.0, 2.0, size=int(c1.sum())).astype(np.float32)
    labels[c1] = 1
    # Cell 2 (bright): bg 120, noise 12, blob +240.
    c2 = _disk_mask([(80, 150)], 45, shape=shape)
    img[c2] = 120.0 + rng.normal(0.0, 12.0, size=int(c2.sum())).astype(np.float32)
    labels[c2] = 2
    for cy, cx, fg in [(80, 55, 60.0), (80, 150, 360.0)]:
        rr, cc = disk((cy, cx), 5, shape=shape)
        img[rr, cc] = fg
    return img, labels, (80, 55), (80, 150)


def test_detect_by_particle_size_is_binary_and_per_cell():
    img, labels, blob1, blob2 = _two_cells_different_scales()
    mask = detect_adaptive_by_particle_size(img, labels, 0.120369, 0.40)

    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    assert mask.shape == img.shape
    # Both blobs detected despite the ~6x intensity-scale gap (per-cell σ).
    assert mask[blob1] == 1
    assert mask[blob2] == 1
    # Nothing detected outside the cells, and the dilute phase stays mostly off.
    assert mask[2, 2] == 0
    assert mask.mean() < 0.15


def test_filter_by_area_keeps_components_at_or_above_min_spot():
    # The size filter the d_min knob drives: keep area >= min_spot (so a particle
    # exactly the smallest size is KEPT), drop smaller specks, no-op when off.
    from percell4.domain.measure.adaptive_clip import _filter_by_area

    m = np.zeros((20, 20), dtype=bool)
    m[2:5, 2:5] = True  # 3x3 = 9 px component
    m[10, 10] = True  # 1 px speck

    off = _filter_by_area(m, 1)  # min_spot 1 -> filter OFF, both survive
    assert off[3, 3] and off[10, 10]

    keep9 = _filter_by_area(m, 9)  # keep area >= 9: block stays, speck goes
    assert keep9[3, 3] and not keep9[10, 10]

    keep10 = _filter_by_area(m, 10)  # 9-block now below threshold -> removed
    assert not keep10[3, 3]


def test_filter_by_area_uses_4_connectivity():
    # Diagonal-touching pixels are SEPARATE components (matches the eye-validated
    # remove_small_objects path), so two diagonal singletons are each < 2 and drop.
    from percell4.domain.measure.adaptive_clip import _filter_by_area

    m = np.zeros((10, 10), dtype=bool)
    m[3, 3] = m[4, 4] = True  # diagonal neighbors
    assert _filter_by_area(m, 2).sum() == 0


def test_detect_by_particle_size_empty_labels_is_empty():
    img = _blobs_image([(50, 50)], radius=6)
    labels = np.zeros(img.shape, dtype=np.int32)
    mask = detect_adaptive_by_particle_size(img, labels, 0.120369, 0.40)
    assert mask.dtype == np.uint8
    assert int(mask.sum()) == 0


# ── detect_smallest_particle_um (Otsu first-pass d_min seed) ──────────────


def test_detect_smallest_particle_um_returns_min_diameter():
    """Returns the SMALLEST component's equivalent Ø (µm), not the mean."""
    # Two well-separated discs: radius 3 (Ø 6 px) and radius 10 (Ø 20 px).
    img = _blobs_image([(40, 40), (140, 140)], radius=3, shape=(200, 200))
    rr, cc = disk((140, 140), 10, shape=img.shape)
    img[rr, cc] = 200.0
    px = 0.1
    d_um = detect_smallest_particle_um(img, 1.0, px, noise_floor_px=3)
    # The smaller disc (Ø ≈ 6 px) drives it, not the larger one or their mean.
    expected_small_px = 2.0 * np.sqrt((np.pi * 3.0**2) / np.pi)  # = 6 px
    assert d_um == pytest.approx(expected_small_px * px, rel=0.25)
    # Strictly smaller than the mean-diameter the otsu-mean baseline would give.
    assert d_um < estimate_adaptive_window(otsu_first_pass(img)) * px


def test_detect_smallest_particle_um_restricts_to_cp_mask():
    """A small out-of-cell particle is ignored when a cell mask is supplied."""
    img = _blobs_image([(100, 100)], radius=10, shape=(200, 200))  # big disc, in cell
    # A smaller real particle OUTSIDE any cell — the global smallest if unmasked.
    rr, cc = disk((30, 30), 3, shape=img.shape)
    img[rr, cc] = 200.0
    cell = np.zeros(img.shape, dtype=bool)
    rr, cc = disk((100, 100), 40, shape=img.shape)
    cell[rr, cc] = True  # covers the big disc, not the small out-of-cell particle
    px = 0.12
    d_in_cell = detect_smallest_particle_um(img, 1.0, px, cp_mask=cell, noise_floor_px=3)
    d_whole = detect_smallest_particle_um(img, 1.0, px, noise_floor_px=3)
    # Whole-frame is dragged down by the out-of-cell particle; in-cell is not.
    assert d_whole < d_in_cell


def test_detect_smallest_particle_um_none_on_constant():
    flat = np.full((64, 64), 5.0, dtype=np.float32)
    assert detect_smallest_particle_um(flat, 1.0, 0.1) is None


def test_detect_smallest_particle_um_none_when_only_specks():
    """Sub-noise-floor components leave nothing -> None (caller keeps its value)."""
    img = _blobs_image([(50, 50)], radius=1, shape=(100, 100))  # ~3-4 px speck
    assert detect_smallest_particle_um(img, 1.0, 0.1, noise_floor_px=50) is None


def test_detect_smallest_particle_um_requires_positive_pixel_size():
    img = _blobs_image([(50, 50)], radius=5, shape=(100, 100))
    with pytest.raises(ValueError):
        detect_smallest_particle_um(img, 1.0, 0.0)
    with pytest.raises(ValueError):
        detect_smallest_particle_um(img, 1.0, None)


def test_detect_smallest_particle_um_scales_with_pixel_size():
    """Same image at 2× the µm/px yields 2× the diameter (physical units)."""
    img = _blobs_image([(60, 60)], radius=6, shape=(160, 160))
    d1 = detect_smallest_particle_um(img, 1.0, 0.10)
    d2 = detect_smallest_particle_um(img, 1.0, 0.20)
    assert d2 == pytest.approx(2.0 * d1, rel=1e-6)


def test_detect_smallest_particle_um_constant_in_cell_returns_none():
    """In-cell pixels constant (bright outside) -> degenerate -> None.

    Guards the boundary-bleed fix: out-of-cell brightness must not smear inward
    across the mask edge and fabricate a spurious above-threshold rim.
    """
    img = np.full((200, 200), 200.0, dtype=np.float32)  # bright everywhere
    cell = np.zeros(img.shape, dtype=bool)
    rr, cc = disk((100, 100), 30, shape=img.shape)
    cell[rr, cc] = True
    img[cell] = 50.0  # constant (dimmer) inside the cell; bright outside
    assert detect_smallest_particle_um(img, 1.0, 0.1, cp_mask=cell) is None


def test_otsu_smallest_particle_report_fields():
    """The report carries the threshold + percentile diameter + threshold-area stats."""
    img = _blobs_image([(40, 40), (140, 140)], radius=3, shape=(200, 200))
    rr, cc = disk((140, 140), 10, shape=img.shape)
    img[rr, cc] = 200.0
    r = otsu_smallest_particle(img, 1.0, 0.1, noise_floor_px=3)  # percentile defaults to 0
    assert r is not None
    assert r.n_components == 2
    assert r.scope == "whole-frame"
    assert r.percentile == 0.0
    # At percentile 0 the smaller disc (radius 3, Ø ~6 px) drives it, not the radius-10.
    assert r.diameter_px < 12.0
    assert r.d_min_um == pytest.approx(r.diameter_px * 0.1)
    # Threshold-area intensities sit above the Otsu threshold; ordering holds.
    assert r.area_min > r.otsu_threshold
    assert r.area_max >= r.area_mean >= r.area_min
    assert r.mean_minus_threshold == pytest.approx(r.area_mean - r.otsu_threshold)
    # detect_smallest_particle_um delegates to the percentile-0 (smallest) d_min_um.
    assert detect_smallest_particle_um(img, 1.0, 0.1, noise_floor_px=3) == pytest.approx(r.d_min_um)


def test_otsu_smallest_particle_percentile_raises_the_size():
    """A higher percentile picks a larger particle than the absolute smallest."""
    # One tiny fragment + several mid-size discs: p0 catches the fragment, p50 ignores it.
    img = _blobs_image([(40, 40)], radius=2, shape=(220, 220))  # tiny fragment
    for cy, cx in [(40, 120), (120, 40), (120, 120), (180, 180)]:
        rr, cc = disk((cy, cx), 9, shape=img.shape)
        img[rr, cc] = 200.0
    r0 = otsu_smallest_particle(img, 1.0, 0.1, noise_floor_px=3, percentile=0.0)
    r50 = otsu_smallest_particle(img, 1.0, 0.1, noise_floor_px=3, percentile=50.0)
    assert r50.diameter_px > r0.diameter_px
    assert r50.percentile == 50.0
    # The percentile is clamped to [0, 100].
    assert otsu_smallest_particle(img, 1.0, 0.1, percentile=150.0).percentile == 100.0


def test_otsu_smallest_particle_none_on_constant():
    flat = np.full((64, 64), 5.0, dtype=np.float32)
    assert otsu_smallest_particle(flat, 1.0, 0.1) is None


def test_otsu_smallest_particle_scope_label_with_mask():
    img = _blobs_image([(100, 100)], radius=8, shape=(200, 200))
    cell = np.zeros(img.shape, dtype=bool)
    rr, cc = disk((100, 100), 40, shape=img.shape)
    cell[rr, cc] = True
    assert otsu_smallest_particle(img, 1.0, 0.12, cp_mask=cell).scope == "in-cell"


# ── resolve_window_px (manual px/µm window) ──────────────────────────────


def test_resolve_window_px_px_is_odd_and_floored():
    assert resolve_window_px(20, "px", None) == 21       # forced odd
    assert resolve_window_px(15, "px", None) == 15       # already odd
    assert resolve_window_px(1, "px", None) == PARTICLE_WINDOW_MIN  # floored at 3


def test_resolve_window_px_um_conversion():
    # 1.8 µm at 0.12 µm/px = 15 px (odd).
    assert resolve_window_px(1.8, "um", 0.12) == 15
    # Scales with pixel size: the same µm at 2x coarser pixel -> ~half the px.
    assert resolve_window_px(1.8, "um", 0.24) < resolve_window_px(1.8, "um", 0.12)


def test_resolve_window_px_um_without_pixel_size_raises():
    with pytest.raises(ValueError):
        resolve_window_px(1.8, "um", None)
    with pytest.raises(ValueError):
        resolve_window_px(1.8, "um", 0.0)


def test_resolve_window_px_unknown_unit_raises():
    with pytest.raises(ValueError):
        resolve_window_px(15, "inches", 0.12)


# ── detect_adaptive_per_cell (explicit-window per-cell core) ─────────────


def test_detect_adaptive_per_cell_marks_blobs_in_cell():
    img = _blobs_image([(50, 50), (50, 150), (150, 100)], radius=6)
    labels = np.ones(img.shape, dtype=np.int32)  # one cell covering the frame
    mask = detect_adaptive_per_cell(img, labels, window_px=21, min_spot_px=3, k=1.0)
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    for cy, cx in [(50, 50), (50, 150), (150, 100)]:
        assert mask[cy, cx] == 1
    assert mask[2, 2] == 0


def test_detect_adaptive_per_cell_matches_particle_size_engine():
    """The d_min engine delegates to the per-cell core with the derived window."""
    img = _blobs_image([(60, 60), (60, 160), (160, 100)], radius=5)
    labels = np.ones(img.shape, dtype=np.int32)
    px, d_min = 0.120369, 0.40
    window_px, min_spot_px = window_min_spot_for_particle(d_min, px)
    via_core = detect_adaptive_per_cell(
        img, labels, window_px=window_px, min_spot_px=min_spot_px, k=1.0
    )
    via_engine = detect_adaptive_by_particle_size(img, labels, px, d_min, k=1.0)
    assert np.array_equal(via_core, via_engine)


# ── global σ mode (pooled noise floor) ───────────────────────────────────


def test_pooled_sigma_matches_manual_mad_over_in_cell_pixels():
    """pooled_sigma is 1.4826·MAD over all labelled pixels of the working image."""
    rng = np.random.default_rng(3)
    work = (10.0 + rng.normal(0.0, 4.0, size=(80, 80))).astype(np.float32)
    labels = np.zeros((80, 80), dtype=np.int32)
    labels[10:40, 10:40] = 1  # two disjoint cells; pixels outside are ignored
    labels[50:70, 50:70] = 2
    vals = work[labels > 0]
    expected = 1.4826 * float(np.median(np.abs(vals - np.median(vals))))
    assert pooled_sigma(work, labels) == pytest.approx(expected)


def test_pooled_sigma_none_on_flat_or_empty_selection():
    flat = np.full((32, 32), 7.0, dtype=np.float32)
    assert pooled_sigma(flat, np.ones((32, 32), np.int32)) is None  # MAD == 0
    noisy = np.random.default_rng(0).normal(0, 1, (32, 32)).astype(np.float32)
    assert pooled_sigma(noisy, np.zeros((32, 32), np.int32)) is None  # no cell pixels


def test_detect_adaptive_per_cell_global_marks_blobs_and_is_binary():
    img = _blobs_image([(50, 50), (50, 150), (150, 100)], radius=6)
    labels = np.ones(img.shape, dtype=np.int32)
    mask = detect_adaptive_per_cell(
        img, labels, window_px=21, min_spot_px=3, k=1.0, global_sigma=True
    )
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    for cy, cx in [(50, 50), (50, 150), (150, 100)]:
        assert mask[cy, cx] == 1


def test_global_sigma_thresholds_a_cell_that_per_cell_omits():
    """A flat quiet cell has MAD 0 (per-cell omits it), but the pooled σ from a
    noisy neighbour gives global mode a finite floor, so its blob is detected."""
    img = np.full((120, 240), 10.0, dtype=np.float32)
    # Cell 1 (left): perfectly flat except one bright disk → per-cell MAD == 0.
    rr, cc = disk((60, 60), 8, shape=img.shape)
    img[rr, cc] = 200.0
    # Cell 2 (right): noisy background, no blob → supplies a finite pooled σ.
    rng = np.random.default_rng(1)
    img[:, 120:] = 10.0 + rng.normal(0.0, 6.0, size=(120, 120)).astype(np.float32)
    labels = np.zeros(img.shape, dtype=np.int32)
    labels[:, :120] = 1
    labels[:, 120:] = 2

    per_cell = detect_adaptive_per_cell(img, labels, window_px=21, min_spot_px=3, k=1.0)
    glob = detect_adaptive_per_cell(
        img, labels, window_px=21, min_spot_px=3, k=1.0, global_sigma=True
    )
    assert per_cell[60, 60] == 0  # cell 1 omitted: its MAD is 0
    assert glob[60, 60] == 1      # global pooled σ gives it a finite floor


def test_detect_by_particle_size_threads_global_sigma():
    img = _blobs_image([(60, 60), (60, 160), (160, 100)], radius=5)
    labels = np.ones(img.shape, dtype=np.int32)
    px, d_min = 0.120369, 0.40
    window_px, min_spot_px = window_min_spot_for_particle(d_min, px)
    via_core = detect_adaptive_per_cell(
        img, labels, window_px=window_px, min_spot_px=min_spot_px, k=1.0, global_sigma=True
    )
    via_engine = detect_adaptive_by_particle_size(
        img, labels, px, d_min, k=1.0, global_sigma=True
    )
    assert np.array_equal(via_core, via_engine)


# ── multi-scale routine: assessment + windows + OR-combine ───────────────


def _wide_range_image(shape=(256, 256)):
    """Noise + small (Ø~6), medium (Ø~16) and large (Ø~40) discs in one frame."""
    img = (10.0 + np.random.RandomState(0).normal(0, 1.5, shape)).astype(np.float32)
    for c, r in [((40, 40), 3), ((40, 120), 8), ((150, 150), 20)]:
        rr, cc = disk(c, r, shape=shape)
        img[rr, cc] = 200.0
    return img


def test_assess_particle_sizes_per_cell_reports_range():
    img = _wide_range_image()
    labels = np.ones(img.shape, dtype=np.int32)
    rep = assess_particle_sizes_per_cell(img, labels, 1.0)
    assert rep is not None
    assert rep.n_raw == 3
    assert rep.raw_min_px < 8.0          # the small disc (Ø ~6 px)
    assert rep.raw_max_px > 30.0         # the large disc (Ø ~40 px)
    assert rep.smallest_px <= rep.mean_px <= rep.largest_px
    assert rep.range_px == pytest.approx(rep.largest_px - rep.smallest_px)


def test_assess_particle_sizes_cutoff_floors_stats_but_keeps_raw():
    img = _wide_range_image()
    labels = np.ones(img.shape, dtype=np.int32)
    # Cutoff above the small disc: it drops out of the (non-raw) stats.
    rep = assess_particle_sizes_per_cell(img, labels, 1.0, cutoff_px=10.0)
    assert rep.raw_min_px < 8.0          # raw min is still the small disc
    assert rep.smallest_px >= 10.0       # but the post-cutoff smallest is floored
    assert rep.n_particles == 2          # small disc excluded from the stats


def test_assess_particle_sizes_none_when_empty():
    flat = np.full((64, 64), 5.0, dtype=np.float32)
    assert assess_particle_sizes_per_cell(flat, np.ones((64, 64), np.int32), 1.0) is None


def test_multiscale_windows_doubles_until_past_largest():
    assert multiscale_windows(11, 39.8) == [11, 23, 47]   # stop after first > 39.8
    assert multiscale_windows(1, 5) == [3, 7]             # floored at 3, then > 5
    # A single pass when the start already exceeds the largest particle.
    assert multiscale_windows(51, 40) == [51]
    # Backstop caps the pass count even for a degenerate max.
    assert len(multiscale_windows(3, 1e9, max_passes=5)) == 5


def test_detect_adaptive_multiscale_fills_small_and_large():
    """The OR-combine fills BOTH the small and the large particle (a single small
    window would hollow out the large one)."""
    img = _wide_range_image()
    labels = np.ones(img.shape, dtype=np.int32)
    rep = assess_particle_sizes_per_cell(img, labels, 1.0)
    start = max(3, int(round(0.5 * rep.mean_px)) | 1)
    mask, windows = detect_adaptive_multiscale(
        img, labels, start_window_px=start, max_particle_px=rep.largest_px
    )
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    assert len(windows) >= 2 and windows == sorted(windows)
    assert mask[150, 150] == 1   # large disc center filled
    assert mask[40, 40] == 1     # small disc filled
    # Union ⊇ any single pass (OR-combine only adds foreground).
    single = detect_adaptive_per_cell(img, labels, window_px=windows[0], min_spot_px=1)
    assert int(mask.sum()) >= int(single.sum())


def test_multiscale_windows_force_passes_overrides_stop():
    # force_passes = N -> exactly N windows, ignoring max_particle.
    assert multiscale_windows(11, 39.8, force_passes=5) == [11, 23, 47, 95, 191]
    assert multiscale_windows(11, 1e9, force_passes=2) == [11, 23]
    # 0 / None -> auto stop at the largest particle.
    assert multiscale_windows(11, 39.8, force_passes=0) == [11, 23, 47]
    assert multiscale_windows(11, 39.8, force_passes=None) == [11, 23, 47]


def test_detect_adaptive_multiscale_force_passes():
    img = _wide_range_image()
    labels = np.ones(img.shape, dtype=np.int32)
    # A tiny max_particle would auto-stop early; force_passes runs exactly N.
    _, windows = detect_adaptive_multiscale(
        img, labels, start_window_px=7, max_particle_px=12, force_passes=4
    )
    assert len(windows) == 4


def test_assess_particle_sizes_rejects_near_constant_cell():
    """A near-constant cell (float32-epsilon gradient) is degenerate, not a
    spurious cell-sized particle — the guard matches otsu_smallest_particle's
    relative tolerance, not exact equality (review consistency fix)."""
    shape = (128, 128)
    labels = np.zeros(shape, dtype=np.int32)
    labels[disk((30, 30), 20, shape=shape)] = 1   # a real cell
    labels[disk((90, 90), 25, shape=shape)] = 2   # a near-constant (empty) cell
    yy, _ = np.mgrid[0:128, 0:128]
    img = np.full(shape, 100.0, dtype=np.float32)
    img[disk((30, 30), 5, shape=shape)] = 300.0   # one real particle in cell 1
    # Faint float32 ramp in cell 2: vmin != vmax exactly (exact guard would admit
    # and Otsu would carve it) but the span is far below the rel-tol (it must not).
    img[labels == 2] = (100.0 + 1e-6 * yy.astype(np.float32))[labels == 2]
    rep = assess_particle_sizes_per_cell(img, labels, 1.0)
    assert rep is not None
    assert rep.n_raw == 1  # only the real particle; the empty cell contributes none


def test_detect_adaptive_multiscale_min_spot_filters_combined():
    """min_spot_px filters the OR-combined mask (a no-op at 1; drops small at N)."""
    img = _wide_range_image()
    labels = np.ones(img.shape, dtype=np.int32)
    unfiltered, _ = detect_adaptive_multiscale(
        img, labels, start_window_px=7, max_particle_px=40, min_spot_px=1
    )
    filtered, _ = detect_adaptive_multiscale(
        img, labels, start_window_px=7, max_particle_px=40, min_spot_px=500
    )
    assert int(filtered.sum()) < int(unfiltered.sum())  # big filter drops components
    # The big disc (area ~1257 px) survives a 500 px filter; tiny specks do not.
    assert filtered[150, 150] == 1


# --------------------------------------------------------------------------- #
# per_cell_sigma helper (U2) — shared robust per-cell noise scale
# --------------------------------------------------------------------------- #
def test_per_cell_sigma_returns_one_entry_per_cell():
    """Each labelled cell gets 1.4826*MAD of its (smoothed) in-cell values."""
    shape = (64, 64)
    labels = np.zeros(shape, dtype=np.int32)
    labels[disk((16, 16), 10, shape=shape)] = 1
    labels[disk((48, 48), 10, shape=shape)] = 2
    rng = np.random.RandomState(0)
    work = rng.normal(100.0, 5.0, shape).astype(np.float32)
    sig = per_cell_sigma(work, labels)
    assert set(sig) == {1, 2}
    for cid in (1, 2):
        vals = work[labels == cid]
        expect = 1.4826 * float(np.median(np.abs(vals - np.median(vals))))
        assert sig[cid] == pytest.approx(expect)
        assert sig[cid] > 0.0


def test_per_cell_sigma_omits_flat_cell():
    """A constant (MAD == 0) cell is omitted — it cannot define a threshold."""
    shape = (64, 64)
    labels = np.zeros(shape, dtype=np.int32)
    labels[disk((16, 16), 10, shape=shape)] = 1   # flat
    labels[disk((48, 48), 10, shape=shape)] = 2   # has spread
    work = np.full(shape, 100.0, dtype=np.float32)
    work[labels == 2] = np.random.RandomState(1).normal(100.0, 5.0, int((labels == 2).sum()))
    sig = per_cell_sigma(work, labels)
    assert 1 not in sig          # flat cell omitted
    assert 2 in sig and sig[2] > 0.0


def test_per_cell_sigma_skips_label_gap():
    """A gap in the label ids (no pixels for an id) is skipped, not crashed."""
    shape = (64, 64)
    labels = np.zeros(shape, dtype=np.int32)
    labels[disk((16, 16), 10, shape=shape)] = 1
    labels[disk((48, 48), 10, shape=shape)] = 3   # id 2 has no pixels
    work = np.random.RandomState(2).normal(100.0, 5.0, shape).astype(np.float32)
    sig = per_cell_sigma(work, labels)
    assert set(sig) == {1, 3}    # id 2 absent, no error


def test_detect_adaptive_per_cell_unchanged_after_sigma_refactor():
    """Characterization: the detector output is the same quantity per-cell σ feeds.

    Detection thresholds ``diff > k*σ`` with σ from :func:`per_cell_sigma` on the
    presmoothed ``work``; recomputing σ the same way and reproducing the mask by
    hand must match the production detector exactly (guards the U2 extraction).
    """
    from scipy.ndimage import gaussian_filter

    shape = (96, 96)
    labels = np.zeros(shape, dtype=np.int32)
    labels[disk((28, 28), 18, shape=shape)] = 1
    labels[disk((68, 68), 18, shape=shape)] = 2
    rng = np.random.RandomState(7)
    img = rng.normal(100.0, 4.0, shape).astype(np.float32)
    img[disk((28, 28), 4, shape=shape)] += 120.0   # bright spot in cell 1
    img[disk((68, 68), 4, shape=shape)] += 120.0   # bright spot in cell 2

    window_px, k = 15, 1.0
    produced = detect_adaptive_per_cell(
        img, labels, window_px=window_px, min_spot_px=1, k=k, presmooth_sigma_px=1.0
    )

    # Reproduce by hand from the shared helper.
    work = apply_gaussian_smoothing(img, 1.0)
    diff = work - gaussian_filter(work, (window_px - 1) / 6.0)
    sig = per_cell_sigma(work, labels)
    expected = np.zeros(shape, dtype=bool)
    for cid, s in sig.items():
        cell = labels == cid
        expected |= (diff > k * s) & cell
    assert np.array_equal(produced.astype(bool), expected)


# --------------------------------------------------------------------------- #
# detect_adaptive_per_cell fill_holes (AE-U1)
# --------------------------------------------------------------------------- #
def test_detect_adaptive_per_cell_fill_holes_closes_rings():
    """A large particle under-windowed into a ring is closed solid with fill_holes."""
    shape = (160, 160)
    labels = np.ones(shape, dtype=np.int32)
    rng = np.random.RandomState(4)
    img = rng.normal(100.0, 3.0, shape).astype(np.float32)
    # A big flat-topped disc: a small window detects only its rim (holes out).
    img[disk((80, 80), 22, shape=shape)] = 260.0
    center = (80, 80)

    hollow = detect_adaptive_per_cell(
        img, labels, window_px=11, min_spot_px=1, k=1.0, fill_holes=False
    )
    solid = detect_adaptive_per_cell(
        img, labels, window_px=11, min_spot_px=1, k=1.0, fill_holes=True
    )
    # The under-windowed centre is a hole without filling, solid with it.
    assert hollow[center] == 0
    assert solid[center] == 1
    assert int(solid.sum()) > int(hollow.sum())


def test_detect_adaptive_per_cell_fill_holes_does_not_merge_components():
    """fill_holes closes interiors only — two separate spots stay two components."""
    from skimage import measure

    shape = (120, 120)
    labels = np.ones(shape, dtype=np.int32)
    rng = np.random.RandomState(5)
    img = rng.normal(100.0, 3.0, shape).astype(np.float32)
    img[disk((35, 35), 5, shape=shape)] += 400.0
    img[disk((85, 85), 5, shape=shape)] += 400.0

    # k=3 + a size filter isolate the two real spots from noise; fill_holes must
    # not bridge them into one component.
    filled = detect_adaptive_per_cell(
        img, labels, window_px=15, min_spot_px=8, k=3.0, fill_holes=True
    )
    n = measure.label(filled, connectivity=1).max()
    assert n == 2  # two distinct spots, not merged


def test_detect_adaptive_per_cell_default_is_unfilled():
    """Default fill_holes=False matches an explicit no-fill run (back-compat)."""
    shape = (160, 160)
    labels = np.ones(shape, dtype=np.int32)
    rng = np.random.RandomState(6)
    img = rng.normal(100.0, 3.0, shape).astype(np.float32)
    img[disk((80, 80), 22, shape=shape)] = 260.0
    default = detect_adaptive_per_cell(img, labels, window_px=11, min_spot_px=1, k=1.0)
    explicit = detect_adaptive_per_cell(
        img, labels, window_px=11, min_spot_px=1, k=1.0, fill_holes=False
    )
    assert np.array_equal(default, explicit)
