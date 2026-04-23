---
date: 2026-04-23
topic: flim-wavelet-filter-boe-replication
---

# FLIM Complex Wavelet Filter — BOE Paper Replication

## What We're Building

A strict reimplementation of the dual-tree complex wavelet filter (CWF) for
FLIM phasor denoising as specified in Wang et al., *Biomedical Optics Express*
12(6) 3463 (2021) — the Leica-collaboration paper — and a side-by-side
comparison against the filter currently shipping in percell4.

The goal is a clean, paper-faithful reference implementation that can be run
on the same inputs as the current filter, so we can measure how far the
current code drifts from the published algorithm and decide which (if either)
to keep as the production filter.

Work happens on the worktree branch `feat/FLIM-complex-wavelet-filter`.

## Why This Approach

The current percell4 filter is a port of `LeeLabBCM/ComplexWaveletFilter`
(the user's own JCB 2025 paper code). Audit showed two layers of divergence
from the BOE paper:

1. **JCB repo vs. BOE paper** (pre-existing, published): global noise uses
   every level × 6 bands (paper: level-1 ±45° only); shrinkage drops the
   `(σ_n² − σ_g²)_+` factor — `σ_n²` is computed but only gates whether to
   shrink, not the shrinkage magnitude.
2. **Percell4 vs. JCB repo** (port regression): `biort='near_sym_a'` instead
   of `biort='Legall'`; local-noise window capped at 7×7 for `flevel > 10`.

Before modifying either, we want a clean BOE reference to test against. An
A/B comparison is the only way to know whether the JCB simplifications
improve, degrade, or are equivalent to the strict paper algorithm on real
FLIM data.

## Key Decisions

- **Target**: strict Wang et al. 2021 BOE specification, including supplement
  equations (1)–(3) and filter-bank choices from Tables S1–S2.
- **Coexistence**: ship the new BOE filter alongside the existing filter;
  do not replace the current implementation until comparison results exist.
  Current code stays unchanged on this branch so the comparison is apples
  to apples.
- **Exposed as**: a GUI toggle (radio/dropdown) in the FLIM panel next to
  "Apply Wavelet Filter", letting the user pick between the current
  "JCB 2025" algorithm and the new "BOE 2021" algorithm. Kept as a
  permanent toggle after the comparison (BOE becomes the default; JCB is
  preserved so users can still reproduce the published JCB paper).
- **Required algorithm pieces** (from supplement):
  - DTCWT with LeGall 5,3 analysis filters at level 1 and Kingsbury Q-shift
    10,10 at higher levels (Tables S1, S2). Must verify dtcwt's
    `biort='legall'` matches Table S1 coefficients.
  - Global noise estimate σ_g² = `median(|Φ(1, b, x, y)|) / 0.6745`, where
    `b ∈ {+45°, −45°}` — first level, diagonal bands only.
  - Local noise variance σ_n²(l, b, x, y) = mean of |Φ|² in a
    (2N+1)×(2N+1) neighborhood. N from Sendur & Selesnick (typically 3).
  - Inter-scale BiShrink:
    `Φ'(l, b, x, y) = (1 − √3·σ_g² / √((|Φ(l)|² + |Φ_parent|²)·(σ_n² − σ_g²)_+))_+ · Φ(l)`
  - Parent = `Φ(l+1, b, ⌊x/2⌋, ⌊y/2⌋)` (nearest-neighbor upsample already
    done correctly in current code).
  - Anscombe / inverse Anscombe on Freal, Fimag, I — applied independently
    as in current code (paper eq. 6, 7, 8).
- **Reference points in the code**:
  - Current filter: `src/percell4/domain/flim/wavelet_filter.py` (keep, no
    edits on this branch until comparison is done).
  - New BOE filter: a sibling module (name TBD at plan time).
- **Tests**: the first unit tests for this filter. Synthetic ground truth
  (known lifetime + Poisson noise) so we have an MSE target.

## Comparison Plan

- **Datasets**: both synthetic and real.
  - *Synthetic*: mirror BOE supp Fig. 2 — a Siemens-star / spoke pattern
    with two components (1.5 ns and 2.5 ns), 0.1 photons/pulse at 80 MHz,
    3 µs dwell. 500-frame accumulation is ground truth; single frame is
    the test input. This lets us directly reproduce the paper's MSE curves
    as a sanity check.
  - *Real*: one high-frame FLIM experiment from an existing project `.h5`
    where enough frames exist to average a clean reference. Specific
    dataset TBD at plan time — aim for low-photon, biologically meaningful
    (e.g. mitochondrial or membrane staining).
- **Metrics**: MSE vs. high-SNR ground truth on G, S, and lifetime maps
  (both whole-image and binned by spatial frequency, like BOE Fig. S3);
  phasor cluster scatter (IQR of G/S within a uniform-lifetime region);
  visual side-by-side on the real image.
- Expected outcomes — not decisions, just hypotheses to test:
  - BOE's `(σ_n² − σ_g²)_+` term gives stronger adaptive shrinkage in
    high-local-variance regions (edges), potentially preserving fine
    structure better than the current code.
  - BOE's level-1 ±45°-only noise estimate gives a cleaner σ_g (less
    contaminated by signal coefficients in lower-frequency bands), so
    shrinkage is less aggressive on true signal.
  - Or: the JCB simplifications may be close enough that the difference is
    negligible on real data, which would be worth knowing.

## Resolved Questions

1. **Ground-truth dataset** — Both. Synthetic spoke pattern mirroring BOE
   supp Fig. 2 (for MSE-vs-spatial-frequency reproduction), plus one real
   high-frame FLIM dataset from the project (for biological sanity check).
2. **User-facing exposure** — GUI toggle in the FLIM panel: radio or
   dropdown selecting between "JCB 2025" and "BOE 2021" algorithms. Same
   "Apply Wavelet Filter" button, results land in the same HDF5 slots.
3. **Disposition after comparison** — Keep the toggle permanently. BOE
   becomes the default; the JCB algorithm stays available (labeled "JCB
   2025 (legacy)" or similar) so existing users can reproduce the
   published paper. No upstream PR to `LeeLabBCM/ComplexWaveletFilter`
   planned at this time.

## Open Questions (plan-time / technical)

1. **dtcwt `biort='legall'` coefficient verification.** The dtcwt package
   has a `legall` option; confirm its tap coefficients match Table S1 of
   the BOE supplement before trusting it for level-1. If mismatched, may
   need custom coefficients supplied via dtcwt's lower-level API.
   (Empirically verifiable — resolve during implementation, not now.)
2. **N for the local-noise window.** BOE supplement doesn't state N.
   Default to **N=3** (Sendur & Selesnick [2] standard → 7×7 window) as
   the strict BOE choice. Expose as an optional parameter for the
   comparison harness; do not expose in GUI.

## Next Steps

→ Resolve open questions 1–2 (ground truth + exposure) at minimum.
→ `/workflows:plan` for implementation details (new module layout, test
   fixtures, comparison harness).
