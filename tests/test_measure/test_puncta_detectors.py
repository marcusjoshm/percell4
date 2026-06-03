"""Tests for the detector registry (plan U3).

Mirrors ``tests/test_measure/test_thresholding.py`` conventions. The synthetic
fixtures build float images with Gaussian spots of varying sigma/amplitude on a
flat-zero residual, plus a boolean ``group_mask``; out-of-group pixels are set
to ``NaN`` to mimic the U4 pipeline's per-group isolation contract.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.domain.measure.puncta_detectors import DETECTOR_NAMES, DETECTORS

# Detectors that take a real signal and are expected to fire on bright spots.
# (``atrous-wavelet`` is a stub; tested separately for the raise.)
_REAL_DETECTORS = ["otsu", "bg-k-sigma", "white-tophat", "log", "dog", "h-maxima"]
_BLOB_DETECTORS = ["log", "dog"]

# Default detector params (the pipeline forwards the calibrated scale range).
_BLOB_PARAMS = {"min_sigma": 1.0, "max_sigma": 4.0, "threshold_rel": 0.1}


# ── Fixtures / builders ───────────────────────────────────────


def _add_spot(img, y, x, amp, sigma):
    """Add an isotropic Gaussian spot of peak ``amp``, width ``sigma`` at (y, x)."""
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
    img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2.0 * sigma**2))


# (y, x, amplitude, sigma) — mixed bright + dim, mixed size. The three dim
# spots (amp ~ 8-12) are the ones a global Otsu threshold drops.
_MIXED_SPOTS = [
    (20, 20, 60.0, 2.0),
    (20, 90, 12.0, 1.5),  # dim
    (60, 45, 40.0, 3.0),
    (95, 95, 8.0, 2.5),  # dim
    (50, 105, 25.0, 2.0),
    (100, 30, 10.0, 1.5),  # dim
]


def _mixed_residual(shape=(120, 120)):
    """Flat-zero residual carrying the mixed bright+dim spot set (no noise)."""
    img = np.zeros(shape, dtype=float)
    for y, x, amp, sigma in _MIXED_SPOTS:
        _add_spot(img, y, x, amp, sigma)
    return img


def _recall(mask, centers, tol=2):
    """Fraction of ``centers`` with a painted pixel within ``tol`` px."""
    hits = 0
    for y, x, *_ in centers:
        sub = mask[max(0, y - tol) : y + tol + 1, max(0, x - tol) : x + tol + 1]
        if sub.any():
            hits += 1
    return hits / len(centers)


def _isolate(img, group_mask):
    """Apply the caller's isolation contract: out-of-group pixels → NaN."""
    iso = np.array(img, dtype=float, copy=True)
    iso[~group_mask] = np.nan
    return iso


# ── Registry / names drift guard ──────────────────────────────


def test_registry_keys_match_names():
    """DETECTORS keys are exactly DETECTOR_NAMES, including the atrous stub."""
    assert set(DETECTORS) == set(DETECTOR_NAMES)
    assert "atrous-wavelet" in DETECTORS


def test_expected_keys_present():
    expected = {"otsu", "bg-k-sigma", "white-tophat", "log", "dog", "h-maxima", "atrous-wavelet"}
    assert set(DETECTORS) == expected


# ── Happy path: multiscale recovers dim foci; Otsu misses them ─


@pytest.mark.parametrize("name", _BLOB_DETECTORS)
def test_multiscale_recovers_mixed_size_spots(name):
    """log/dog recover >= 90% of mixed bright+dim spot centers (within ~2px)."""
    img = _mixed_residual()
    gmask = np.ones(img.shape, dtype=bool)
    mask = DETECTORS[name](img, gmask, None, _BLOB_PARAMS)
    assert _recall(mask, _MIXED_SPOTS) >= 0.9


def test_otsu_misses_dim_spots_documents_the_win():
    """Baseline global Otsu drops the dim foci that the multiscale default keeps.

    This is the documented motivation for the puncta work: a single global
    threshold cannot capture both bright and dim foci, so Otsu's recall is
    strictly below the multiscale detector's on this mixed field.
    """
    img = _mixed_residual()
    gmask = np.ones(img.shape, dtype=bool)
    otsu_mask = DETECTORS["otsu"](img, gmask, None, {})
    log_mask = DETECTORS["log"](img, gmask, None, _BLOB_PARAMS)
    otsu_recall = _recall(otsu_mask, _MIXED_SPOTS)
    log_recall = _recall(log_mask, _MIXED_SPOTS)
    assert otsu_recall < log_recall  # Otsu loses dim foci
    assert otsu_recall < 0.9  # specifically misses at least one dim spot


# ── bg-k-sigma: exactly residual > k*sigma within the group ───


def test_bg_k_sigma_marks_exactly_above_threshold():
    """bg-k-sigma on a known residual+sigma marks exactly residual > k*sigma."""
    rng = np.random.default_rng(0)
    residual = rng.normal(0.0, 1.0, size=(60, 60))
    residual[10, 10] = 10.0
    residual[40, 40] = 8.0
    gmask = np.ones(residual.shape, dtype=bool)
    sigma = 1.0
    k = 2.5
    mask = DETECTORS["bg-k-sigma"](residual, gmask, sigma, {"k": k})
    expected = (residual > k * sigma) & gmask
    assert np.array_equal(mask.astype(bool), expected)


def test_bg_k_sigma_falls_back_to_robust_sigma_when_none():
    """sigma=None → bg-k-sigma uses 1.4826*MAD of the finite in-group residual."""
    rng = np.random.default_rng(1)
    residual = rng.normal(0.0, 2.0, size=(80, 80))
    residual[30, 30] = 50.0  # unambiguous spike survives any reasonable k*sigma
    gmask = np.ones(residual.shape, dtype=bool)
    mask = DETECTORS["bg-k-sigma"](residual, gmask, None, {"k": 2.5})
    assert mask[30, 30] == 1
    # The robust sigma keeps the mask sparse (not a flood over pure noise).
    assert mask.sum() < residual.size * 0.05


# ── Cross-group isolation (the threshold_rel-normalizes-globally fix) ──


def test_cross_group_isolation_dim_group_unaffected_by_bright_out_of_group():
    """In-group dim foci are recovered regardless of bright OUT-of-group content.

    Builds a residual with a very bright structure OUTSIDE ``group_mask`` and
    dim foci inside it. Under the contract the caller has NaN'd the out-of-group
    pixels, so ``threshold_rel`` normalizes within the group — the bright region
    must not suppress the dim in-group detection.
    """
    img = np.zeros((120, 120), dtype=float)
    dim_centers = [(30, 30, 12.0, 1.5), (70, 40, 10.0, 2.0), (95, 25, 9.0, 1.5)]
    for y, x, amp, sigma in dim_centers:
        _add_spot(img, y, x, amp, sigma)
    _add_spot(img, 60, 90, 500.0, 4.0)  # very bright, OUT of group

    gmask = np.zeros(img.shape, dtype=bool)
    gmask[:, :60] = True  # group is the left half

    iso = _isolate(img, gmask)
    mask = DETECTORS["log"](iso, gmask, None, _BLOB_PARAMS)

    # Dim in-group foci all recovered despite the bright out-of-group blob.
    assert _recall(mask, dim_centers) == 1.0
    # No detection leaks out of the group.
    assert not mask[~gmask].any()


def test_full_field_without_isolation_loses_dim_group_contrast():
    """Documents why isolation matters: a full-field residual lets the bright
    region drag ``threshold_rel`` up and suppress the dim group entirely."""
    img = np.zeros((120, 120), dtype=float)
    dim_centers = [(30, 30, 12.0, 1.5), (70, 40, 10.0, 2.0), (95, 25, 9.0, 1.5)]
    for y, x, amp, sigma in dim_centers:
        _add_spot(img, y, x, amp, sigma)
    _add_spot(img, 60, 90, 500.0, 4.0)
    gmask = np.zeros(img.shape, dtype=bool)
    gmask[:, :60] = True

    # NO isolation (full-field residual handed to the detector).
    mask = DETECTORS["log"](img, gmask, None, _BLOB_PARAMS)
    assert _recall(mask, dim_centers) < 1.0  # bright region suppresses dim foci


# ── NaN block inside the residual: fill-then-restrict, no corruption ──


@pytest.mark.parametrize("name", _REAL_DETECTORS)
def test_internal_nan_block_does_not_corrupt_detection(name):
    """A NaN block away from a spot must not flip the spot's detection.

    Pins the fill-then-restrict discipline: skimage ops are not NaN-safe, so a
    detector that convolved over raw NaN would corrupt pixels across the
    footprint. The detector must fill NaN, detect, then restrict to finite
    in-group pixels.
    """
    img = np.zeros((120, 120), dtype=float)
    _add_spot(img, 30, 30, 60.0, 2.5)  # strong spot, well clear of the hole
    gmask = np.ones(img.shape, dtype=bool)

    clean = DETECTORS[name](img, gmask, None, _BLOB_PARAMS)
    assert clean[28:33, 28:33].any()  # spot detected without the hole

    holed = img.copy()
    holed[80:95, 80:95] = np.nan  # NaN block far from the spot
    holed_mask = DETECTORS[name](holed, gmask, None, _BLOB_PARAMS)

    # No detection inside or bleeding out of the NaN block (restricted to finite).
    assert not holed_mask[80:95, 80:95].any()
    # The spot is still detected with the hole present.
    assert holed_mask[28:33, 28:33].any()
    # Output stays {0,1} uint8 with the hole present.
    assert holed_mask.dtype == np.uint8
    assert set(np.unique(holed_mask)) <= {0, 1}


# ── Signal-free residual → all-zero, no flood ─────────────────


@pytest.mark.parametrize("name", _REAL_DETECTORS)
def test_signal_free_flat_residual_all_zero(name):
    """A pure-flat (~0) residual → every detector returns all-zero, no raise."""
    residual = np.zeros((80, 80), dtype=float)
    gmask = np.ones(residual.shape, dtype=bool)
    mask = DETECTORS[name](residual, gmask, 1.0, _BLOB_PARAMS)
    assert mask.dtype == np.uint8
    assert mask.sum() == 0


# The sigma-aware detectors are the ones contractually responsible for staying
# sparse on pure k*sigma noise. The threshold-free detectors (otsu/log/dog/
# white-tophat) rely on the U4 pipeline's signal-presence GATE to refuse a
# no-signal residual before they are ever called (see plan: "Signal-presence
# gate is defined in the active detector's own terms"), so they are not
# expected to self-suppress noise here.
@pytest.mark.parametrize("name", ["bg-k-sigma", "h-maxima"])
def test_sigma_aware_pure_noise_residual_no_flood(name):
    """A zero-mean k*sigma noise residual → sigma-aware detectors stay sparse."""
    rng = np.random.default_rng(123)
    residual = rng.normal(0.0, 1.0, size=(80, 80))
    gmask = np.ones(residual.shape, dtype=bool)
    sigma = 1.0
    mask = DETECTORS[name](residual, gmask, sigma, _BLOB_PARAMS)
    assert mask.dtype == np.uint8
    # k=2.5 on unit noise leaves only the deep tail — well under 5%.
    assert mask.sum() < residual.size * 0.05


# ── Empty group_mask → all-zero, no raise ─────────────────────


@pytest.mark.parametrize("name", _REAL_DETECTORS)
def test_empty_group_mask_all_zero(name):
    """An all-False group_mask → all-zero mask, no exception."""
    img = _mixed_residual(shape=(80, 80))
    gmask = np.zeros(img.shape, dtype=bool)
    # Isolate per contract: everything out-of-group → NaN.
    iso = _isolate(img, gmask)
    mask = DETECTORS[name](iso, gmask, 1.0, _BLOB_PARAMS)
    assert mask.dtype == np.uint8
    assert mask.sum() == 0


# ── Determinism: bit-identical across two calls ───────────────


@pytest.mark.parametrize("name", _REAL_DETECTORS)
def test_determinism(name):
    """Same (residual, sigma, params) → bit-identical mask across two calls."""
    img = _mixed_residual()
    gmask = np.ones(img.shape, dtype=bool)
    m1 = DETECTORS[name](img, gmask, 1.0, _BLOB_PARAMS)
    m2 = DETECTORS[name](img, gmask, 1.0, _BLOB_PARAMS)
    assert np.array_equal(m1, m2)


# ── {0,1}-only / uint8 (255-regression pin) ───────────────────


@pytest.mark.parametrize("name", _REAL_DETECTORS)
def test_output_is_zero_one_uint8(name):
    """Every detector's output is uint8 with values strictly in {0, 1}."""
    img = _mixed_residual()
    gmask = np.ones(img.shape, dtype=bool)
    mask = DETECTORS[name](img, gmask, 1.0, _BLOB_PARAMS)
    assert mask.dtype == np.uint8, f"{name} returned {mask.dtype}"
    assert set(np.unique(mask)) <= {0, 1}, f"{name} has values != 0/1"
    assert mask.shape == img.shape


@pytest.mark.parametrize("name", _REAL_DETECTORS)
def test_output_restricted_to_group_and_finite(name):
    """No detector marks an out-of-group or non-finite pixel (isolation pin)."""
    img = _mixed_residual()
    gmask = np.zeros(img.shape, dtype=bool)
    gmask[:, :60] = True  # left half only
    iso = _isolate(img, gmask)  # out-of-group → NaN
    mask = DETECTORS[name](iso, gmask, 1.0, _BLOB_PARAMS)
    assert not mask[~gmask].any()  # never out-of-group
    assert not mask[~np.isfinite(iso)].any()  # never on a NaN pixel


# ── np.isin(..., list(...))-derived group mask exercised (NumPy 2.x) ──


@pytest.mark.parametrize("name", _BLOB_DETECTORS)
def test_isin_derived_group_mask_non_empty(name):
    """A group_mask built via np.isin(labels, list(...)) yields detections.

    Pins the NumPy 2.x ``np.isin(..., list(...))`` membership idiom used by the
    caller — a bare set/array argument could silently produce an all-False mask.
    """
    img = _mixed_residual()
    labels = np.zeros(img.shape, dtype=int)
    labels[:, :70] = 1  # cell 1 covers the left columns
    labels[:, 70:] = 2
    gmask = np.isin(labels, list({1}))  # the list() wrap is load-bearing
    assert gmask.any()
    iso = _isolate(img, gmask)
    mask = DETECTORS[name](iso, gmask, None, _BLOB_PARAMS)
    assert mask.sum() > 0
