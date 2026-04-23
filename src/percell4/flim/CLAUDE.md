# src/percell4/domain/flim/

FLIM phasor computation and wavelet denoising. All functions are pure
numpy / scipy / dtcwt — no HDF5 or GUI coupling.

## Modules

- `phasor.py` — `compute_phasor()`. Direct cosine/sine transform (not a
  full FFT) at a single harmonic, so memory cost is O(H*W) instead of
  O(H*W*n_bins). Supports both in-memory arrays and chunked HDF5
  datasets for large images. Returns `(G, S)` as two float32 arrays.
  Assumes the time bins span one full laser period (standard Becker &
  Hickl TCSPC convention).

## Subpackages

- `wavelet/` — complex-wavelet denoising for FLIM phasor data. Two
  algorithms coexist behind a single dispatch:
  - `wavelet.denoise_phasor(g, s, intensity, *, algorithm=...)` picks
    the implementation via `_FILTER_REGISTRY`. The GUI combo (in
    `flim_panel.py`) reads the same `ALGORITHM_CHOICES` list.
  - `wavelet.boe.denoise_phasor_boe` — strict replication of Wang et
    al. 2021 (*Biomed. Opt. Express* 12(6):3463). LeGall 5/3 +
    Q-shift 10, σ_g from level-1 ±45° bands (MAD/0.6745), full
    Sendur-Selesnick BiShrink with `(σ_n² − σ_g²)_+` factor,
    ThreadPoolExecutor over the three channels, rigorous input
    sanitization and minimum-size guard. Default.
  - `wavelet.jcb.denoise_phasor_jcb` — reproduces the Python code at
    `LeeLabBCM/ComplexWaveletFilter` (the JCB 2025 paper's reference
    implementation). Kept available for users reproducing the
    published JCB paper. Uses a simpler shrinkage formula (drops the
    `(σ_n² − σ_g²)_+` term) and estimates σ_g across all levels/bands.
  - `wavelet._shared` — Anscombe forward, Mäkitalo-Foi closed-form
    inverse, `next_multiple` / `next_pow2` padding helpers.

Requires the optional `dtcwt` package: `pip install percell4[flim]`.
dtcwt is imported lazily inside the filter functions so the module
imports cleanly without it.
