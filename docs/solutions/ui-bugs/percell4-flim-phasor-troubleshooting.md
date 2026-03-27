---
title: "PerCell4 FLIM Phasor Pipeline Troubleshooting"
category: ui-bugs
tags: [flim, phasor, wavelet-filter, calibration, hdf5, bin-import, pyqtgraph, axis-scaling]
module: flim, gui, io
date: 2026-03-26
symptom: "Phasor plot showed wrong values, wavelet filter produced diffuse cloud, axis labels misleading, .bin import crashed"
root_cause: "Multiple issues: wrong omega for phasor transform, simplified wavelet filter, ImageItem axis mapping, SI prefix on axes, dtype mismatch for .bin files"
---

# PerCell4 FLIM Phasor Pipeline Troubleshooting

Comprehensive troubleshooting session fixing the entire FLIM pipeline from .bin import through phasor computation, wavelet filtering, calibration, and phasor plot rendering.

## Issues Found and Fixed

### 1. .bin File Data Type (uint32 not float32)

**Symptom:** Intensity image was blank after import — all values displayed as zero.

**Root cause:** Becker & Hickl SPCImage .bin exports use 4-byte uint32 photon counts (values 0-28), not float32. Reading as float32 reinterpreted small integers as denormalized floats (~1e-45).

**Fix:** Changed default dtype to `uint32` in import dialog and auto-detect defaults. Added dtype dropdown (uint32, uint16, float32, uint8) to import dialog.

**Key lesson:** File size matching (4 bytes/element) doesn't distinguish uint32 from float32. Check actual data values — photon counts are always integers.

### 2. Bus Error on Import from External Drive

**Symptom:** `bus error` crash when importing .bin files through the GUI. Command-line import worked fine.

**Root cause:** QThread Worker + external drive I/O combination caused memory access issues. Also, accessing dialog widget properties after `exec_()` returned could fail on some Qt backends.

**Fix:** Import now runs on main thread with wait cursor. Dialog values captured into local variables immediately after `exec_()` returns, before any processing.

### 3. Tile Index 1-Based vs 0-Based Mismatch

**Symptom:** `Can't broadcast (512,512,132) -> (3072,3072,132)` when stitching tiles.

**Root cause:** Tile tokens parsed from filenames (`_s1` through `_s36`) are 1-based, but the tile position mapping is 0-based (0-35). Every tile lookup failed, falling through to the non-tiled branch.

**Fix:** Subtract minimum tile index to normalize to 0-based after parsing.

### 4. Streaming HDF5 Write for Large Decay Arrays

**Symptom:** Bus error when importing 36 tiles — the stitched decay array (3072x3072x132 float32 = ~5 GB) exceeded memory.

**Root cause:** Tried to allocate the full stitched array in memory.

**Fix:** Decay tiles written directly to HDF5 one tile at a time using region writes. Only one tile (~50 MB) in memory at a time.

### 5. Wrong Omega for Phasor Transform

**Symptom:** Phasor values completely wrong — G=0.05 instead of expected ~0.3.

**Root cause:** Used physical omega (0.49 rad/ns from `2π * freq * bin_width`) instead of normalized DFT omega (`2π / n_bins`). The flimfret NPZ stored omega=0.49 computed with config `bin_width_ns=1.0`, but the actual physical bin width is 0.097 ns. With 132 bins × 0.097 ns = 12.8 ns = one laser period, the normalized omega `2π/132 ≈ 0.0476` is correct.

**Fix:** Reverted to normalized DFT omega: `omega = 2π * harmonic / n_bins`.

**Key lesson:** The phasor transform omega should be dimensionless (`2π/n_bins`), assuming bins span one laser period. The physical omega (with frequency and bin_width) is only needed for lifetime calculation.

### 6. Calibration Math (Cartesian Rotation)

**Symptom:** Phasor cloud rotated to wrong position after calibration.

**Root cause:** Initially used polar coordinate approach. While mathematically equivalent, switched to flimfret's exact Cartesian rotation matrix for consistency:
```
g_cal = g * m * cos(phi) - s * m * sin(phi)
s_cal = g * m * sin(phi) + s * m * cos(phi)
```

**Fix:** Exact match of flimfret's preprocessing.py formulation.

### 7. Missing Median Filter

**Symptom:** Phasor cloud more scattered than flimfret's output.

**Root cause:** flimfret applies `scipy.ndimage.median_filter(size=3)` to G, S, and intensity AFTER calibration but BEFORE saving. This is documented in flimfret's pipeline reference as Section 2.5. The "unfiltered" data in flimfret is actually spatially median-filtered, just not wavelet-filtered.

**Fix:** Added 3x3 median filter after calibration, matching flimfret.

### 8. Simplified Wavelet Filter Produced Diffuse Cloud

**Symptom:** Wavelet-filtered phasor showed a diffuse blob instead of flimfret's tight arc along the semicircle.

**Root cause:** PerCell4's wavelet filter used simplified soft thresholding. flimfret uses inter-scale Wiener-like shrinkage (`compute_phi_prime`) with multi-scale coefficient interaction — a fundamentally different and more sophisticated algorithm.

**Fix:** Copied exact functions from flimfret's wavelet_filter.py: `calculate_median_values`, `calculate_local_noise_variance`, `compute_phi_prime`, `update_coefficients`. Slower (nested Python loops) but produces correct results.

### 9. Phasor Plot ImageItem Axis Mapping

**Symptom:** Phasor histogram appeared flipped/rotated — cloud peak at wrong position.

**Root cause:** `setTransform` with translate+scale mapped pixel coordinates incorrectly. The histogram2d output axes didn't match ImageItem's display convention.

**Fix:** Used `setRect(QRectF)` to directly map image extent to data coordinates. `histogram2d` returns `hist[g_bin, s_bin]` which maps to ImageItem's `[x, y]` convention without transposing.

### 10. SI Prefix Auto-Scaling on Axes

**Symptom:** S axis showed "S (x0.001)" with values 0-700 instead of 0-0.7. Made ROI position appear to be in a different location than the actual data.

**Root cause:** pyqtgraph auto-applies SI prefix scaling to axis labels.

**Fix:** `axis.enableAutoSIPrefix(False)` on both axes.

## Prevention Patterns

| Pattern | When to Apply |
|---------|---------------|
| Check actual data values, not just file size | Any binary format import |
| Run I/O-heavy imports on main thread | External drive operations |
| Normalize tile indices to 0-based | Any filename-token-based tile stitching |
| Use streaming writes for arrays > 1 GB | HDF5 writes of large datasets |
| Use normalized DFT omega (`2π/n_bins`) for phasor | All TCSPC phasor transforms |
| Apply median filter after calibration | Phasor preprocessing (matches flimfret) |
| Copy exact algorithm, don't simplify | Wavelet filter or any signal processing |
| Use `setRect` not `setTransform` for ImageItem | pyqtgraph histogram display |
| Disable SI prefix on axes | Any plot with values 0-1 range |

## Reference: flimfret Pipeline (Ground Truth)

The authoritative reference for the correct FLIM pipeline is:
`/Users/leelab/flimfret/docs/phasor_plot_pipeline_reference.md`

Key stages:
1. `.bin` → TIFF (via ImageJ, bin_width=0.097 ns)
2. Phasor transform (omega = 2π × freq × bin_width × harmonic, dimensionless)
3. Calibration (Cartesian rotation matrix)
4. Median filter (3×3, 1 pass)
5. Save G/S/intensity TIFFs
6. Wavelet filter (DTCWT + inter-scale Wiener shrinkage)
7. Intensity-weighted 2D histogram with SymLogNorm coloring
