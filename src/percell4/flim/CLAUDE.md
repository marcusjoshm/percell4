# src/percell4/flim/

FLIM phasor computation and denoising. All functions are pure numpy — no
HDF5 or GUI coupling.

## Modules

- `phasor.py` — `compute_phasor()`. Direct cosine/sine transform (not a
  full FFT) at a single harmonic, so memory cost is O(H*W) instead of
  O(H*W*n_bins). Supports both in-memory arrays and chunked HDF5 datasets
  for large images. Returns `(G, S)` as two float32 arrays. Assumes the
  time bins span one full laser period (standard Becker & Hickl TCSPC
  convention).
- `wavelet_filter.py` — `wavelet_filter_phasor()`. Vectorized port of
  `flimfret`'s DTCWT-based Wiener-like shrinkage, ~100x faster than the
  original nested-loop version. Requires the optional `dtcwt` package
  (`pip install percell4[flim]`).
