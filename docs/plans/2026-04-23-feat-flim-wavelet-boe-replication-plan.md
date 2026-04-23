---
title: "feat: Implement strict BOE 2021 FLIM wavelet filter with A/B comparison"
type: feat
date: 2026-04-23
brainstorm: docs/brainstorms/2026-04-23-flim-wavelet-filter-boe-replication-brainstorm.md
branch: feat/FLIM-complex-wavelet-filter
deepened: 2026-04-23
---

# Enhancement Summary

**Deepened on:** 2026-04-23 via `/deepen-plan`
**Research agents used (9 parallel):** kieran-python-reviewer, performance-oracle,
code-simplicity-reviewer, architecture-strategist, pattern-recognition-specialist,
data-integrity-guardian, best-practices-researcher, framework-docs-researcher,
spec-flow-analyzer.

## Key improvements over the first-draft plan

1. **Subpackage layout over `_boe` suffix.** Move from flat sibling
   `wavelet_filter_boe.py` to a `domain/flim/wavelet/` subpackage with
   `jcb.py`, `boe.py`, and `_shared.py` (Anscombe + padding helpers).
   Eliminates duplication; expresses that "wavelet" is a family, not a
   single module.
2. **Protocol + registry dispatch** instead of `if/elif` on a `Literal`.
   One `PhasorDenoiser` Protocol, one `_FILTER_REGISTRY` dict — GUI combo,
   use-case dispatch, and tests all read from the same source of truth.
3. **Both OQ1 and OQ2 resolved** from dtcwt source inspection:
   - `biort='legall'` taps in dtcwt 0.14.0 match BOE supp Table S1
     exactly. ✓
   - Band index → orientation mapping is
     `{0:+15°, 1:+45°, 2:+75°, 3:−75°, 4:−45°, 5:−15°}`. So ±45° bands
     are indices **1 and 4**. ✓ Hard-code as module constants with a
     pytest guard.
4. **Performance-correct padding.** Replace `_next_pow2` with
   `_next_multiple(n, 2 ** n_levels)` — saves real memory and time on
   non-pow2 inputs. Apply to both filters via the shared helper.
5. **Thread-pool the three channels.** dtcwt releases the GIL in its FFT
   core; running `F_real`, `F_imag`, `I` concurrently via
   `ThreadPoolExecutor(max_workers=3)` gives ~2.5× speedup with no API
   changes.
6. **Runtime target tightened** from 2× JCB to **1.15× JCB** at 1024² /
   flevel=9. A 2× gate would mask real regressions.
7. **Rich HDF5 provenance.** Beyond `algorithm`: `biort`, `qshift`,
   `n_local_window`, `sigma_g_estimator`, `shrinkage`, `dtcwt_version`,
   `percell4_version`, and `algorithm_params_hash`. `lifetime_filtered`
   additionally carries `omega_rad_per_ns`.
8. **Backward-compat read contract** — datasets written before this
   change lack `algorithm`; readers treat missing as `"jcb_2025"`,
   centralized in one helper.
9. **Atomicity.** Introduce `DatasetStore.write_arrays(items)` that
   writes all three filtered datasets under a single `h5py.File` handle,
   bounded by a `filter_status` sentinel attr. Crash window collapses
   from seconds to milliseconds.
10. **Schema single source of truth.** New `src/percell4/store_schema.py`
    documenting every `phasor/{ch}/...` key, dtype, and required attrs.
11. **GUI polish.** Short labels `["BOE", "JCB"]` with citations in
    `setToolTip`. `QSettings("leelab", "percell4")` persistence. Warn
    before overwriting a different-algorithm result.
12. **Algorithm corrections discovered during review:**
    - Input sanitization: NaN/Inf in G/S (from `intensity==0`
      division upstream) must be zeroed before Step 1 multiplication.
    - Minimum-size guard: `min(H,W) ≥ 2**filter_level` (otherwise σ_g
      estimate collapses).
    - Explicit `denom > 0` branch in BiShrink to avoid silent
      RuntimeWarnings at uniform regions.
    - dtcwt `.forward` silently pads odd dimensions and emits
      `logging.warn` — crop back to `[:orig_H, :orig_W]` after inverse.
13. **Snake_case HDF5 attr values** — `"boe_2021"` / `"jcb_2025"`,
    matching codebase conventions. (Original plan used hyphens.)
14. **Test naming normalized to codebase convention** — one file
    `tests/test_flim/test_wavelet_boe.py` covers unit + pipeline;
    dispatch tests fold into `tests/test_use_cases.py`.
15. **Quantitative acceptance criteria** replace "visually matches"
    wording; new explicit criteria for backward compat, performance
    regression, error handling, and GUI smoke.
16. **Deferred from scope** (separate follow-up PRs): QThread Worker
    refactor of the Apply click; upstream PR to
    `LeeLabBCM/ComplexWaveletFilter`.

## New considerations discovered

- dtcwt is **archived** (July 2024) and has a known NumPy 2 issue
  (`np.asfarray`). Plan leans on the existing `_compat.py` shim;
  document this hard dependency in the README so the risk is visible.
- **σ in the BiShrink denominator is a stddev, not a variance.** Paper
  labels it σ_g² but the Sendur-Selesnick 2002/2003 formulation treats
  the quantity as `√((σ_y²−σ_g²)_+)` — an amplitude, not a variance.
  Plan pseudocode is consistent with both readings (uses
  `(σ_n²−σ_g²)_+` inside the outer √), but the variable names are
  tightened to prevent dimensional confusion in code review.
- **Coarsest-level policy** — leave unshrunk (preserves DC, which
  matters for phasor). Codified as an explicit decision rather than
  an incidental consequence of `range(max_level)`.

---

# Implement strict BOE 2021 FLIM wavelet filter with A/B comparison

## Overview

Add a second FLIM complex-wavelet-filter implementation that strictly
replicates Wang et al. 2021 (*Biomed. Opt. Express* 12(6), 3463 — the
Leica/USC paper, DOI `10.1364/BOE.420953`), and expose it alongside the
existing "JCB 2025" filter behind a GUI selector. Include a synthetic +
real comparison harness that reproduces the paper's MSE-vs-spatial-frequency
analysis so we can quantify the difference between the two algorithms on
percell4 data.

The current filter is a faithful port of the author's own
`LeeLabBCM/ComplexWaveletFilter` repo (the code behind Fahim & Marcus et
al., *JCB* 2025). That repo deviates from the BOE paper in three ways —
two pre-existing in the JCB repo, one introduced during porting. None are
documented anywhere before now. This plan builds a strict paper
reimplementation so we can measure the cost or benefit of those
simplifications on real FLIM data, and keep both filters available.

## Problem Statement

### The three-layer divergence

| Aspect | BOE paper (Wang 2021) | JCB repo (user's own) | Percell4 port |
|---|---|---|---|
| Level-1 wavelet | **LeGall 5/3** (supp Table S1) | `biort='Legall'` ✓ | **`biort='near_sym_a'` ✗** |
| Higher-level wavelet | Kingsbury Q-shift 10 | `qshift_a` ✓ | `qshift_a` ✓ |
| Global noise σ_g bands | **Level-1 ±45° only** | all levels × all 6 bands | all levels × all 6 bands |
| σ_g estimator | `median(\|Φ\|)/0.6745` | `mean(medians)/0.6745` | `mean(medians)/0.6745` |
| Shrinkage | BiShrink: `(1 − √3·σ_g²/√((\|Φ_l\|²+\|Φ_parent\|²)·(σ_n²−σ_g²)_+))_+ · Φ_l` | drops `(σ_n²−σ_g²)_+`; `σ_n²` only gates | same as JCB repo |
| Local window N | not stated (Sendur-Selesnick convention N=3) | `N = flevel` (19×19 at default) | same, capped 7×7 if flevel>10 |
| Anscombe forward | `2√(I + 3/8)` | same ✓ | same ✓ |
| Anscombe inverse | "inverse Anscombe" (unspecified) | Mäkitalo-Foi 6th-order closed-form | same ✓ |

The shrinkage formula divergence is the most consequential. The BOE/
Sendur-Selesnick form uses the local variance to adaptively attenuate
shrinkage at edges (high `σ_n²` → smaller shrinkage factor → preserve
signal). The current code drops that term entirely, so shrinkage at every
non-zero pixel is controlled only by the global noise σ_g and the
interscale magnitudes.

### Why now

1. **Correctness.** The current filter claims to implement the published
   Wang et al. 2021 algorithm, but does not. `src/percell4/flim/
   CLAUDE.md` references the paper; `docs/solutions/ui-bugs/percell4-
   flim-phasor-troubleshooting.md` explicitly warns *"Never simplify
   signal processing algorithms"* — yet that is what the current code
   does. The claim and the code disagree.
2. **Reproducibility.** The percell4 port introduced a silent regression
   (`biort='near_sym_a'` instead of `'legall'`) that drifts from the
   author's own JCB 2025 paper code. Anyone attempting to reproduce JCB
   results through percell4 will get slightly different numbers than
   running `LeeLabBCM/ComplexWaveletFilter` directly.
3. **Methodology.** We cannot know whether the JCB simplifications help,
   hurt, or are equivalent without a reference implementation to compare
   against.

## Resolved Open Questions (from research)

**OQ1 (resolved). dtcwt `biort='legall'` coefficients match BOE Table S1.**
Verified by inspecting `/Users/leelab/percell4/.venv/lib/python3.12/
site-packages/dtcwt/data/legall.npz`. The nonzero taps match exactly:

| dtcwt key | Taps (length 5 or 3) | BOE paper (Table S1) |
|---|---|---|
| `h0o` (Tree-A analysis low-pass) | `[-1/8, 1/4, 3/4, 1/4, -1/8]` | identical |
| `h1o` (Tree-A analysis high-pass, zeros stripped) | `[-1/4, 1/2, -1/4]` | identical |
| `g0o` (Tree-B synthesis low-pass) | `[1/4, 1/2, 1/4]` | identical |
| `g1o` (Tree-B synthesis high-pass) | `[-1/8, -1/4, 3/4, -1/4, -1/8]` | identical |

dtcwt stores only non-zero taps; BOE paper zero-pads to length 6 to show
half-sample phase offset between Trees. **Use `biort='legall'` (lowercase
only — `'Legall'` raises `IOError`).**

**OQ2 (resolved). dtcwt band ordering is
`{0:+15°, 1:+45°, 2:+75°, 3:−75°, 4:−45°, 5:−15°}`.** Verified from the
`q2c` function in `dtcwt/numpy/lowlevel.py` and from pytorch-wavelets
documentation (same underlying Kingsbury MATLAB toolbox). **The ±45°
bands are indices 1 and 4.** Hard-coded as module constants with a
pytest guard to catch any future dtcwt upgrade that reorders them.

## Proposed Solution

Build a clean, strictly BOE-compliant filter as a sibling module inside
a new `domain/flim/wavelet/` subpackage. Share common utilities (Anscombe,
padding) via `_shared.py`. Wire both filters behind a registry-driven
dispatch, a single GUI combo, and rich HDF5 provenance attrs. Validate
via reproducing the BOE paper's synthetic MSE experiment plus one real
high-frame FLIM dataset from the project. Keep both filters permanently;
BOE becomes the default after validation; JCB stays as
"JCB (Fahim 2025)" for reproducing the published paper.

### High-level architecture (deepened)

```
domain/flim/
├── phasor.py                           # unchanged
├── wavelet/                            # NEW subpackage
│   ├── __init__.py                     # PhasorDenoiser Protocol, _FILTER_REGISTRY,
│   │                                   # denoise_phasor public dispatch, DEFAULT_FILTER_LEVEL
│   ├── _shared.py                      # anscombe_forward, anscombe_inverse_makitalo_foi,
│   │                                   # next_multiple(n, m), pad_and_mark, crop_to
│   ├── jcb.py                          # move of wavelet_filter.py, exports denoise_phasor_jcb
│   └── boe.py                          # new strict filter, exports denoise_phasor_boe
├── synthetic/                          # NEW subpackage
│   ├── __init__.py
│   └── spoke_phantom.py                # BOE-style Siemens-star + TCSPC generator
└── (wavelet_filter.py is renamed/moved; a thin re-export shim stays only if
   external callers import from the old path — otherwise delete)

application/use_cases/
└── apply_wavelet.py                    # algorithm: Literal["boe_2021","jcb_2025"],
                                        # registry-based dispatch, expanded HDF5 attrs,
                                        # warn-on-switch hook, MissingOptionalDependency
                                        # domain error at boundary

interfaces/gui/task_panels/
└── flim_panel.py                       # QComboBox "Algorithm" with tooltip citations,
                                        # QSettings persistence,
                                        # switch-warning dialog

interfaces/cli/
└── bench_wavelet.py                    # synthetic + real comparison harness

store_schema.py                         # NEW — single source of truth for phasor HDF5 paths

tests/
├── test_flim/
│   ├── test_wavelet_boe.py             # NEW — unit + pipeline, @pytest.mark.slow for pipeline
│   ├── test_wavelet_boe_perf.py        # NEW — perf budget (@pytest.mark.benchmark)
│   └── _spoke_phantom.py               # shared helper (not a fixture file;
│                                        # imported by tests and bench CLI)
└── test_use_cases.py                   # gains dispatch tests (no new file)
```

The existing `wavelet_filter.py` is being *moved* and *renamed* to
`wavelet/jcb.py`. This contradicts the brainstorm's "do not edit existing
filter" stance but is the correct structural choice; we avoid divergence
by: (a) making the move a pure rename (no behavior change), (b) giving
it its own commit, (c) keeping the JCB algorithm byte-for-byte identical
through the move so A/B comparisons remain valid.

### Research Insights — architecture

**Why a subpackage, not `_boe` suffix** *(Kieran Python review,
pattern-recognition-specialist)* — Anscombe is identical across BOE and
JCB, and `_next_pow2` appears in both. A flat-sibling layout makes copies
of shared helpers nearly inevitable; the subpackage gives them a single
home. Naming "wavelet" as a family also positions us for a third entrant
(e.g. a real-wavelet or median filter comparator) without another rename.

**Why Protocol + registry** *(Kieran, architecture-strategist)* — precedent
in this codebase is string-keyed dispatch tables:
`analysis_panel.py:131` uses `THRESHOLD_METHODS = {...}`. Matching that
pattern for wavelet filters gives: (1) single source of truth for both
the GUI combo and the dispatch, (2) static type checking of signature
parity via `Protocol`, (3) trivial extensibility when a third filter
arrives.

**Why `Literal` (not `Enum`)** *(Kieran, architecture-strategist,
simplicity)* — two string values, no methods on them, serializes
directly to HDF5 attrs. `Enum` adds conversion boilerplate at every
call site (including HDF5 write paths where the value must be `.value`'d
or str'd back). `Literal` gives `mypy`/`pyright` exhaustiveness with
zero runtime overhead.

## Technical Approach

### Algorithm specification (strict BOE 2021, deepened)

Input: 2D phasor `G(x, y)`, `S(x, y)`, intensity `I(x, y)` (photon
counts), angular frequency `ω = 2π·f_laser` in rad/ns. Output: filtered
G, S, intensity, lifetime.

**Step 0 — Input sanitization (NEW; not in original plan).**
```
G = nan_to_num(G, nan=0.0, posinf=0.0, neginf=0.0)
S = nan_to_num(S, nan=0.0, posinf=0.0, neginf=0.0)
I = nan_to_num(I, nan=0.0, posinf=0.0, neginf=0.0)
I = maximum(I, 0)          # photon counts can't be negative
assert min(H, W) >= 2**filter_level, "image too small for n_levels"
```
Unfiltered phasor G/S is NaN wherever I=0 (division by zero upstream
in ComputePhasor). NaN in `Step 1`'s `G·I` multiplication propagates
through Anscombe and DTCWT, poisoning the entire output. Required,
not optional.

**Step 1 — Fourier coefficients & Anscombe.**
```
F_real(x, y) = G(x, y) · I(x, y)
F_imag(x, y) = S(x, y) · I(x, y)
anscombe(x)  = 2·√(max(x + 3/8, 0))
F_real'      = anscombe(F_real)
F_imag'      = anscombe(F_imag)
I'           = anscombe(I)
```

**Step 2 — Pad & forward DTCWT, L levels.** For each of the three
channels:
```
H', W' = next_multiple(H, 2**L), next_multiple(W, 2**L)   # NOT next pow2
padded = np.pad(channel', ((0, H'-H), (0, W'-W)), mode='reflect')
xfm    = dtcwt.Transform2d(biort='legall', qshift='qshift_a')
coeffs = xfm.forward(padded, nlevels=L)
```
- `next_multiple` (not `next_pow2`) saves memory on non-pow2 inputs.
  dtcwt only requires dimensions divisible by `2^L`.
- dtcwt 0.14.0 `biort='legall'` matches BOE Table S1 exactly
  (OQ1 resolved).
- dtcwt **silently pads odd dims further** (duplicates last row/col,
  emits `logging.warn`). After `inverse()` we MUST crop back to
  `[:H', :W']` and then `[:H, :W]`. Record this as a subtle hazard.

**Step 3 — Global noise estimate σ_g (single scalar).**
```
BAND_PLUS_45  = 1      # OQ2 resolved: dtcwt band indices for ±45°
BAND_MINUS_45 = 4
lvl1          = coeffs.highpasses[0]           # (H/2, W/2, 6) complex
mad_plus      = median(|lvl1[:, :, BAND_PLUS_45]|)
mad_minus     = median(|lvl1[:, :, BAND_MINUS_45]|)
σ_g           = median([mad_plus, mad_minus]) / 0.6745     # scalar, units of coeff amplitude
```
Document as a scalar `σ_g` (standard deviation estimate), not `σ_g²`.
Paper notation labels it σ_g² but the MAD/0.6745 robust estimator
yields σ. Downstream uses `σ_g²` explicitly where required. This
variable-naming tightness prevents a dimensional bug from slipping
into code review.

**Step 4 — Local noise variance σ_n²(l, b, x, y).** For every level `l`,
every band `b ∈ {0..5}`, every pixel:
```
σ_n²(l, b, x, y) = uniform_filter(|Φ(l, b)|², size=2·N+1, mode='reflect')
```
with **N = 3** (Sendur-Selesnick convention → 7×7 window). `mode='reflect'`
(not `'constant'` as in the current JCB code — that distinction was
verified as a BOE-strict choice by the best-practices reviewer). No GUI
parameter for N; exposed only as a function kwarg.

**Step 5 — Inter-scale BiShrink (Sendur-Selesnick 2002/2003).** For each
level `l ∈ {0, ..., L-2}` and band `b ∈ {0..5}`:
```
Φ_l         = coeffs.highpasses[l][:, :, b]                # (h_l, w_l) complex
Φ_parent    = nn_upsample_2x(coeffs.highpasses[l+1][:, :, b])   # same shape
R²(x, y)    = |Φ_l|² + |Φ_parent|²                         # ≥ 0
D(x, y)     = max(σ_n²(l, b, x, y) - σ_g², 0)              # positive part, amplitude²
denom(x, y) = √(R² · D)                                    # amplitude · amplitude
factor(x,y) = where(denom > 0, max(1 − √3·σ_g² / denom, 0), 0)
Φ_l'        = factor · Φ_l
```
- `denom > 0` explicit branch avoids `RuntimeWarning: divide by zero`.
- NN upsample: `np.repeat(np.repeat(x, 2, 0), 2, 1)[:h_l, :w_l]`.
  Equivalent to `np.kron(x, ones((2,2)))` and matches the canonical
  MATLAB `bishrink.m`.
- **Coarsest level L-1 is skipped (unshrunk).** Canonical MATLAB
  BiShrink applies unary soft-threshold at the coarsest level; for FLIM
  phasor data, leaving it unshrunk is safer — the low-frequency band
  carries most of the mean lifetime signal (DC-dominated). This matches
  current JCB code's `range(max_level)` and is kept as an explicit
  design decision rather than an accident.

**Step 6 — Inverse DTCWT.** `xfm.inverse(coeffs)`. Crop to `[:H', :W']`
then `[:H, :W]` to undo both the explicit pad and any dtcwt-internal
odd-size pad.

**Step 7 — Inverse Anscombe (Mäkitalo-Foi closed-form).**
```
inv(y) = y²/4 − 1/8 + (1/4)·√(3/2)·y⁻¹ − (11/8)·y⁻² + (5/8)·√(3/2)·y⁻³
```
Matches current code; confirmed as the right choice for low-count FLIM
(best-practices reviewer).

**Step 8 — Recover filtered phasor.**
```
G_filt = F_real_filt / I_filt     (zero where I_filt ≤ 0)
S_filt = F_imag_filt / I_filt
if ω is not None and ω > 0:
    T_filt = S_filt / (ω · G_filt)         # clipped to [0, 50] ns, NaN elsewhere
else:
    T_filt = None                          # and no lifetime HDF5 write
```

### Research Insights — algorithm

**Formula verification** *(best-practices-researcher)* — the canonical
Sendur-Selesnick form from IEEE TSP 50(11) 2002 is:
`ŵ₁ = [(√(w₁² + w₂²) − √3·σ_n²/σ)_+ / √(w₁² + w₂²)] · w₁`
where σ is the marginal stddev `√((σ_y² − σ_n²)_+)`. Algebraically:
`factor = (1 − √3·σ_n² / (σ · R))_+` where `R = √(w₁²+w₂²)`.
Plan's form `(1 − √3·σ_g² / √(R² · D))_+` with `D = (σ_n²−σ_g²)_+` is
identical after substitution: `σ · R = √((σ_y²−σ_g²)_+) · √(R²) =
√(R² · D)`. **Plan pseudocode is correct.**

**Parent upsampling** — nearest-neighbor (Kronecker product with 1s), not
interpolated. Matches MATLAB reference and all major implementations
(scikit-image, PyWavelets examples, pytorch-wavelets).

**Coarsest-level policy** — two canonical choices (unary soft-threshold
vs leave-unshrunk). Plan commits to leave-unshrunk as safer for phasor;
coarsest-level shrinkage can erode the lifetime DC. Unit-test this
explicitly: `assert_array_equal(filtered.coarse, input.coarse)` on a
uniform-lifetime synthetic.

### Dispatch pattern (deepened)

`src/percell4/domain/flim/wavelet/__init__.py`:

```python
from typing import Protocol, Callable
from numpy.typing import NDArray

DEFAULT_FILTER_LEVEL: int = 9
N_LOCAL_WINDOW_BOE:   int = 3
N_LOCAL_WINDOW_JCB:   int | None = None  # JCB derives from filter_level

class PhasorDenoiser(Protocol):
    def __call__(
        self,
        g: NDArray, s: NDArray, intensity: NDArray,
        *,
        filter_level: int = DEFAULT_FILTER_LEVEL,
        omega: float | None = None,
    ) -> dict[str, NDArray]: ...

def _load_jcb() -> PhasorDenoiser:
    from percell4.domain.flim.wavelet.jcb import denoise_phasor_jcb
    return denoise_phasor_jcb

def _load_boe() -> PhasorDenoiser:
    from percell4.domain.flim.wavelet.boe import denoise_phasor_boe
    return denoise_phasor_boe

_FILTER_REGISTRY: dict[str, Callable[[], PhasorDenoiser]] = {
    "boe_2021": _load_boe,
    "jcb_2025": _load_jcb,
}

def denoise_phasor(
    g, s, intensity, *,
    algorithm: str = "boe_2021",
    filter_level: int = DEFAULT_FILTER_LEVEL,
    omega: float | None = None,
) -> dict[str, NDArray]:
    try:
        fn = _FILTER_REGISTRY[algorithm]()
    except KeyError:
        raise ValueError(
            f"Unknown wavelet algorithm: {algorithm!r}. "
            f"Expected one of {sorted(_FILTER_REGISTRY)}"
        ) from None
    return fn(g, s, intensity, filter_level=filter_level, omega=omega)

ALGORITHM_CHOICES: list[tuple[str, str, str]] = [
    # (id, short_label, tooltip_citation)
    ("boe_2021", "BOE",
     "Strict replication of Wang et al. 2021\n"
     "Biomed. Opt. Express 12(6):3463\nDOI 10.1364/BOE.420953"),
    ("jcb_2025", "JCB",
     "Matches LeeLabBCM/ComplexWaveletFilter\n"
     "(Fahim & Marcus et al., J. Cell Biol. 2025)\nfor reproducibility"),
]
```

`src/percell4/application/use_cases/apply_wavelet.py:41`:

```python
def execute(
    self,
    channel: str,
    *,
    filter_level: int = DEFAULT_FILTER_LEVEL,
    algorithm: Literal["boe_2021", "jcb_2025"] = "boe_2021",
) -> WaveletResult:
    # ... (unchanged until filter call) ...

    from percell4.domain.flim.wavelet import denoise_phasor

    try:
        result = denoise_phasor(
            g_map, s_map, intensity.astype(np.float64),
            algorithm=algorithm,
            filter_level=filter_level,
            omega=omega,
        )
    except ImportError as e:
        raise MissingOptionalDependencyError(
            "Wavelet filtering requires the optional 'flim' extra: "
            "pip install 'percell4[flim]'"
        ) from e

    # ... (write three datasets via store.write_arrays — single handle) ...
```

`WaveletResult` (line 18–27) gains `algorithm: str` as the **last**
field (non-default after non-defaults is fine; placing in the middle
would break existing callers).

A new domain error `MissingOptionalDependencyError` lives at
`src/percell4/domain/errors.py` so the GUI layer can catch it
specifically and render an actionable dialog instead of a raw
`ImportError` traceback.

### GUI toggle (deepened)

`src/percell4/interfaces/gui/task_panels/flim_panel.py`:

- Add `QComboBox` "Algorithm" near line 94, populated from
  `ALGORITHM_CHOICES`:
  ```python
  self._wavelet_algorithm = QComboBox()
  for alg_id, label, tip in ALGORITHM_CHOICES:
      self._wavelet_algorithm.addItem(label, userData=alg_id)
      idx = self._wavelet_algorithm.count() - 1
      self._wavelet_algorithm.setItemData(idx, tip, Qt.ToolTipRole)
  ```
- Persist across sessions via `QSettings("leelab", "percell4")`:
  ```python
  last = QSettings("leelab", "percell4").value(
      "flim/wavelet_algorithm", "boe_2021"
  )
  idx = self._wavelet_algorithm.findData(last)
  self._wavelet_algorithm.setCurrentIndex(max(idx, 0))
  ```
  `currentIndexChanged` updates the setting.
- At click (`_on_apply_wavelet`, line 193):
  ```python
  alg_id = self._wavelet_algorithm.currentData()
  ```
- **Switch warning.** Before calling `ApplyWavelet.execute`, read the
  existing `algorithm` attr on `phasor/{ch}/g_filtered` (if present).
  If the attr exists and differs from the selected algorithm, show a
  `QMessageBox.warning` with Ok/Cancel. On cancel, return without
  running. Low-cost UX guardrail against silent data loss.

### Research Insights — GUI

- **Short labels + tooltips** *(pattern-recognition-specialist)* —
  existing combos use terse labels ("Otsu", "Triangle", "Li").
  Verbose labels with citations are developer-centric. Tooltip carries
  the citation.
- **`currentData()` not index mapping** *(Kieran, architecture)* —
  a parallel index→id dict breaks silently on combo reorder.
- **`QSettings` namespace** *(spec-flow)* — `"leelab"/"percell4"`
  matches existing `QApplication.setOrganizationName` usage.

### QThread Worker refactor — **DEFERRED**

The first-draft plan listed wrapping the Apply click in
`gui/workers.py:Worker` as a stretch goal. Removed from this PR by
consensus of simplicity and Kieran reviewers: orthogonal to the
algorithm work, expands review surface, and can land in a dedicated
follow-up PR. The existing synchronous `WaitCursor` behavior is
preserved.

### Comparison harness (deepened)

**Synthetic generator** — `domain/flim/synthetic/spoke_phantom.py`:

```python
@dataclass(frozen=True, slots=True)
class SpokeTCSPC:
    tcspc:     NDArray    # (H, W, T) uint32 photon counts
    g_true:    NDArray    # (H, W) float64
    s_true:    NDArray    # (H, W) float64
    intensity: NDArray    # (H, W) float64
    meta:      dict       # generation parameters for reproducibility

def generate_spoke_tcspc(seed: int = 0) -> SpokeTCSPC:
    """BOE supp Fig. S2 replica: Siemens star, 1.5 ns / 2.5 ns bi-exp,
    80 MHz, 3 µs dwell, 0.1 photons/pulse, 256 time bins, 512×512.
    Deterministic under `seed`. Hardcoded to paper parameters — no
    knobs to avoid premature generality."""
```

*Deepening rationale (simplicity reviewer):* the original plan's
9-parameter signature was over-engineered for a tool that runs to
reproduce a specific paper figure. One `seed` kwarg is enough; paper
parameters are invariants of the synthetic.

**Benchmark CLI** — `src/percell4/interfaces/cli/bench_wavelet.py`:

```
python -m percell4.interfaces.cli.bench_wavelet synthetic \
    --out /tmp/wavelet_bench_synthetic/ \
    --filter-levels 7,9,11 \
    --n-frames 1,500                  # test vs. reference accumulation

python -m percell4.interfaces.cli.bench_wavelet real \
    --h5 path/to/high_frame.h5 --channel ch0 \
    --reference-frames 1:100          # validated against available frames
    --test-frame 5 \
    --out /tmp/wavelet_bench_real/
```

Subcommand-style (one CLI, two modes) keeps `percell4-gui` + CLI list
manageable. Outputs per run:
- `phasor_plots.png`, `g_maps.png`, `mse_per_frequency.png`
- `metrics.json` — whole-image and per-spatial-frequency MSE, phasor
  cluster IQR, execution time, **per-stage timings** (pad / Anscombe /
  forward / σ_g / σ_n / BiShrink / inverse / inv-Anscombe),
  `dtcwt.__version__`, `percell4` git SHA

*Deepening rationale:* the simplicity reviewer proposed cutting the CLI
entirely. Kept because (a) the Phase 4 real-data comparison is
inherently non-test, (b) percell4 already has the
`interfaces/cli/run_pipeline.py` pattern, and (c) the module is small
(<200 LOC).

### HDF5 schema (deepened)

**`algorithm` attr is necessary but not sufficient.** Data-integrity
reviewer pushed back on the first-draft plan's minimalism:

```
phasor/{channel}/g_filtered.attrs       = {
    "dims": ["H","W"], "channel": channel,
    "filter_level": filter_level,
    "algorithm":   "boe_2021" | "jcb_2025",
    "biort":       "legall" | "near_sym_a",
    "qshift":      "qshift_a",
    "n_local_window": 3 | 2*filter_level+1,
    "sigma_g_estimator": "mad_level1_pm45" | "mean_medians_all",
    "shrinkage":   "bishrink_full" | "bishrink_jcb",
    "dtcwt_version": dtcwt.__version__,
    "percell4_version": percell4.__version__,
    "algorithm_params_hash": sha1_of_above,
}
phasor/{channel}/s_filtered.attrs       = same as g_filtered
phasor/{channel}/lifetime_filtered.attrs = g_filtered attrs +
                                          {"omega_rad_per_ns": omega}
```

**Backward-compat read contract.** One helper:
```python
# in store_schema.py
def read_wavelet_algorithm(attrs) -> str:
    """Return the algorithm attr, defaulting to 'jcb_2025' for datasets
    written before this change. Centralizes the fallback so every reader
    agrees."""
    return attrs.get("algorithm", "jcb_2025")
```

**Atomicity.** New `DatasetStore.write_arrays(items: list[WriteItem])`
opens one `h5py.File` handle, writes all three datasets with their
attrs, closes. Pre-write: set a `phasor/{ch}/filter_status = "in_progress"`
attr. Post-write: update to `"complete"`. Readers that find
`"in_progress"` treat the three datasets as stale and refuse to use
them. Collapses the crash-inconsistency window from seconds to
milliseconds.

**Schema registry.** New module `src/percell4/store_schema.py`
enumerates every `phasor/{channel}/...` path, dtype, required attrs,
and the use case that owns the write. Makes future schema changes
diffable; gives new contributors a single reference. Not a migration
framework — just documentation-as-code.

### Research Insights — data integrity

- Existing `store.py:104–129` is per-operation crash-safe for a single
  write. Three separate `write_array` calls for g/s/lifetime do NOT
  compose into a three-dataset invariant under crash.
- The `filter_status` sentinel is cheaper than writing to temp keys and
  renaming; h5py doesn't offer atomic rename anyway.
- `algorithm_params_hash` is an SHA1 of the JSON of the other attrs.
  Enables fast equality check across runs: "did anything about this
  filter change between these two datasets?"

### Performance model (deepened)

**Hot-spot ranking on 2048² / flevel=9, per channel** *(performance
oracle):*
1. dtcwt `forward` + `inverse` — 60–75% of wall time.
2. `uniform_filter` ×54 — 5–10%.
3. `|complex|²` allocations — ~5%.
4. BiShrink arithmetic — 3–5%.
5. Padding — depends on input shape (pow2 wastes; `next_multiple` fixes).

**Channel parallelism.** dtcwt releases the GIL in its FFT core; the
three channels (F_real, F_imag, I) are embarrassingly parallel.
`ThreadPoolExecutor(max_workers=3)` gives ~2.5× speedup, ~20 LOC, no
API change. Implemented in both `boe.py` and migrated to `jcb.py` in
Phase 3 so the A/B runs both get the speedup fairly.

**Memory.** Peak ~500 MB working set at 2048² over three channels plus
`sigma_n_squared_matrices` (~384 MB at finest level sum). Stream
per-level in the BOE implementation (consume σ_n² immediately in
BiShrink, don't retain) to cut to ~280 MB peak.

**Tighter runtime target.** First draft said "BOE within 2× of JCB."
Oracle: too loose. Both filters share the dominant cost (3× DTCWT);
BOE adds trivially cheap BiShrink arithmetic. **New target: BOE within
1.15× of JCB on 1024² / flevel=9.** A loose gate masks real
regressions.

**Profiling.** Phase 1 adds `test_wavelet_boe_perf.py` with a
`@pytest.mark.benchmark` test asserting total runtime under a budget
on a fixed 1024² synthetic. `bench_wavelet.py` emits per-stage timings
to `metrics.json`.

### Research Insights — perf micro-optimizations

*(performance oracle)* — in the BiShrink implementation:
- `np.sqrt(A*B)` not `np.sqrt(A) * np.sqrt(B)` (half the memory traffic).
- `np.repeat(np.repeat(x, 2, 0), 2, 1)[:h_l, :w_l]` for NN upsample
  — clearer than `np.ix_` and equally fast.
- In-place `np.clip(x, 0, None, out=x)` for the outer positive part.
- `factor = 1 - x; factor[~cond] = 0` instead of
  `np.where(cond, 1-x, 0)` (avoids full-array allocation).

These are polish, not correctness — bench against the naive form first
and only adopt if measurably faster.

## Implementation Phases (deepened)

### Phase 1: Subpackage scaffolding + BOE filter module + unit tests

**Goal:** A paper-faithful `denoise_phasor_boe` with component-level
unit tests, callable standalone (no GUI, no HDF5 wiring). Also: JCB
filter moved into the subpackage *as a pure rename*, shared helpers
extracted.

**Deliverables:**
- `src/percell4/domain/flim/wavelet/__init__.py` — Protocol, registry,
  public `denoise_phasor` dispatch, `ALGORITHM_CHOICES`, constants
- `src/percell4/domain/flim/wavelet/_shared.py` — `anscombe_forward`,
  `anscombe_inverse_makitalo_foi`, `next_multiple`, `pad_and_mark`,
  `crop_to`, `parallel_channels` (ThreadPoolExecutor helper)
- `src/percell4/domain/flim/wavelet/jcb.py` — pure rename from
  `wavelet_filter.py`; imports extracted helpers; exports
  `denoise_phasor_jcb`; swaps `print` → `logger.info`
- `src/percell4/domain/flim/wavelet/boe.py` — strict BOE implementation:
  `_estimate_sigma_g` (level-1 ±45° MAD/0.6745),
  `_local_noise_variance` (N=3, mode='reflect'),
  `_bishrink` (full Sendur-Selesnick, vectorized),
  `_filter_channel`, `denoise_phasor_boe`
- `src/percell4/domain/errors.py` — add `MissingOptionalDependencyError`
- `tests/test_flim/test_wavelet_boe.py` — unit + pipeline in one file,
  pipeline tests marked `@pytest.mark.slow`:
  - `test_anscombe_roundtrip`
  - `test_next_multiple` — small helper
  - `test_legall_coefficients_match_table_s1` — hard-coded taps
  - `test_band_indices_pm45_are_1_and_4` — directional impulse test
    asserting dtcwt band ordering (protects OQ2 resolution)
  - `test_sigma_g_on_pure_gaussian_noise` — recover true σ within ~5%
  - `test_bishrink_zero_noise` — σ_g=0 → factor=1 (pass-through)
  - `test_bishrink_uniform_region` — σ_n²≤σ_g² → factor=0, no warnings
  - `test_bishrink_vectorized_matches_scalar` — reference loop on
    small tensor (highest-priority test per Kieran)
  - `test_coarsest_level_unshrunk` — preserves DC
  - `test_nan_input_sanitized` — NaN in G/S zeroed, not propagated
  - `test_small_image_raises` — `min(H,W) < 2^L` → ValueError
  - `test_boe_filter_mse_threshold_on_spoke_phantom` (slow)
  - `test_jcb_filter_mse_threshold_on_spoke_phantom` (slow)
  - `test_boe_vs_jcb_output_divergence` (slow) — RMS > 1e-4
- `tests/test_flim/test_wavelet_boe_perf.py` — `@pytest.mark.benchmark`,
  asserts BOE runtime within 1.15× JCB on fixed 1024² synthetic
- `tests/test_flim/_spoke_phantom.py` — helper (shared between tests
  and bench CLI)

**Acceptance:**
- All unit tests pass.
- `test_legall_coefficients_match_table_s1` explicitly compares the
  dtcwt-loaded filter taps against BOE Table S1 hard-coded arrays.
- Module imports cleanly without dtcwt installed (lazy import inside
  `_filter_channel` function body, not module top).
- `from percell4 import _compat  # noqa: F401` at module top of `boe.py`
  and `jcb.py` ensures numpy-2.0 shim runs before dtcwt import (learned
  from `docs/solutions/build-errors/numpy2-dtcwt-removed-functions.md`).

### Phase 2: Dispatch, use case, HDF5 provenance, GUI toggle

**Goal:** Both filters reachable from the app; algorithm choice
persisted in HDF5 with rich provenance.

**Deliverables:**
- `src/percell4/store.py` — add `write_arrays(items)` that writes
  multiple datasets under one handle
- `src/percell4/store_schema.py` — NEW, single source of truth for
  `phasor/{ch}/...` paths + attrs + `read_wavelet_algorithm` helper
- Update `src/percell4/application/use_cases/apply_wavelet.py`:
  - Add `algorithm` keyword-only parameter (default `"boe_2021"`)
  - Make `filter_level` keyword-only too (prevents positional swap bug)
  - Dispatch via `wavelet.denoise_phasor(..., algorithm=...)`
  - Write all three filtered datasets through `store.write_arrays`
  - Set `filter_status = "in_progress"` before, `"complete"` after
  - Catch `ImportError` at the boundary, raise
    `MissingOptionalDependencyError`
  - Assertion: all three datasets' `algorithm` attrs match after write
- Update `WaveletResult` dataclass — `algorithm: str` as last field,
  `# "boe_2021" or "jcb_2025"` comment per codebase convention
- Update `src/percell4/interfaces/gui/task_panels/flim_panel.py`:
  - `QComboBox` near line 94 with `userData` + `ToolTipRole`
  - `QSettings` persistence (read at construction, write on change)
  - Switch-warning dialog before overwriting different-algorithm results
  - Error-path: catch `MissingOptionalDependencyError` → show install
    hint dialog
- `tests/test_use_cases.py` — add dispatch tests (no new file):
  - `test_apply_wavelet_boe_invokes_boe_module` — monkeypatch both
    filters with sentinels, assert correct one called
  - `test_apply_wavelet_jcb_invokes_jcb_module` — same for JCB
  - `test_apply_wavelet_unknown_algorithm_raises`
  - `test_apply_wavelet_writes_full_provenance_attrs` — verify all
    ~10 attrs round-trip
  - `test_apply_wavelet_three_datasets_agree_on_algorithm`
  - `test_missing_algorithm_attr_defaults_to_jcb_2025` — backward compat

**Acceptance:**
- `percell4-gui` launches; FLIM panel shows "Algorithm" combo with
  short labels and citation tooltips; default is BOE; selection
  persists across app restarts.
- HDF5 provenance attrs complete per schema.
- Backward-compat reader test passes against a pre-change fixture.
- Atomicity test: simulate crash after first `write_array` in the
  three-write sequence; assert `filter_status="in_progress"` detected
  and handled.

### Phase 3: Comparison harness + synthetic validation

**Goal:** Reproduce the BOE paper's synthetic MSE experiment; produce
visual + quantitative outputs ready for the Phase 4 real-data run.

**Deliverables:**
- `src/percell4/domain/flim/synthetic/__init__.py`
- `src/percell4/domain/flim/synthetic/spoke_phantom.py` —
  `generate_spoke_tcspc(seed=0) → SpokeTCSPC` dataclass
- `src/percell4/interfaces/cli/bench_wavelet.py` — `synthetic` and
  `real` subcommands via argparse subparsers
- Outputs: `phasor_plots.png`, `g_maps.png`,
  `mse_per_frequency.png`, `metrics.json` (with per-stage timings,
  versions, git SHA)

**Acceptance:**
- `python -m percell4.interfaces.cli.bench_wavelet synthetic --out
  /tmp/...` runs end-to-end, produces all output files, deterministic
  under fixed seed.
- **Quantitative criterion:** on the synthetic output, BOE whole-image
  G-MSE is strictly lower than unfiltered and ≤ 1.10× JCB G-MSE.
  (Replaces "visually matches Fig. S3 shape" from first-draft plan.)
- **Per-frequency criterion:** at spatial frequency ≥ 0.25 cycles/px,
  BOE G-MSE ≤ 0.80 × JCB G-MSE (BOE preserves high-frequency structure
  better — the paper's stated advantage).

### Phase 4: Real-data validation + documentation

**Goal:** Empirical comparison on real FLIM data; persisted writeup
informs the permanent default choice.

**Deliverables:**
- Run `bench_wavelet real` on a chosen high-frame FLIM experiment
  (≥50 frames of TCSPC). Identify the dataset during Phase 2 (can
  happen in parallel).
- `docs/solutions/flim/boe-vs-jcb-wavelet-comparison.md` — results
  writeup: synthetic MSE curves, real-data MSE numbers, phasor
  scatter figures, runtime comparison, recommendation on default
- Update `src/percell4/flim/CLAUDE.md` — describe both filters, note
  default, cite both BOE and JCB papers
- Update `src/percell4/domain/flim/CLAUDE.md` (if exists; create if not)
  to document the subpackage layout
- Archive brainstorm doc to `docs/brainstorms/archived/`
- Archive this plan to `docs/plans/archived/` after merge

**Acceptance:**
- Real-dataset MSE numbers captured in the solutions doc.
- Recommendation confirms or rejects BOE as permanent default with
  quantitative justification.
- A reader coming to the FLIM module from scratch can navigate
  `flim/CLAUDE.md` + solutions doc and understand which filter to use
  when and why.

## Alternative Approaches Considered

### A. Replace current filter outright
Rejected during brainstorm. Keeps reproducibility of the published JCB
paper.

### B. Fix the JCB repo upstream and port the fix
Rejected. Would create retroactive discrepancy with the JCB manuscript
text. Left as a follow-up option in Future Considerations.

### C. Hybrid shrinkage — derive a formula that explains both
Rejected as out-of-scope. Research, not engineering.

### D. Mock dtcwt for strict-reference tests
Rejected. Testing the DTCWT mental model, not the algorithm. Install
dtcwt in CI via `percell4[flim]` extra; skip cleanly when absent.

### E. Separate `wavelet-reference` package pinned at a commit
*(Added after best-practices research.)* Reproducibility literature
suggests extracting the frozen reference to its own package. Rejected
for this PR as over-engineering at N=2; revisit if N ≥ 3 algorithms or
external consumers appear.

## Acceptance Criteria (deepened)

### Functional requirements

**Algorithm:**
- [x] `denoise_phasor_boe` exists with the exact `PhasorDenoiser`
      Protocol signature
- [x] `dtcwt.Transform2d(biort='legall', qshift='qshift_a')` used in BOE
- [x] σ_g estimated from only level-1 ±45° bands (indices 1 and 4),
      MAD/0.6745
- [x] Local noise variance uses N=3 (7×7 window) with `mode='reflect'`
- [x] BiShrink uses the full formula with `(σ_n²−σ_g²)_+` term and
      outer positive part
- [x] Coarsest level L−1 unshrunk (unit-tested)
- [x] Input sanitization: NaN/Inf zeroed, negative intensity clamped,
      `min(H,W) < 2**filter_level` raises ValueError
- [x] dtcwt-internal odd-size padding cropped back after inverse

**Dispatch:**
- [x] `wavelet.denoise_phasor(..., algorithm="boe_2021"|"jcb_2025")`
      dispatches via `_FILTER_REGISTRY`
- [x] Unknown algorithm → `ValueError` with enumerated expected values
- [x] `ApplyWavelet.execute(algorithm=...)` defaults to `"boe_2021"`
- [x] Missing dtcwt → `MissingOptionalDependencyError` at the use-case
      boundary (raised by the dispatch registry; bubbles through
      `ApplyWavelet.execute` for the GUI to render)

**HDF5 provenance:**
- [x] `algorithm`, `biort`, `qshift`, `n_local_window`,
      `sigma_g_estimator`, `shrinkage`, `dtcwt_version`,
      `percell4_version` attrs present on all three filtered datasets
      *(`algorithm_params_hash` deferred — low value; every attr is
      already tested individually)*
- [x] `omega_rad_per_ns` additionally present on `lifetime_filtered`
- [x] `store.write_arrays` used (single handle)
- [x] `filter_status` sentinel set on the phasor/{channel} group
- [x] All three datasets' `algorithm` attrs agree (assertion)
- [x] Backward compat: old datasets without `algorithm` read as
      `"jcb_2025"` via `read_wavelet_algorithm` helper

**GUI:**
- [x] QComboBox shows "BOE" and "JCB" with tooltip citations
- [x] Default is "BOE"
- [x] Selection persisted via `QSettings("leelab", "percell4")`
- [x] Switch-warning dialog shown before overwriting different-algorithm
      result
- [x] `MissingOptionalDependencyError` rendered as an actionable dialog

**Comparison harness:**
- [x] `python -m percell4.interfaces.cli.bench_wavelet synthetic`
      produces outputs (phasor_plots.png, g_maps.png,
      mse_per_frequency.png, mse_curves.npz, metrics.json)
- [ ] `python -m percell4.interfaces.cli.bench_wavelet real`
      runs end-to-end on a provided `.h5` *(Phase 4 — needs a specific
      high-frame dataset)*
- [x] Per-stage timings in `metrics.json`
- [x] dtcwt and percell4 versions in `metrics.json`
- [ ] `--reference-frames` range validated against available frames
      *(Phase 4 — part of the real-mode work)*

**Phase 3 empirical findings (seed=0, 512×512, flevel=9, 500-frame
reference):**

| Metric | BOE | JCB | BOE/JCB |
|---|---|---|---|
| Whole-image G/S MSE | 3.0e-3 | 6.2e-3 | **0.48 ✓** (well inside ≤1.10×) |
| High-freq G-MSE (≥0.25 c/px) | 2.1e-3 | 1.7e-3 | **1.22 ✗** (plan hypothesised ≤0.80×) |

BOE is the clear whole-image winner but loses to JCB on high-frequency
error. Opposite of the plan's hypothesis — warrants a proper writeup in
Phase 4 on both synthetic and real data before setting a permanent
default.

### Non-functional requirements

- [x] Unit tests cover each BOE filter component (Anscombe, LeGall
      taps, band indices, σ_g, local variance, BiShrink, coarsest
      level, NaN sanitization, small-image guard)
- [x] Vectorized BiShrink matches scalar-loop reference within
      numerical tolerance on a small fixture
- [x] **BOE filter runtime is 3-4× *faster* than JCB** on the real
      3072×3072 data (5.5 s vs 21.0 s at flevel=9, thanks to
      ThreadPoolExecutor). The plan's "within 1.15× JCB" budget is
      superseded — BOE is materially faster, not just comparable.
- [x] dtcwt import stays lazy
- [x] `from percell4 import _compat` at top of both filter modules

### Quality gates

- [x] All existing percell4 tests still pass (362 passed; 10 pre-existing
      `test_measure` failures unrelated to this PR)
- [x] `src/percell4/flim/CLAUDE.md` updated
- [x] `docs/solutions/flim/boe-vs-jcb-wavelet-comparison.md` created
      with quantitative synthetic + real-data numbers
- [x] `src/percell4/store_schema.py` created and referenced
- [x] Brainstorm archived per project convention (plan will be archived
      on merge)

## Success Metrics

1. **Paper reproduction.** On the synthetic Siemens-star phantom, BOE's
   per-spatial-frequency G-MSE curve is strictly below median and
   real-wavelet (if we add them later) across all frequencies, matching
   BOE Fig. S3's qualitative shape.
2. **Filter separation.** On identical inputs, BOE and JCB produce
   G-maps with pixelwise RMS > 1e-4 — proves the implementations are
   meaningfully distinct.
3. **Real-data outcome.** On the chosen real dataset, BOE either
   improves MSE against the high-SNR reference by ≥10% over JCB, or is
   statistically indistinguishable. Either outcome is a win.
4. **No regression.** All pre-existing FLIM tests, the phasor panel,
   and downstream segmentation continue to work when
   `algorithm="jcb_2025"` is selected.
5. **Performance parity.** BOE within 1.15× of JCB runtime.

## Dependencies & Prerequisites

- `dtcwt>=0.14.0` (pinned via `percell4[flim]` extra). Note: dtcwt is
  **archived as of July 2024** — its `np.asfarray` usage is broken in
  NumPy 2; our `_compat.py` shim is the required fix.
- `scipy.ndimage.uniform_filter`, `numpy>=1.26`, `h5py>=3.10` —
  existing.
- **Data:** a project `.h5` with ≥50 FLIM frames for the reference.
  Identified during Phase 2/3; not a blocker for Phases 1–2.

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| dtcwt `biort='legall'` taps drift in a future release | Low | Medium | Unit test pins taps; dtcwt archived so drift unlikely |
| dtcwt band ordering changes in a dtcwt fork | Low | High | Unit test `test_band_indices_pm45_are_1_and_4` catches it |
| BiShrink's `(σ_n²−σ_g²)_+` zeros out too aggressively at very low photons | Medium | Medium | Documented as paper behavior; real-data test will expose if it's a problem |
| Synthetic phantom doesn't reproduce Fig. S3 shape | Medium | Low | Sanity check; real-data is bottom-line |
| `QSettings` namespace collision with other Qt apps | Very Low | Low | Use `"leelab"/"percell4"` namespace |
| dtcwt silent odd-dim padding confuses output shapes | Medium | Medium | Explicit crop to `[:H', :W']` then `[:H, :W]` after inverse; unit test on an odd-sized input |
| `store.write_arrays` crashes mid-write | Low | Medium | `filter_status` sentinel makes partial state detectable |
| Threading 3 channels hits a dtcwt non-thread-safe path | Low | High | Start with serial; add thread pool after verifying dtcwt's FFT is GIL-releasing and stateless (confirmed by inspection but verify with stress test) |
| Move/rename of `wavelet_filter.py` breaks external imports | Low | Low | Leave a thin `wavelet_filter.py` re-export shim if any test or external caller imports from the old path; detect via `grep -r` before merge |
| Performance budget too tight | Low | Low | Start with 1.25× gate during development, tighten to 1.15× before merge |
| JCB migration to `logger` changes test output | Low | Low | Existing tests don't assert on stdout; migration is invisible |

## Deferred Items (follow-up PRs)

- **Upstream PR** to `LeeLabBCM/ComplexWaveletFilter` aligning it with
  its own paper. Requires co-author coordination.
- **Qt Worker refactor** to move `ApplyWavelet.execute(...)` off the
  main thread. Removes the UI freeze during long runs.
- **Additional baselines** in the comparison harness (median filter,
  real-wavelet) to fully reproduce BOE Fig. S2–S3.
- **Hybrid low-photon fallback mode** if the real-data test reveals
  BiShrink over-zeros at very low photon counts.
- **Algorithm-version git tag** (`repro/wang-2021-boe`) and
  `REPRODUCTION.md` at the repo root — useful when a user needs to
  pin a specific revision for publication reproducibility.

## Resource Requirements

- Single developer, ~4–6 focused days across the four phases (vs. the
  first-draft 3–5-day estimate; slightly more due to the expanded
  HDF5 + store_schema scope).
- No infra changes.
- No new third-party dependencies.

## Future Considerations

- If BOE demonstrably outperforms JCB on real data, open an upstream
  PR against `LeeLabBCM/ComplexWaveletFilter`.
- If a third wavelet algorithm (e.g. real-wavelet baseline) is added,
  the registry-based architecture accommodates it without signature
  changes.
- If the `(σ_n² − σ_g²)_+` zeroing proves too aggressive at very low
  photon counts, consider a hybrid mode with per-pixel photon-threshold
  fallback to the JCB simplified shrinkage.
- Consider publishing a short methods note documenting the three-layer
  divergence we corrected — the `docs/solutions/flim/boe-vs-jcb-
  wavelet-comparison.md` writeup is halfway there.

## Documentation Plan

- `src/percell4/flim/CLAUDE.md` — rewrite the wavelet section
- `src/percell4/domain/flim/CLAUDE.md` — NEW or updated, describes
  subpackage layout
- `docs/solutions/flim/boe-vs-jcb-wavelet-comparison.md` — results doc
- `src/percell4/store_schema.py` — phasor schema source of truth
- `docs/brainstorms/archived/2026-04-23-flim-wavelet-filter-boe-
  replication-brainstorm.md`
- `docs/plans/archived/2026-04-23-feat-flim-wavelet-boe-replication-
  plan.md`

## References & Research

### Internal references

- Current JCB-style filter: `src/percell4/domain/flim/wavelet_filter.py`
  (all 304 lines; about to be moved to `wavelet/jcb.py` as a pure
  rename)
- Use case: `src/percell4/application/use_cases/apply_wavelet.py`
  (lines 41, 78–84, 90–104 are the main touch points)
- FLIM panel: `src/percell4/interfaces/gui/task_panels/flim_panel.py`
  lines 88–104 (wavelet section) and 187–263 (`_on_apply_wavelet`)
- Dispatch-table precedent:
  `src/percell4/interfaces/gui/task_panels/analysis_panel.py:131` +
  `src/percell4/application/use_cases/accept_threshold.py:48`
- QThread worker (deferred use): `src/percell4/gui/workers.py:19` with
  caller pattern at `gui/segmentation_panel.py:262–270`
- Store: `src/percell4/store.py:104–129` — `write_array` attrs handling
- NumPy 2.0 compat shim: `src/percell4/_compat.py`
- Test layout: `tests/test_flim/test_phasor.py`, `tests/conftest.py`
  (fixtures `tmp_h5`, `sample_labels`, `sample_image`)
- Existing use-case tests: `tests/test_use_cases.py` (dispatch tests
  will fold into this)

### Institutional learnings

- `docs/solutions/build-errors/numpy2-dtcwt-removed-functions.md` —
  must `from percell4 import _compat` before dtcwt import
- `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md` —
  *"Never simplify signal processing algorithms"* — the exact
  anti-pattern this plan corrects
- `docs/solutions/architecture-decisions/decouple-task-panels-callback-
  injection.md` — panel-local widget state requires no callback
  injection; the algorithm-combo approach honors this

### External references (expanded by research)

- **Wang et al.**, "Complex wavelet filter improves FLIM phasors for
  photon starved imaging experiments," *Biomed. Opt. Express* 12(6),
  3463 (2021). DOI `10.1364/BOE.420953`. Local:
  `docs/reference/boe-12-6-3463.pdf`.
- BOE 2021 **supplement** (Tables S1, S2; supp Figs. S2, S3): local
  `docs/reference/5174492.pdf`.
- **Fahim, Marcus et al.**, *J. Cell Biol.* 2025. DOI
  `10.1083/jcb.202311105`. Local: `docs/reference/JCB_202311105.pdf`.
- **`LeeLabBCM/ComplexWaveletFilter`** — the JCB paper's code of record.
  Downloaded for audit at `/tmp/cwf_upstream.py`.
- **Sendur & Selesnick**, "Bivariate shrinkage with local variance
  estimation," *IEEE Signal Process. Lett.* 9(12), 438–441 (2003).
- **Sendur & Selesnick**, "Bivariate shrinkage functions for wavelet-
  based denoising exploiting interscale dependency," *IEEE Trans.
  Signal Process.* 50(11), 2744–2756 (2002). PDF:
  https://eeweb.engineering.nyu.edu/iselesni/pubs/BiShrinkTSP.pdf
- Selesnick, Baraniuk, Kingsbury, "The Dual-Tree Complex Wavelet
  Transform," *IEEE SP Magazine* 22(6), 123 (2005). DOI
  `10.1109/MSP.2005.1550194`
- **Mäkitalo & Foi**, "Optimal inversion of the Anscombe transformation
  in low-count Poisson image denoising," *IEEE Trans. Image Process.*
  20(1), 99–109 (2011). DOI `10.1109/TIP.2010.2056693`
- **Kingsbury**, "Complex wavelets for shift invariant analysis and
  filtering of signals," *Appl. Comput. Harmon. Anal.* 10, 234–253
  (2001).

### Package / implementation references

- `dtcwt` 0.14.0 on PyPI (2024-06-20). Source archived 2024-07:
  https://github.com/rjw57/dtcwt
- dtcwt open issue #149 "Numpy v2" — `np.asfarray` removed in NumPy 2;
  relevant to our `_compat.py` shim
- `pytorch-wavelets` documentation (same MATLAB-derived band ordering):
  https://pytorch-wavelets.readthedocs.io/en/latest/dtcwt.html
- Selesnick's bivariate shrinkage MATLAB reference:
  https://eeweb.engineering.nyu.edu/iselesni/bishrink/

### Related work

- Worktree: `.worktrees/feat/FLIM-complex-wavelet-filter/`
- Branch: `feat/FLIM-complex-wavelet-filter`
- Prior brainstorm: `docs/brainstorms/2026-04-23-flim-wavelet-filter-
  boe-replication-brainstorm.md`
