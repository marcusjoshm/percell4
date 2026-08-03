---
title: Phasor segmentation — intensity / reference-circle filters + GMM ROI placement
status: open
created: 2026-05-03
type: feature-requirements
related:
  - docs/brainstorms/2026-04-17-phasor-roi-separate-masks-brainstorm.md
  - docs/brainstorms/2026-04-30-phasor-mask-filter-requirements.md
  - docs/plans/2026-03-27-feat-multi-roi-phasor-masks-plan.md
reference_implementations:
  - ~/ComplexWaveletFilter/Circular_ROI_lifetime.py
  - ~/ComplexWaveletFilter/CondensedPhaseGMM.py
---

# Phasor segmentation — intensity / reference-circle filters + GMM ROI placement

## Problem

The phasor plot already supports manually-drawn elliptical ROIs that the user nudges with the mouse and a spinbox. For datasets with many overlapping populations — condensed vs. dilute phase, autofluorescence vs. label, multiple metabolic states — manual placement is slow, subjective, and hard to reproduce. The reference scripts in `~/ComplexWaveletFilter/` show the intended automation: filter the phasor (by intensity, by a circular ROI anchored to a target lifetime on the universal circle, or by a binary mask), fit a Gaussian mixture, and place ROIs whose center, axes, and angle come from each component's mean and covariance. Users can then refine each ROI by stretching it (cov_f scaling on eigenvalues) and shifting it along its own principal axis. None of this exists in PerCell4 today; users currently drag rectangles by hand and eyeball where they should be.

## User outcome

For a typical FLIM workflow the user can:

1. Compute a phasor (existing flow).
2. Toggle filters — intensity threshold, reference circle at a target tau, or active mask — and watch the phasor histogram immediately restrict to the filtered region. The filter set is the GMM's input.
3. Click "Run GMM" with a chosen shape (circle or ellipse) and either a fixed cluster count or auto-selection by BIC/AIC. Each cluster appears as a new entry in the existing phasor ROI list, colored, named `GMM_1`, `GMM_2`, ..., with center, axes, and angle derived from the fit.
4. Select any GMM-origin ROI and nudge its `cov_f` (stretch) or `shift` (translate along principal axis) spinbox in the Selected-ROI panel — the ROI updates live on the phasor plot.
5. Click "Apply Visible as Mask" to commit each ROI to its own binary mask in HDF5 (existing flow, unchanged).

GMM never overwrites manual ROIs; running it again simply appends more entries. The user owns cleanup via the existing Remove button.

## Requirements

### R1. Three new filter controls — intensity threshold + reference circle (FLIM tab); active mask (existing toolbar checkbox)

A new "Phasor Filters" group in the FLIM task panel exposes:

- **Intensity threshold** — spinbox, photon counts. Default `0` (no filter). Pixels with `intensity < threshold` are excluded. Uses the same `/decay/<channel>.sum(axis=-1)` intensity field already used for the phasor histogram weighting.
- **Reference circle** — checkbox + (tau ns spinbox, radius spinbox). When enabled, places a circular boundary centered at `(G_c, S_c)` on the universal semicircle, computed from the current harmonic + the dataset's `flim_frequency_mhz` metadata using the same solver as `CondensedPhaseGMM.calculate_g_and_s`. Default tau = 2.5 ns, default radius = 0.5 (phasor units). Pixels outside the reference circle are excluded.
- **Active mask filter** — unchanged. The existing checkbox on the phasor plot toolbar stays where it is.

All three filters compose with each other and with the existing cell-selection filter (`session.filter_ids`) via boolean AND on a per-pixel `valid` array. This is the same composition pattern used today by `domain.flim.phasor_display.compute_valid_phasor_pixels`.

**Display + GMM input.** Filters restrict both the on-screen histogram AND the GMM's input pixels. There is no separate "fit on a slice but show everything" mode. Toggling any filter immediately refreshes the histogram (existing 150 ms `_filter_timer` debounce).

### R2. "Phasor Segmentation" group in the FLIM tab

A new group below "Phasor Analysis" containing:

- **Shape** combo — `Circle` | `Ellipse`. Single choice per run. Applies to all clusters produced by that run.
- **N clusters** — checkbox `Auto` + spinbox (1–10, default 2).
  - When `Auto` is checked, the spinbox becomes the *upper bound* of the BIC/AIC sweep.
  - When `Auto` is unchecked, the spinbox is the exact n.
- **Auto criterion** combo — `BIC` | `AIC`. Visible only when `Auto` is checked.
- **cov_f (stretch)** spinbox — default 2.0, range 0.5–5.0. The eigenvalue multiplier that controls how much of the cluster the ROI encloses (≈ 2σ at default).
- **Shift** spinbox — default 0.0, range −2.0 to +2.0. Initial shift applied to every newly-placed ROI (most users leave this at 0 and tune per-ROI later).
- **"Run GMM"** button.

The cov_f and shift values in this panel are the *initial* values for newly-placed ROIs. After placement, each ROI's cov_f and shift can be edited individually in the Selected-ROI panel (R4).

### R3. GMM fit and ROI placement

When the user clicks "Run GMM":

1. Read the active `(g, s)` maps (filtered or unfiltered, per the existing `Filtered` checkbox on the phasor plot).
2. Build the same `valid` mask the histogram uses (R1's composition).
3. Sample at most `MAX_GMM_PIXELS` (default 100,000) valid pixels, weighted by intensity. Sampling avoids the `np.repeat` memory blowup that `CondensedPhaseGMM.py` accepts as a price for full intensity weighting. For pixel counts below the cap, no subsampling.
4. Fit `sklearn.mixture.GaussianMixture` on the sampled `(g, s)` pairs:
   - If `Auto` off: fixed `n_components`.
   - If `Auto` on: fit for `n=1..n_max`, pick lowest BIC or AIC per criterion. Status bar reports the chosen n.
5. For each component i, compute eigenvalues + eigenvectors of `cov_matrices[i]`. The principal axis is the eigenvector of the larger eigenvalue.
6. Build a `PhasorROI` per component:
   - `center` = `means_[i]` shifted by `(shift × sqrt(λ_major)) × (cos θ, sin θ)`.
   - For Ellipse: `radii = (cov_f × sqrt(λ_major), cov_f × sqrt(λ_minor))`, `angle_deg = atan2(eigvec_major)` in degrees.
   - For Circle: `radii = (cov_f × sqrt(λ_minor), cov_f × sqrt(λ_minor))` (radius = stretched smaller eigenvalue, matching `Circular_ROI_lifetime.py` line 169), `angle_deg = 0`.
   - `name = "GMM_<n>"`, with the existing rename-suffix collision handling.
   - `color` cycled from `COLOR_CYCLE`.
   - `origin = "gmm"` (new field — see R5 metadata).
   - `principal_angle_rad`, `lambda_major`, `lambda_minor`, `mean_g`, `mean_s` stored alongside (new fields — preserved so cov_f/shift edits in R4 stay eigenstructure-aware).

GMM ROIs are **appended** to the existing list. Manual ROIs and prior GMM ROIs are not removed. Status bar reports `"GMM placed N ROIs (criterion=BIC, n=3, sampled 100,000 pixels)"`.

### R4. Per-ROI shift / stretch in the Selected-ROI panel

The existing Selected-ROI panel grows two spinboxes for GMM-origin ROIs only:

- **cov_f** spinbox — default = the value at fit time. Range 0.1–10.0.
- **Shift** spinbox — default = 0.0. Range −5.0 to +5.0.

Editing either spinbox recomputes `center` and `radii` from the stored eigenstructure (`mean_g`, `mean_s`, `lambda_major`, `lambda_minor`, `principal_angle_rad`) and updates the RectROI + ellipse curve live. The histogram's preview updates via the existing `_preview_timer`.

**Manual drag interaction.** Dragging the RectROI (existing behavior) sets `center` / `radii` directly and does not change `cov_f` / `shift`. Editing cov_f or shift afterward *snaps the ROI back* to the eigenstructure-derived position with the new scalars applied — this is intentional, so users have a clear "regenerate from fit" affordance. The angle spinbox already exists and is not replaced; for GMM-origin ROIs its initial value is the principal-axis angle.

For manually-drawn ROIs (origin = "manual"), the cov_f and shift spinboxes are hidden (no eigenstructure to operate on).

### R5. PhasorROI gains `origin` + GMM metadata fields

```python
@dataclass
class PhasorROI:
    name: str
    center: tuple[float, float]
    radii: tuple[float, float]
    angle_deg: float
    label: int
    color: str
    visible: bool = True
    origin: str = "manual"           # new: "manual" | "gmm"
    gmm_fit: GMMFit | None = None    # new: present iff origin == "gmm"

@dataclass
class GMMFit:
    mean_g: float
    mean_s: float
    lambda_major: float          # larger eigenvalue
    lambda_minor: float          # smaller eigenvalue
    principal_angle_rad: float   # angle of major eigenvector
    cov_f: float                 # current scalar
    shift: float                 # current scalar
    shape: str                   # "circle" | "ellipse"
    criterion: str | None        # "BIC" | "AIC" | None (manual n)
    sampled_pixels: int          # how many pixels the fit saw
```

`from_dict` / `to_dict` JSON serialization extends to round-trip these fields. Existing JSON files (origin field absent) load as `origin="manual"` for backwards compat in the Save/Load ROIs flow.

### R6. Composition with existing pipelines

- **Apply Visible as Mask** — unchanged. Each visible ROI (manual or GMM) writes to `/masks/<roi_name>` as a binary uint8 array, exactly as today (`2026-04-17-phasor-roi-separate-masks-brainstorm.md`).
- **Live preview overlay** in napari — unchanged. Both manual and GMM ROIs feed the same combined-mask preview.
- **Save / Load ROIs** — JSON gains `origin` + `gmm_fit`. Loading old JSON works (origin defaults to manual).
- **Cell-selection filter** — composes with the new filters via the same boolean AND already used.
- **Filtered (wavelet) checkbox** — GMM runs on whichever `(g, s)` pair is currently displayed.

### R7. Performance

- Filter composition cost: one boolean AND per filter on a flat `(H*W,)` array — no measurable refresh hit.
- GMM cost: with the 100k-pixel cap, fit time stays under ~1 s for typical 1024×1024 datasets across the BIC/AIC sweep up to n=6.
- ROI placement / shift / stretch: O(K_ROIs) ellipse curve updates, no full refit.

GMM should run on a `QThread` worker (existing `gui/workers.py` pattern) so the UI does not freeze during the auto-criterion sweep.

## UI sketch

```
FLIM Tab
├── Phasor Analysis     [unchanged]
│   ├── Harmonic: [1]
│   ├── [Compute Phasor]
│   └── [Open Phasor Plot]
│
├── Phasor Filters      ← new
│   ├── Intensity ≥ [    0]
│   ├── ☐ Reference circle  τ = [2.5] ns  r = [0.50]
│   └── (Active mask filter is on the phasor plot toolbar)
│
├── Phasor Segmentation ← new
│   ├── Shape:    [Ellipse ▾]
│   ├── ☑ Auto    [n_max ▴ 6]
│   ├── Criterion [BIC ▾]
│   ├── cov_f:    [2.0]
│   ├── Shift:    [0.0]
│   └── [Run GMM]
│
├── Wavelet Filter      [unchanged]
└── Lifetime Map        [unchanged]
```

```
Phasor Plot Window — Selected ROI panel
├── Name: [GMM_2          ]
├── Angle: [   17°]
├── cov_f: [2.0]   ← shown only when origin == "gmm"
├── Shift: [0.0]   ← shown only when origin == "gmm"
└── ☑ Visible
```

## Acceptance criteria

A reviewer can verify the feature by:

1. Compute a phasor on a multi-population dataset.
2. In the FLIM tab, set Intensity threshold = 1000. Verify phasor histogram restricts to bright pixels.
3. Enable Reference circle (τ = 2.5 ns, r = 0.5). Verify the histogram further restricts to the reference circle. Verify the existing active-mask checkbox still composes via AND.
4. Set Shape = Ellipse, Auto = on, Criterion = BIC, n_max = 4. Click Run GMM.
5. Verify N ROIs (where N is whatever BIC chose) appear in the right-side ROI list with names `GMM_1` ... `GMM_N`, distinct colors, sensible angles.
6. Verify status bar shows the chosen n and pixel count sampled.
7. Select GMM_1. Edit cov_f from 2.0 to 3.0. Verify the ellipse grows uniformly along both axes and stays anchored to the cluster mean.
8. Edit Shift from 0.0 to 0.5. Verify the ellipse translates along its principal axis (not horizontally / vertically).
9. Drag the GMM_1 RectROI by mouse to a new spot. Verify it moves freely. Edit Shift from 0.5 to 0.0. Verify the ROI snaps back to the cluster mean (regenerate-from-fit).
10. Add a manual ROI ("Add ROI" button). Verify cov_f and shift spinboxes are hidden when the manual ROI is selected.
11. Click Run GMM again with Shape = Circle, Auto = off, n = 3. Verify three new GMM ROIs *append* to the list; manual ROI and prior GMM ROIs remain.
12. Click "Apply Visible as Mask". Verify each visible ROI writes to `/masks/<name>` as a binary uint8 array.
13. Save ROIs to JSON, clear them, Load. Verify GMM ROIs round-trip with their cov_f/shift/principal_angle preserved; manual ROIs loaded from older JSON files load as origin = "manual".

## Scope boundaries

### Deferred for later

- **Cell-aware GMM input** — fit GMM only on pixels inside `session.filter_ids` cells. The existing `compute_valid_phasor_pixels` already AND-composes filter_ids, so this falls out naturally if the user pre-selects cells; no extra work needed in this feature.
- **Per-cluster shape choice** — current scope is one shape per GMM run. Mixing circles and ellipses in the same run would require a post-fit dialog; defer.
- **GMM on wavelet-filtered + intensity-weighted samples beyond the 100k cap** — if users hit the cap and want full-dataset fits, an opt-in "no subsample" mode could be added later. Defer until someone asks.
- **Save GMM fit metadata to HDF5** alongside the masks — useful for reproducibility but not load-bearing for the immediate workflow. JSON round-trip in the ROI Save/Load file is enough today.
- **"Remove all GMM ROIs" button** — the `origin` field makes this trivial later; not needed for the first cut.
- **GMM that respects cluster covariance type beyond `full`** — sklearn's other modes (tied, diag, spherical) aren't requested.
- **Reference-circle anchor by direct (G, S)** — current scope is tau-only input. Direct (G, S) entry can be added later if users ask for it.

### Outside this product's identity

- **A new ROI editor UI parallel to the phasor plot's** — GMM produces ROIs that flow through the existing list, not a separate surface.
- **Server-side / batch GMM segmentation** — this is an interactive desktop tool; batch-mode segmentation belongs in the workflows pipeline, not here.
- **Direct cluster-membership masks (bypassing the ROI step)** — the user explicitly chose ROI-list integration. Users who want pixel-perfect GMM membership can use a circle that encloses the whole cluster (cov_f = 5+) and accept the small extra coverage.

## Dependencies and assumptions

- `flim_frequency_mhz` is reliably present in dataset metadata for any dataset where reference-circle filtering is meaningful (the user computed the phasor — they have the frequency). When metadata is missing, the reference-circle checkbox is disabled with a tooltip.
- Active phasor `(g, s)` always has shape == intensity shape == labels shape. `compute_phasor` enforces this.
- `sklearn.mixture.GaussianMixture` is available — already a transitive dep via `cellpose`. Otherwise add to `pyproject.toml`.
- The 100k-pixel sample cap is sufficient for stable GMM fits at typical dataset sizes; revisit if BIC behaves unstably on under-sampled clusters.

## Files likely touched (planning input, not implementation design)

- `src/percell4/interfaces/gui/task_panels/flim_panel.py` — add Phasor Filters group, Phasor Segmentation group, and the run-GMM handler that drives the phasor plot window via a new public method.
- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — add `origin`, GMM metadata fields to PhasorROI; add cov_f/shift spinboxes to Selected-ROI panel; add `place_gmm_rois` public method; extend Save/Load JSON.
- `src/percell4/domain/flim/phasor_display.py` — extend `compute_valid_phasor_pixels` to accept intensity threshold + reference-circle parameters.
- `src/percell4/domain/flim/phasor.py` — new pure functions: `universal_circle_gs(harmonic, tau_ns, freq_mhz)`, `gmm_fit_phasor(...)`, `gmm_eigenstructure(cov_matrix)`.
- `src/percell4/application/use_cases/run_phasor_gmm.py` — new use case that wraps the GMM fit + criterion selection (Qt-free; testable in isolation).
- `src/percell4/gui/workers.py` — new `GmmWorker` running the fit on a QThread.
- `tests/` — unit tests for the universal-circle solver, the GMM eigenstructure → ROI conversion, the per-ROI shift/stretch math, and the integration through `compute_valid_phasor_pixels`.

Detailed file changes and sequencing belong in `/ce-plan`.

## Outstanding questions

Non-blocking — flag at planning time:

- **GMM ROI naming collision with manual ROIs named `GMM_*`** — current rename-suffix collision handler (`_2`, `_3`) inherits cleanly. Confirm that suffices.
- **Subsampling determinism** — should the 100k-pixel sampler use a fixed seed so re-running GMM with the same params produces the same fit? Default yes (seed = 0); easy knob to expose later if reproducibility matters.
- **Auto sweep upper bound** — n_max default 6 was chosen because phasor populations rarely exceed that. Confirm with a real dataset before locking in.
- **Reference-circle solver edge case** — `calculate_g_and_s` from `CondensedPhaseGMM.py` uses scipy's `minimize` with a NonlinearConstraint; for typical tau values the closed-form `G = 1/(1+(ωτ)²)`, `S = ωτ/(1+(ωτ)²)` is exact and faster. Switch to the closed form during planning.
