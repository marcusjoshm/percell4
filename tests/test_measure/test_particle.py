"""Tests for per-cell particle analysis."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from percell4.domain.measure.particle import (
    _PARTICLE_SHARPNESS_METRICS,
    _SHARPNESS_COMPUTABLE_COL,
    _iter_particles,
    analyze_particles,
    analyze_particles_detail,
)


def _make_test_data():
    """Create test data: 2 cells, one with particles, one without.

    Cell 1 (label=1): region (10:30, 10:30), 20x20 = 400 pixels
        Has 2 bright spots (particles) in the mask
    Cell 2 (label=2): region (50:70, 50:70), 20x20 = 400 pixels
        No particles (mask is zero in this region)
    """
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[10:30, 10:30] = 1
    labels[50:70, 50:70] = 2

    image = np.full((100, 100), 10.0, dtype=np.float32)
    image[10:30, 10:30] = 50.0  # cell 1 background
    image[15:18, 15:18] = 200.0  # bright spot 1 in cell 1
    image[22:25, 22:25] = 200.0  # bright spot 2 in cell 1

    # Mask: threshold-like binary mask covering the bright spots
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[15:18, 15:18] = 1  # spot 1 (9 pixels)
    mask[22:25, 22:25] = 1  # spot 2 (9 pixels)

    return image, labels, mask


def test_basic_particle_analysis():
    """Cell 1 has 2 particles, cell 2 has 0."""
    image, labels, mask = _make_test_data()
    df = analyze_particles({"ch0": image}, labels, mask)

    assert len(df) == 2
    assert "particle_count" in df.columns

    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["particle_count"] == 2
    assert cell1["total_particle_area"] == 18.0  # 9 + 9

    cell2 = df[df["label"] == 2].iloc[0]
    assert cell2["particle_count"] == 0
    assert cell2["total_particle_area"] == 0.0


def test_particle_coverage_fraction():
    """Coverage fraction = total_particle_area / cell_area."""
    image, labels, mask = _make_test_data()
    df = analyze_particles({"ch0": image}, labels, mask)

    cell1 = df[df["label"] == 1].iloc[0]
    # 18 pixels of particles / 400 pixels of cell
    assert abs(cell1["particle_coverage_fraction"] - 18.0 / 400.0) < 0.001


def test_particle_mean_area():
    """Mean area = total / count."""
    image, labels, mask = _make_test_data()
    df = analyze_particles({"ch0": image}, labels, mask)

    cell1 = df[df["label"] == 1].iloc[0]
    assert cell1["mean_particle_area"] == 9.0  # 18 / 2


def test_particle_intensity():
    """Particle intensity metrics are computed from the image."""
    image, labels, mask = _make_test_data()
    df = analyze_particles({"ch0": image}, labels, mask)

    cell1 = df[df["label"] == 1].iloc[0]
    # Both particles are in regions with intensity 200. Single channel => no
    # channel prefix, so the aggregated per-cell column is `particle_mean_intensity`.
    assert cell1["particle_mean_intensity"] == 200.0


def test_min_area_filter():
    """Particles below min_area are filtered out."""
    image, labels, mask = _make_test_data()
    df = analyze_particles({"ch0": image}, labels, mask, min_area=10)

    cell1 = df[df["label"] == 1].iloc[0]
    # Each particle is 9 pixels, both below min_area=10
    assert cell1["particle_count"] == 0


def test_empty_labels():
    """Empty label array returns empty DataFrame."""
    image = np.ones((10, 10), dtype=np.float32)
    labels = np.zeros((10, 10), dtype=np.int32)
    mask = np.ones((10, 10), dtype=np.uint8)

    df = analyze_particles({"ch0": image}, labels, mask)
    assert len(df) == 0
    assert "particle_count" in df.columns


def test_no_mask_overlap():
    """All cells get zero counts when mask doesn't overlap any cell."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[10:20, 10:20] = 1

    image = np.ones((50, 50), dtype=np.float32)
    mask = np.zeros((50, 50), dtype=np.uint8)
    mask[30:40, 30:40] = 1  # mask is outside the cell

    df = analyze_particles({"ch0": image}, labels, mask)
    assert len(df) == 1
    assert df.iloc[0]["particle_count"] == 0


def test_single_large_particle():
    """A cell that is entirely covered by the mask = 1 large particle."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[5:25, 5:25] = 1  # 20x20 = 400 pixels

    image = np.full((30, 30), 100.0, dtype=np.float32)
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[5:25, 5:25] = 1  # mask covers entire cell

    df = analyze_particles({"ch0": image}, labels, mask)
    cell = df.iloc[0]
    assert cell["particle_count"] == 1
    assert cell["total_particle_area"] == 400.0
    assert cell["particle_coverage_fraction"] == 1.0


def test_multiple_cells_multiple_particles():
    """Test with N>=2 cells, each having different particle counts."""
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[5:15, 5:15] = 1   # cell 1
    labels[5:15, 25:35] = 2  # cell 2
    labels[25:35, 5:15] = 3  # cell 3

    image = np.full((50, 50), 50.0, dtype=np.float32)
    mask = np.zeros((50, 50), dtype=np.uint8)

    # Cell 1: 1 particle
    mask[7:9, 7:9] = 1
    # Cell 2: 0 particles
    # Cell 3: 3 small particles
    mask[27:28, 7:8] = 1
    mask[30:31, 7:8] = 1
    mask[33:34, 7:8] = 1

    df = analyze_particles({"ch0": image}, labels, mask)
    assert len(df) == 3

    c1 = df[df["label"] == 1].iloc[0]
    c2 = df[df["label"] == 2].iloc[0]
    c3 = df[df["label"] == 3].iloc[0]

    assert c1["particle_count"] == 1
    assert c2["particle_count"] == 0
    assert c3["particle_count"] == 3


# ── Per-particle sharpness / focus metrics (analyze_particles_detail) ──────

_SHARPNESS_COLS = list(_PARTICLE_SHARPNESS_METRICS)


def _disk(shape, cy, cx, r, value):
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    out = np.zeros(shape, dtype=np.float64)
    out[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = value
    return out


def _sharp_vs_blurred():
    """Two cells: cell 1 an in-focus (sharp) spot, cell 2 an out-of-focus one.

    The out-of-focus spot is a Gaussian-blurred, dimmer version of the same
    disk (equal core intensity before blur), so it is spread and hazy.
    """
    labels = np.zeros((60, 60), dtype=np.int32)
    labels[5:35, 5:35] = 1
    labels[5:35, 40:55] = 2

    img = np.full((60, 60), 8.0, dtype=np.float64)  # background
    sharp = _disk((60, 60), 18, 18, 2, 200.0)
    img = np.maximum(img, sharp * (sharp > 0) + 8.0 * (sharp == 0))
    img[sharp > 0] = 200.0
    oof = gaussian_filter(_disk((60, 60), 18, 47, 2, 200.0), sigma=2.5)
    img = np.maximum(img, oof + 8.0)

    mask = np.zeros((60, 60), dtype=np.uint8)
    mask[(img > 14) & (labels > 0)] = 1
    return img.astype(np.float32), labels, mask


def test_sharpness_columns_present_per_channel():
    """AE1: detail export gains the sharpness columns for every channel."""
    img, labels, mask = _sharp_vs_blurred()
    df = analyze_particles_detail({"C0": img, "C1": img}, labels, mask)
    for ch in ("C0", "C1"):
        for metric in _SHARPNESS_COLS:
            assert f"{ch}_{metric}" in df.columns
    assert _SHARPNESS_COMPUTABLE_COL in df.columns
    # Single-channel: no prefix, matching the existing intensity-metric rule.
    df1 = analyze_particles_detail({"C0": img}, labels, mask)
    for metric in _SHARPNESS_COLS:
        assert metric in df1.columns
        assert f"C0_{metric}" not in df1.columns


def test_sharpness_directionality_sharp_vs_blurred():
    """Wiring/sanity check: metrics respond monotonically to defocus blur.

    True by construction (not a proof of real optical separation — that is
    AE2, validated on real crops). Confirms the operators are wired and
    oriented correctly.
    """
    img, labels, mask = _sharp_vs_blurred()
    df = analyze_particles_detail({"C0": img}, labels, mask)
    sharp = df[df["cell_id"] == 1].iloc[0]
    oof = df[df["cell_id"] == 2].iloc[0]

    # Out-of-focus: hazier edge -> higher skirt ratio.
    assert oof["edge_skirt_ratio"] > sharp["edge_skirt_ratio"]
    # Out-of-focus: shallower edge -> lower boundary gradient.
    assert oof["boundary_gradient"] < sharp["boundary_gradient"]
    # Out-of-focus: less high-freq content -> lower Laplacian variance.
    assert oof["laplacian_variance"] < sharp["laplacian_variance"]
    # Out-of-focus: weaker edges -> lower Tenengrad response.
    assert oof["tenengrad"] < sharp["tenengrad"]


def test_sharpness_low_signal_is_finite_not_nan():
    """A dim, low-contrast particle yields real finite values, never NaN.

    Guards the failure mode where the out-of-focus target population would be
    silently deleted from the researcher's eye-gated distributions.
    """
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[2:28, 2:28] = 1
    img = np.full((30, 30), 8.0, dtype=np.float32)
    img[13:16, 13:16] = 12.0  # peak only just above background -> low contrast
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[13:16, 13:16] = 1

    df = analyze_particles_detail({"C0": img}, labels, mask)
    row = df.iloc[0]
    for metric in _SHARPNESS_COLS:
        assert np.isfinite(row[metric]), f"{metric} should be finite for a dim particle"
    assert bool(row[_SHARPNESS_COMPUTABLE_COL]) is True


def test_boundary_gradient_stable_when_peak_near_background():
    """Regression guard: normalising by peak (not peak-background) keeps the
    boundary gradient finite and bounded when peak ~= background, where a
    (peak - background) denominator would blow up or invert."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[2:28, 2:28] = 1
    img = np.full((30, 30), 100.0, dtype=np.float32)
    img[13:16, 13:16] = 101.0  # peak barely above the 100 background
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[13:16, 13:16] = 1

    df = analyze_particles_detail({"C0": img}, labels, mask)
    bgrad = df.iloc[0]["boundary_gradient"]
    assert np.isfinite(bgrad)
    assert 0.0 <= bgrad < 1.0  # small relative steepness, no explosion


def test_sharpness_appended_after_existing_columns_unchanged():
    """Characterization: existing columns keep name, order, and values; the
    new sharpness columns are appended strictly after them."""
    img, labels, mask = _sharp_vs_blurred()
    df = analyze_particles_detail({"C0": img}, labels, mask)

    expected_existing = [
        "cell_id", "particle_id", "area", "centroid_y", "centroid_x",
        "mean_intensity", "max_intensity", "min_intensity",
        "integrated_intensity", "std_intensity", "median_intensity",
        "mode_intensity", "sg_ratio",
    ]
    cols = list(df.columns)
    assert cols[: len(expected_existing)] == expected_existing
    new_cols = cols[len(expected_existing) :]
    assert new_cols == [*_SHARPNESS_COLS, _SHARPNESS_COMPUTABLE_COL]

    # A uniform-intensity particle still measures its intensity exactly —
    # proving the sharpness additions did not perturb existing metric values.
    sharp = df[df["cell_id"] == 1].iloc[0]
    assert sharp["max_intensity"] == 200.0
    assert sharp["mean_intensity"] == 200.0


def test_sharpness_single_pixel_particle_degenerate():
    """AE3: a single-pixel particle is flagged not-computable, its variance is
    NaN, and no exception is raised."""
    labels = np.zeros((20, 20), dtype=np.int32)
    labels[2:18, 2:18] = 1
    img = np.full((20, 20), 10.0, dtype=np.float32)
    img[10, 10] = 200.0
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[10, 10] = 1  # 1-pixel particle

    df = analyze_particles_detail({"C0": img}, labels, mask)
    row = df.iloc[0]
    assert bool(row[_SHARPNESS_COMPUTABLE_COL]) is False
    assert np.isnan(row["laplacian_variance"])  # variance needs >=2 px


def test_sharpness_empty_skirt_is_nan_and_flagged():
    """A particle that fills its whole cell has no room for a skirt annulus:
    edge-skirt ratio is NaN and the particle is flagged not-computable."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[5:25, 5:25] = 1
    img = np.full((30, 30), 100.0, dtype=np.float32)
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[5:25, 5:25] = 1  # mask == whole cell -> no external skirt inside cell

    df = analyze_particles_detail({"C0": img}, labels, mask)
    row = df.iloc[0]
    assert np.isnan(row["edge_skirt_ratio"])
    assert bool(row[_SHARPNESS_COMPUTABLE_COL]) is False


def test_sharpness_skirt_excludes_neighbor_particle():
    """The edge-skirt annulus excludes a bright neighbouring particle that sits
    WITHIN the 2px skirt reach. B is placed 1px from A (a separate component)
    so that without the `~other_particles` exclusion its 250-valued pixels would
    enter A's skirt and push the ratio well above 0.1; the assertion only holds
    because the exclusion is active. (Verified to fail if the clause is removed.)"""
    labels = np.zeros((40, 40), dtype=np.int32)
    labels[2:38, 2:38] = 1  # one big cell holding both particles
    img = np.full((40, 40), 10.0, dtype=np.float32)
    img[18:20, 18:20] = 200.0   # particle A (2x2), peak 200
    img[15:24, 21:32] = 250.0   # bright wall B, 1px gap (col 20) from A
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[18:20, 18:20] = 1
    mask[15:24, 21:32] = 1

    df = analyze_particles_detail({"C0": img}, labels, mask)
    a = df.sort_values("centroid_x").iloc[0]  # leftmost component = A
    # With exclusion the skirt is pure background (10/200 = 0.05).
    assert a["edge_skirt_ratio"] < 0.1


def test_sharpness_skirt_excludes_neighboring_cell():
    """The skirt is restricted to the owning cell. Cell 2 is EMBEDDED inside
    cell 1's bounding box (so cell 1's bbox crop actually contains cell-2
    pixels — the only situation where the `cell_mask` term is load-bearing;
    for merely-adjacent rectangular cells the crop boundary already excludes
    the neighbour). A sits 2px from cell 2's edge, so without `cell_mask` the
    bright cell-2 pixels would enter A's skirt. (Verified to fail if removed.)"""
    labels = np.full((30, 30), 0, dtype=np.int32)
    labels[2:28, 2:28] = 1          # cell 1 fills the box (incl. all corners)
    labels[20:26, 20:26] = 2        # cell 2 embedded inside cell 1's bbox
    img = np.full((30, 30), 10.0, dtype=np.float32)
    img[20:26, 20:26] = 220.0       # cell 2 uniformly bright (not in the mask)
    img[20:23, 17:19] = 200.0       # particle A in cell 1, 2px left of cell 2
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[20:23, 17:19] = 1

    df = analyze_particles_detail({"C0": img}, labels, mask)
    row = df[df["cell_id"] == 1].iloc[0]
    # With cell_mask the skirt reads cell-1 background (10/200 = 0.05).
    assert row["edge_skirt_ratio"] < 0.1


def test_sharpness_detail_empty_when_no_particles():
    """Zero particles -> empty frame carrying the full column schema (base +
    intensity + sharpness + validity flag), exercising the empty-frame branch
    that _detail_columns now shares with the populated path."""
    labels = np.zeros((10, 10), dtype=np.int32)  # no cells
    img = np.ones((10, 10), dtype=np.float32)
    mask = np.zeros((10, 10), dtype=np.uint8)
    df = analyze_particles_detail({"Halo": img, "mNG": img}, labels, mask)
    assert len(df) == 0
    for ch in ("Halo", "mNG"):
        for metric in _SHARPNESS_COLS:
            assert f"{ch}_{metric}" in df.columns
    assert list(df.columns)[-1] == _SHARPNESS_COMPUTABLE_COL


def test_sharpness_computable_is_geometric_dark_channel_stays_nan():
    """sharpness_computable is geometry-only: a particle bright in one channel
    but dark (zero) in another is flagged computable=True, yet the dark
    channel's metrics are NaN while the bright channel's are finite. Downstream
    filters must AND the flag with per-channel notna()."""
    labels = np.zeros((30, 30), dtype=np.int32)
    labels[2:28, 2:28] = 1
    bright = np.full((30, 30), 8.0, dtype=np.float32)
    bright[13:16, 13:16] = 200.0
    dark = np.zeros((30, 30), dtype=np.float32)  # no signal in this channel
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[13:16, 13:16] = 1

    df = analyze_particles_detail({"mNG": bright, "Halo": dark}, labels, mask)
    row = df.iloc[0]
    assert bool(row[_SHARPNESS_COMPUTABLE_COL]) is True   # geometry is fine
    assert np.isfinite(row["mNG_edge_skirt_ratio"])        # bright channel OK
    assert np.isnan(row["Halo_edge_skirt_ratio"])          # dark channel NaN
    assert np.isnan(row["Halo_boundary_gradient"])
    assert np.isnan(row["Halo_laplacian_variance"])


def test_sharpness_not_computed_for_per_cell_summary():
    """Compute-gating: the per-cell summary path never runs sharpness work and
    exposes no sharpness columns."""
    img, labels, mask = _sharp_vs_blurred()

    # analyze_particles (per-cell summary) must not emit sharpness columns.
    summary = analyze_particles({"C0": img}, labels, mask)
    for metric in _SHARPNESS_COLS:
        assert not any(metric in c for c in summary.columns)

    # _iter_particles defaults to compute_sharpness=False -> empty payload.
    rec = next(_iter_particles({"C0": img}, labels, mask))
    assert rec.sharpness_values == {}


def test_sharpness_multichannel_prefix():
    """Multi-channel export prefixes sharpness columns per channel, matching
    the existing intensity-metric convention."""
    img, labels, mask = _sharp_vs_blurred()
    df = analyze_particles_detail({"Halo": img, "mNG": img}, labels, mask)
    assert "Halo_edge_skirt_ratio" in df.columns
    assert "mNG_boundary_gradient" in df.columns
    # Both channels are identical here, so their metrics must match row-wise.
    np.testing.assert_allclose(
        df["Halo_laplacian_variance"].to_numpy(),
        df["mNG_laplacian_variance"].to_numpy(),
        equal_nan=True,
    )
