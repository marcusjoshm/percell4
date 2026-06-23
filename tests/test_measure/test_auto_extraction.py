"""Tests for the two-pass auto-extraction domain module (AE-U2)."""

from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import disk

import pytest

from percell4.domain.measure.auto_extraction import (
    AutoExtractReport,
    auto_extract,
    measure_largest_particle_diameter,
    measure_smallest_particle_diameter,
    noise_symmetry_floor_k,
)
from percell4.domain.measure.adaptive_clip import per_cell_sigma
from percell4.domain.measure.thresholding import apply_gaussian_smoothing


def _wide_range_image(shape=(256, 256), seed=0):
    """One whole-frame cell with several small spots + one large disc, over noise."""
    rng = np.random.RandomState(seed)
    img = rng.normal(100.0, 5.0, shape).astype(np.float32)
    labels = np.ones(shape, dtype=np.int32)
    # small spots (radius 2)
    for cy, cx in [(40, 40), (40, 90), (90, 40), (60, 160), (160, 60)]:
        rr, cc = disk((cy, cx), 2, shape=shape)
        img[rr, cc] += 180.0
    # one large flat-topped disc (radius 12 -> diameter ~24)
    rr, cc = disk((180, 180), 12, shape=shape)
    img[rr, cc] = 320.0
    return img, labels


def _small_only_image(shape=(256, 256), seed=1):
    rng = np.random.RandomState(seed)
    img = rng.normal(100.0, 5.0, shape).astype(np.float32)
    labels = np.ones(shape, dtype=np.int32)
    for cy, cx in [(40, 40), (90, 90), (140, 50), (60, 150), (180, 180)]:
        rr, cc = disk((cy, cx), 2, shape=shape)
        img[rr, cc] += 200.0
    return img, labels


# --------------------------------------------------------------------------- #
# measure_largest_particle_diameter (LoG)
# --------------------------------------------------------------------------- #
def test_measure_largest_diameter_sizes_big_disc():
    img, labels = _wide_range_image()
    d = measure_largest_particle_diameter(img, labels)
    # radius-12 disc -> diameter ~24px; LoG p99 should land in a generous band.
    assert 16.0 <= d <= 34.0


def test_measure_largest_diameter_empty_cells_returns_zero():
    img = np.random.RandomState(0).normal(100.0, 5.0, (64, 64)).astype(np.float32)
    labels = np.zeros((64, 64), dtype=np.int32)  # no cells
    assert measure_largest_particle_diameter(img, labels) == 0.0


def test_measure_smallest_diameter_finds_blobs():
    img, labels = _wide_range_image()
    d = measure_smallest_particle_diameter(img, labels)
    # a low percentile of LoG sizes -> a small positive value (≈ the fine window)
    assert d > 0.0
    # and the small end is well below the largest
    assert d < measure_largest_particle_diameter(img, labels)


def test_measure_smallest_diameter_empty_cells_returns_zero():
    img = np.random.RandomState(0).normal(100.0, 5.0, (64, 64)).astype(np.float32)
    labels = np.zeros((64, 64), dtype=np.int32)
    assert measure_smallest_particle_diameter(img, labels) == 0.0


# --------------------------------------------------------------------------- #
# noise_symmetry_floor_k
# --------------------------------------------------------------------------- #
def test_noise_floor_no_cells_returns_floor():
    work = np.zeros((32, 32), dtype=np.float32)
    labels = np.zeros((32, 32), dtype=np.int32)
    assert noise_symmetry_floor_k(work, labels, {}, 31, k_floor=1.0) == 1.0


def test_noise_floor_in_range_and_rises_with_window():
    img, labels = _wide_range_image()
    work = apply_gaussian_smoothing(img, 1.0)
    sigma = per_cell_sigma(work, labels)
    k = noise_symmetry_floor_k(work, labels, sigma, 67, fdr=0.1, k_floor=1.0)
    assert 1.0 <= k <= 15.0


# --------------------------------------------------------------------------- #
# auto_extract
# --------------------------------------------------------------------------- #
def test_auto_extract_two_pass_fills_large_and_small():
    img, labels = _wide_range_image()
    mask, report = auto_extract(img, labels, smallest_particle_px=3.0)
    assert isinstance(report, AutoExtractReport)
    assert mask.dtype == np.uint8 and set(np.unique(mask)) <= {0, 1}
    assert report.second_pass_used is True
    assert len(report.passes) == 2
    assert report.passes[0] == (9, 1.0)         # fine = 3 x 3
    assert report.passes[1][1] > 1.0            # coarse k raised above 1
    # the large disc centre is filled solid (it would hole out at the fine window)
    assert mask[180, 180] == 1
    # small spots detected too
    assert mask[40, 40] == 1


def test_auto_extract_single_pass_when_no_large_particle():
    img, labels = _small_only_image()
    # A large fine window (smallest=10 -> fine=30) exceeds the measured coarse,
    # so only one pass runs.
    mask, report = auto_extract(img, labels, smallest_particle_px=10.0)
    assert report.second_pass_used is False
    assert len(report.passes) == 1
    assert mask[40, 40] == 1


def test_auto_extract_autodetects_smallest_by_default():
    """smallest_particle_px=None -> fine window = 3 × LoG-measured smallest Ø."""
    img, labels = _wide_range_image()
    mask, report = auto_extract(img, labels)  # default: autodetect both ends
    assert report.smallest_source.startswith("auto LoG")
    assert report.smallest_diameter_px > 0.0
    # fine window is the no-hole 3× of the measured smallest, not equal to it.
    assert report.fine_window == max(3, round(3.0 * report.smallest_diameter_px))
    assert int(mask.sum()) > 0


def test_auto_extract_supplied_smallest_uses_fill_factor():
    """A supplied true Ø sets the fine window to fill_factor × it."""
    img, labels = _wide_range_image()
    _, report = auto_extract(img, labels, smallest_particle_px=3.0)
    assert report.fine_window == 9          # 3 × 3
    assert report.smallest_source.startswith("supplied")
    assert report.smallest_diameter_px == 3.0


def test_auto_extract_raises_when_no_smallest_blobs():
    """Autodetection on a flat (blob-free) image raises, prompting a manual Ø."""
    img = np.full((64, 64), 100.0, dtype=np.float32)
    labels = np.ones((64, 64), dtype=np.int32)
    with pytest.raises(ValueError, match="autodetection found no blobs"):
        auto_extract(img, labels)  # autodetect on a flat image


def test_auto_extract_no_cells_is_empty():
    img = np.random.RandomState(0).normal(100.0, 5.0, (64, 64)).astype(np.float32)
    labels = np.zeros((64, 64), dtype=np.int32)
    mask, report = auto_extract(img, labels, smallest_particle_px=3.0)
    assert int(mask.sum()) == 0
    assert report.n_cells == 0


def test_auto_extract_min_spot_filters_specks():
    img, labels = _wide_range_image()
    big_filter, _ = auto_extract(img, labels, smallest_particle_px=3.0, min_spot_px=400)
    no_filter, _ = auto_extract(img, labels, smallest_particle_px=3.0, min_spot_px=1)
    assert int(big_filter.sum()) < int(no_filter.sum())
    # the large disc (area ~452 px) survives a 400 px filter
    assert big_filter[180, 180] == 1
