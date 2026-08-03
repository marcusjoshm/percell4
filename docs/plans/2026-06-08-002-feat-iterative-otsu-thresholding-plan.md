---
title: "feat: Iterative Otsu peeling thresholding (headless CLI + Analysis-tab GUI)"
type: feat
status: completed
date: 2026-06-08
deepened: 2026-06-08
---

# Iterative Otsu Peeling Thresholding

## Overview

Add an **iterative Otsu autothresholding** method to PerCell4's thresholding pipeline. Instead of a single Otsu split per unit (which is dragged toward whichever pixel population dominates — so dim foci are missed when bright foci dominate the histogram), the method *peels* the image one layer at a time:

1. Run Otsu on the working image, per configurable **unit** (intensity group / individual cell / whole-field).
2. Accept the round's foreground into a cumulative mask.
3. Dilate the accepted region by ~5 px and remove it from the working image (NaN-stamp those pixels).
4. Re-run Otsu on the reduced image.
5. Repeat per unit until a **stopping criterion** says "no real foreground left," then latch that unit done.
6. When all units are done (or a hard round cap is hit), union every round's foreground into one binary `{0,1}` mask.

The hard problem the user flagged — *when only background remains, Otsu still calls ~half the pixels foreground* — is solved by a **configurable, composable stopping-criterion registry** (race several signals, tune each, combine by any/all) plus a mandatory max-rounds backstop and degenerate-unit guards. The output is a standard binary `/masks/<round>` mask, exposed both as a `percell4-batch-threshold` flag and as a Creator panel under the Analysis tab.

This is a focused sibling to the in-progress two-axis puncta registry (`docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md`): both branch off `_apply_threshold_frame` and both target the same recall problem, but iterative-Otsu is a single self-contained strategy rather than a pluggable detector × background-estimator matrix.

### Terminology (load-bearing — read before the rest)

Two words are easy to conflate; this plan uses them strictly:

- **iteration** — one peel pass *inside* a single `peel()` call (capture → dilate → NaN-stamp → re-Otsu). Bounded by `max_rounds`. Never appears in an HDF5 path. The pseudocode loop variable is `iteration`, not `round`.
- **round / `<round>`** — the *name* of one named thresholding run, i.e. the HDF5 path component `/masks/<round>` + `/groups/<round>` and the pandas column suffix. One `peel()` call produces exactly one `<round>` mask (the binary union of all its iterations). This is the same "round" `ThresholdingRound` and `_ROUND_NAME_RE` already mean.
- **scope** — the configuration choice of what an iteration thresholds: `groups`, `per-cell`, or `whole-field`.
- **unit** — one element of the chosen scope (one intensity group / one cell label / the single all-cells mask). A `peel()` call iterates over a list of units; each unit latches "done" independently.

So: "iteration 2 of the `sg_bright` round captured the dim focus in unit (cell) 7." `max_rounds` caps **iterations**, not named rounds.

---

## Problem Frame

A single Otsu threshold assumes balanced foreground/background classes. Over a whole cell or intensity group, the threshold is pulled toward the dominant population: where bright foci dominate it lands too high and **misses dim foci** (the common under-capture case); where diffuse haze dominates it lands too low and floods. The manual fix researchers use is to threshold one bright region at a time.

Iterative Otsu automates that: capture the brightest layer, remove it (plus a guard ring so its halo is not re-detected), and re-threshold the remainder so the *next* brightest foci now dominate their reduced histogram and get captured. Peeling continues per unit until the residual is indistinguishable from background.

The single subtlety that makes or breaks the method is **termination**. Otsu on a pure-noise residual still returns a split, marking roughly half the remaining pixels "foreground." Without an explicit signal-presence test, the loop would (a) never stop and (b) pollute the mask with noise. The user wants to *experiment* with several termination signals rather than commit to one up front — including their own observation that a high positive-fraction is itself the tell that Otsu is thresholding nothing.

---

## Requirements Trace

- R1. Provide an iterative-Otsu method that, per configurable unit, runs Otsu → accepts foreground → dilates and removes (NaN-stamps) the accepted region → re-runs Otsu, repeating until a per-unit stopping criterion holds, then unions all rounds into one binary mask. → U1, U3
- R2. Make the stopping decision **configurable and composable**: ship the three discussed signals (background-floor `bg + k·σ`, Otsu separability `η`, positive-fraction-high) plus additional seeded criteria, each individually toggled and parameterized, combined by any/all, with a hard `max_rounds` backstop and degenerate-unit guards so the loop always terminates. → U1, U2
- R3. Emit a binary `{0,1}` uint8 `/masks/<round>` mask fully compatible with existing downstream consumers (per-particle donut, per-particle multichannel, dilute-phase, measure). → U1, U3
- R4. Expose the method headless via a flag on `percell4-batch-threshold`, writing `/masks/<round>` + `/groups/<round>` with the same conventions and exit codes as today. → U4
- R5. Expose the method interactively as a Creator panel under the Analysis tab: runs on the active channel + segmentation, writes a new mask, auto-selects it, shows it in napari, four-step Creator contract, heavy compute in a worker thread. → U5, U6
- R6. Support a configurable **scope**: groups (reuse existing GMM/kmeans grouping), per-cell (each label independently), and whole-field (all in-cell pixels as one unit). → U1, U2, U3
- R7. Reuse existing machinery rather than reinventing it: the per-group Otsu body, `skimage.morphology.dilation(footprint=disk(r))`, NaN-safe smoothing / working-buffer discipline, `np.maximum`/`np.minimum(·,1)` union, the `ThresholdingRound` sentinel-field idiom, the `AcceptPunctaMask` use case, and the adaptive-clip GUI trio. → U1, U2, U3, U5

**Acceptance examples**

- AE1 (Covers R1, R6). A cell with a bright focus and a dim focus where single-shot Otsu captures only the bright one: iterative-Otsu captures the bright focus in iteration 1, the dim focus in iteration 2, and stops at iteration 3 (residual is background). The single `<round>` mask contains both foci.
- AE2 (Covers R2). A cell containing only background noise: the method latches the cell done within ~1 round (positive-fraction-high and/or bg-floor fires) and contributes an empty mask for that cell — it does **not** mark ~half the cell positive.
- AE3 (Covers R2). The loop always terminates: even with an adversarial or misconfigured stopping config, `max_rounds` caps iteration and the call returns.
- AE4 (Covers R1, R7). Dilation never bleeds across cell boundaries — a focus near a cell edge does not stamp or detect pixels belonging to the adjacent cell.
- AE5 (Covers R3, R4). The headless CLI writes a binary `{0,1}` uint8 mask readable by `store.read_mask`, and `percell4-batch-measure` consumes it with no changes.
- AE6 (Covers R5). The GUI Run button produces a mask that appears in napari and becomes the active mask (refresh → select), with no interactive QC step.

---

## Scope Boundaries

- **Output is a binary union mask only.** Per-round/per-iteration labeling (which round captured each pixel) is not written. (User decision: binary union.)
- **No validation harness / ground-truth scoring.** Qualifying iterative-Otsu against labeled data is the job of the separate 2026-06-03 puncta plan, not this one.
- **Not a `THRESHOLD_METHODS` entry.** That registry is whole-image, label-unaware; iterative-Otsu is unit-aware and lives in the workflow/round layer (mirrors how `puncta` was added).
- **`store.py` is not modified.** Writes go through the existing `write_mask` / `write_dataframe` contracts unchanged.
- **Segmentation is unchanged.** The method consumes existing `/labels`; it never segments.
- **Mutually exclusive with puncta mode** on a single round (a round carries `puncta` or `iterative_otsu`, not both).
- **In `per-cell`/`whole-field` scope the `/groups/<round>` table is degenerate by design** — every cell maps to a single group `1` (the masking did not use GMM/k-means grouping, so the table must not imply it did). Grouping params (`--algorithm`, `--gmm-*`, `--kmeans-*`) are inert in these scopes. See U3.
- **The GUI panel is single-frame for time-lapse** (it runs on the currently-displayed frame, matching the adaptive-clip precedent), whereas the headless CLI writes a full `(T,H,W)` stack. This is a deliberate surface asymmetry, not a parity bug.

### Deferred to Follow-Up Work

- **Batch-workflow (`WorkflowConfig`) integration.** Wiring iterative-Otsu into the multi-phase single-cell runner / config dialog as a round flavor is a follow-up; v1 ships the standalone CLI flag + the standalone Analysis-tab Creator panel (same staging the adaptive-clip module used).
- **Per-iteration diagnostic sibling array.** If capture-order inspection proves useful during tuning, a sibling non-`/masks` array recording the round index per pixel can be added later without touching the binary contract.
- **ML / learned stopping criterion.** The registry is open; a learned signal-presence classifier can be registered later but is not in v1.

---

## Context & Research

### Relevant Code and Patterns

- **Per-group Otsu body — the reuse target.** `src/percell4/workflows/phases.py::_apply_threshold_frame` (lines 603–662) and the sibling `_apply_puncta_groups` (lines 531–600). The per-group loop maps `group_id → cells_in_group → group_label_mask` via `np.isin(labels, cells)`, smooths to float32, guards `np.isfinite(...).any()` and `np.unique(...).size < 2`, thresholds `group_label_mask & (smoothed >= thr)`, and unions with `np.maximum(combined, ..., out=combined)`. Iterative-Otsu is an **outer loop around this body**, plus a third dispatch branch alongside `use_puncta`.
- **Dilute-phase peeling — the architectural twin.** `src/percell4/gui/workflows/dilute_phase/controller.py` (lines ~361–372) does exactly threshold → `dilation(accepted, footprint=disk(radius))` → `working_buffer[dilated] = np.nan` → union → repeat. The whole downstream chain (`measure_cells`, `apply_gaussian_smoothing`, per-pixel threshold) is already NaN-aware to support this. **Prefer NaN-stamping over numeric subtraction** so the existing NaN-safe Otsu guards apply.
- **Settings/round idiom.** `src/percell4/workflows/models.py`: `PunctaDetectorSettings` (frozen, `__post_init__` validation, `_normalize_params` → sorted JSON-scalar tuples for hashable run-config round-trip), added to `ThresholdingRound` as a `puncta: PunctaDetectorSettings | None` sentinel field that branches the apply phase. Mirror this exactly.
- **Names-module idiom.** `src/percell4/domain/measure/puncta_names.py` exposes skimage-free `*_NAMES` tuples so constructing settings never imports scikit-image; the registry modules assert `keys == names` at import (drift guard). Mirror for stopping-criterion names + scope names.
- **Registry idiom.** Flat `dict[str, Callable]` with a documented per-axis signature — `THRESHOLD_METHODS` (`thresholding.py`), `DETECTORS` / `BACKGROUND_ESTIMATORS` (`puncta_detectors.py`, `bg_estimators.py`). The stopping-criterion registry follows this.
- **GUI Creator trio — the exact template.** The untracked `docs/adaptive-local-clipping-core-implementation.md` documents the four files behind the Adaptive Local Clipping module: pure-domain algorithm (`domain/measure/adaptive_clip.py`) → reusable settings form (`gui/_adaptive_clip_settings.py`, frozen `current_config()` + `config_changed`) → Creator panel + pure worker body (`gui/adaptive_clip_panel.py`) → `AcceptPunctaMask` use case. The panel reads `session.active_channel`, runs a `gui.workers.Worker`, on completion calls the use case then `viewer_win.add_mask`. **Read this doc first when building U5.**
- **Use case (reuse).** `src/percell4/application/use_cases/accept_puncta_mask.py::AcceptPunctaMask` is generic: it coerces any array to `{0,1}` uint8, `write_mask` (store-before-layer), `refresh_resource_lists`, `set_active_mask`. Reuse as-is for iterative-Otsu (no new use case).
- **Analysis-tab registration.** `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (lines ~180–206) constructs `GroupedSegPanel` and `AdaptiveClipPanel` with `get_repo/get_store/get_viewer_window/show_status` callables and wraps each in a `QGroupBox`. The new panel slots in beside them.
- **CLI template.** `src/percell4/interfaces/cli/batch_threshold.py` — `main(argv) -> int`, deferred heavy imports (Qt-free `--help`, guarded by `test_batch_threshold_import_is_qt_free`), `resolve_paths`, per-dataset loop, overwrite guard via `store.array_exists`, exit `0 if n_ok > 0 else 1`, `Next: percell4-batch-measure ...` hint.
- **Morphology + connected components.** `skimage.morphology.dilation` + `disk` (no `binary_dilation` anywhere — the codebase standardized on this); `scipy.ndimage.label` / `percell4.domain.measure.particle.analyze_particles` for component counting (min-area stop criterion).

### Institutional Learnings

- **Masks are `{0,1}` uint8 — binarize at the write boundary; the store does NOT normalize.** Values >1 (or 255) render blank in napari (`DirectLabelColormap` maps only `{0,1}`). The per-group union already finishes with `np.minimum(combined, 1, out=combined)`. End every union the same way / use `(merged > 0).astype(np.uint8)`. (`docs/solutions/logic-errors/batch-compress-development-lessons.md`, `grouped-thresholding-development-lessons.md`.)
- **One payload type per HDF5 group** — masks under `/masks/`, labels under `/labels/`; never collide a mask round name with a labels name. Write via `store.write_mask`. (`docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`.)
- **Degenerate-unit guard inverts in a loop.** The single-shot code treats a constant unit (`np.unique(...).size < 2`) as "accept all pixels." In the iterative loop that would spin forever — a constant/empty **residual** means **done**, not "accept all." Make this explicit. (`grouped-thresholding-development-lessons.md` item 4.)
- **Re-clamp dilation to the unit's label mask every round** so a 5 px dilation can't leak across cell boundaries — the same `group_label_mask &` discipline the single-round code uses. (Synthesized from `phases.py` guards.)
- **Couple the `(mask, groups)` write set** and honor `--overwrite`; don't write a new mask round without refreshing/removing the sibling group table. (`in-session-hdf5-staleness-multi-vector-2026-04-30.md`.)
- **Creator four-step contract** (`store.write_mask` → `viewer.add_mask` → `refresh_resource_lists` → `set_active_mask`) applies to the GUI panel; `AcceptPunctaMask` already owns steps 1/3/4, the panel owns step 2. (`creator-contract-four-step-sequence-2026-05-18.md`.)
- **Float-safe ops on the working buffer.** Subtraction/NaN-stamping produces non-integer, possibly-negative residuals; use finite-filtered Otsu inputs and avoid integer-only ops (`np.bincount`) on the residual.

### External References

- None required. The algorithm is classical (Otsu peeling) and every primitive (Otsu, disk dilation, connected components) already exists in the codebase's skimage/scipy stack. Otsu's separability metric `η = σ²_between / σ²_total` is computed directly from the class statistics at the chosen threshold.

---

## Key Technical Decisions

- **Removal = NaN-stamp, not numeric subtraction.** Stamping accepted+dilated pixels to NaN in a float32 working buffer (a) excludes them from the next Otsu via finite-filtering, and (b) matches the dilute-phase twin's removal step. Numeric subtraction would distort the histogram with a zero spike and risk negative values. (Resolves the user's "subtract from the image" step.)
- **Smooth once, before the loop (v1).** Unlike the dilute-phase twin — which re-runs `apply_gaussian_smoothing` every round so a freshly-exposed edge is re-blurred against its now-NaN neighbors — `peel()` smooths the channel **once** up front and then only NaN-stamps. Rationale: each iteration's Otsu runs on `working[unit_mask][isfinite]`, and finite-filtering already excludes stamped pixels, so a per-iteration re-smooth changes the Otsu input without a clear recall benefit and costs a full convolution per iteration. Consequence to accept: because smoothing is not re-applied, the NaN-safe `nan_safe_gaussian_filter` path is *not* exercised by this loop (finite-filtering carries the NaN-safety instead). A per-iteration re-smooth toggle is a deferred option, not v1.
- **Stop-criterion params are namespaced; `params_for(name)` is a prefix filter.** All per-criterion params live in one flat `stop_params` bag (hashable, run-config-round-trippable, mirroring `PunctaDetectorSettings.detector_params`), but keys are **dotted-namespaced by criterion**: `"bg-floor.k"`, `"peak-prominence.k"`, `"positive-fraction-high.max_frac"`, etc. This prevents the real collision where `bg-floor` and `peak-prominence` both want a key named `k`. `params_for(name)` selects the `"<name>."`-prefixed entries and strips the prefix before handing them to the predicate. CLI flags (`--bg-floor-k`) and GUI spinboxes map 1:1 onto these dotted keys.
- **`peel()` returns a report, not just a mask.** It returns `(mask, IterativeOtsuReport)` where the report carries `n_iterations_run`, `units_total`, `units_hit_max_rounds`, per-criterion fire counts, and `n_positive`. This is cheap to accumulate and is the load-bearing signal for the user's stated goal of *testing which stopping criterion works best* — the GUI status line and CLI per-dataset log surface it. The workflow branch ignores the report (it only persists the mask); the CLI logs it; the GUI worker returns it.
- **Stopping criteria are a pluggable registry, not hardcoded.** `STOP_CRITERIA: dict[str, Callable]` of pure predicates over a per-unit round context; a run names the active criteria, their params, and a combine mode (any/all). This directly serves the user's "test which works best" goal and matches the codebase's registry idiom. The three discussed signals plus additional seeded ones (below) all ship.
- **Seeded stopping criteria** (each pure, individually toggled/parameterized):
  1. `bg-floor` — done when Otsu threshold ≤ residual background + `k·σ` (robust median + MAD σ). "Remaining signal is within noise."
  2. `separability` — done when Otsu's between-class separability `η` < cutoff. "No real bimodal split left."
  3. `positive-fraction-high` — done when the round's foreground fraction of the **remaining residual** ≥ cutoff (default ~0.5). *The user's idea:* on pure noise Otsu marks ~half, so a high positive fraction is the tell that it is thresholding nothing.
  4. `min-positive` — done when the round's foreground is below a floor (absolute px and/or fraction). Negligible captures stop peeling.
  5. `diminishing-returns` — done when the round's *new* foreground is < a fraction of the cumulative captured area (the peel is exhausted).
  6. `peak-prominence` — done when `max(residual) − bg` within the unit < `k·σ` (brightest remaining pixel is no longer prominent above background).
  7. `min-area-components` — done when no connected component in the round's foreground meets a min-area (every capture is sub-particle speckle).
- **`max_rounds` and degenerate-unit guards are always-on backstops, not optional criteria.** A unit with an empty/constant/all-NaN residual latches done immediately; the global loop cannot exceed `max_rounds`. Guarantees termination regardless of stopping config (AE3).
- **Scope selects the iteration unit.** `groups` reuses the existing `GroupingResult` (so `--algorithm`/gmm/kmeans still define the grouping); `per-cell` iterates each label; `whole-field` is a single all-cells unit. The pure-domain core takes a list of unit masks and is agnostic to how they were built.
- **Extend `ThresholdingRound` with an `iterative_otsu` sentinel field**, mirroring `puncta`; add a third branch in `_apply_threshold_frame`. The CLI/GUI construct the settings and attach them to the round. Keeps the legacy path byte-identical.
- **Reuse `AcceptPunctaMask`** for GUI persistence rather than adding a near-identical use case (it is already a generic binary-mask Creator). Note it as a rename candidate (`AcceptBinaryMask`) but do not rename in this plan.
- **GUI ships standalone first** (Analysis-tab Creator panel + CLI), deferring `WorkflowConfig`/runner integration — the same incremental path the adaptive-clip module took.

---

## Open Questions

### Resolved During Planning

- *How should the program know to stop?* → A composable stopping-criterion registry (seven seeded signals incl. the user's positive-fraction idea), combined by any/all, plus an always-on `max_rounds` cap and degenerate-residual guard. (R2.)
- *Per-cell or per-group?* → Configurable `scope` (groups / per-cell / whole-field). (R6.)
- *Surface?* → Both: a `percell4-batch-threshold` flag and an Analysis-tab Creator panel. (R4, R5.)
- *Output shape?* → Binary union `/masks/<round>`. (R3.)
- *How to "subtract from the image"?* → NaN-stamp accepted+dilated pixels in a float32 working buffer.
- *New `THRESHOLD_METHODS` entry or round flavor?* → Round flavor (`iterative_otsu` sentinel field + `_apply_threshold_frame` branch), like puncta.

### Deferred to Implementation

- **Default stopping config** (which criteria are on by default, and their default params/combine mode). Pick a sensible recall-first default during U1 once the criteria can be exercised on the synthetic fixtures; the user will tune via the exposed knobs.
- **Exact CLI flag spelling** for per-criterion params (e.g. `--stop-criteria bg-floor,positive-fraction-high --bg-floor-k 2.5`). Finalize in U4 against the existing arg-group style.
- **Default `max_rounds`** value — choose from fixture behavior in U1 (peeling on real-ish data rarely needs many rounds; a low cap like 8–12 is expected to be ample).
- **Whether `min-area-components` reuses `analyze_particles` or a thinner `scipy.ndimage.label`** — decide in U1 based on import weight in the pure-domain layer.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

**The peel loop (pure domain, one call per frame).** Input: an already-smoothed float32 image (smoothed **once**, before the loop), a list of unit masks (bool), and `IterativeOtsuSettings`. Output: one `{0,1}` uint8 combined mask + an `IterativeOtsuReport`.

```
working   = smoothed.astype(float32).copy()      # smoothed ONCE upstream; NaN-stamped as we peel
combined  = zeros(uint8)
done      = {unit_index: False}                  # latches; never un-latches
for iteration in range(1, max_rounds + 1):       # 'iteration', not 'round' (see Terminology)
    if all(done.values()): break
    for u, unit_mask in enumerate(units):
        if done[u]: continue
        residual = working[unit_mask]
        residual = residual[isfinite(residual)]
        if residual.size == 0 or residual.min == residual.max:   # degenerate → DONE (not accept-all)
            done[u] = True; continue
        thr, eta = otsu_with_separability(residual)
        candidate = unit_mask & (working >= thr) & isfinite(working)
        ctx = RoundContext(residual, candidate, combined & unit_mask, unit_mask,
                           thr, eta, bg, sigma, iteration)         # expensive fields computed lazily
        if should_stop(ctx, settings.stop_criteria, settings.stop_combine):  # registry vote
            done[u] = True; continue                              # do NOT accept this iteration
        np.maximum(combined, candidate.astype(uint8), out=combined)       # accept
        ring = dilation(candidate, footprint=disk(dilation_radius_px)) & unit_mask  # re-clamp!
        working[ring] = nan                                       # remove for next iteration
np.minimum(combined, 1, out=combined)            # guarantee {0,1}
return combined, report          # report: n_iterations_run, units_total, units_hit_max_rounds, fires, n_positive
```

**Stopping-criterion registry (the configurable axis).**

```
STOP_CRITERIA: dict[str, Callable[[RoundContext, dict], bool]] = {
    "bg-floor":              lambda c, p: c.thr <= c.bg + p["k"] * c.sigma,
    "separability":          lambda c, p: c.eta < p["min_eta"],
    "positive-fraction-high":lambda c, p: c.candidate_frac_of_residual >= p["max_frac"],
    "min-positive":          lambda c, p: c.candidate_px < p["min_px"],
    "diminishing-returns":   lambda c, p: c.new_px < p["min_gain_frac"] * c.cumulative_px,
    "peak-prominence":       lambda c, p: (c.residual_max - c.bg) < p["k"] * c.sigma,
    "min-area-components":   lambda c, p: c.max_component_area < p["min_area_px"],
}
# keys MUST equal STOP_CRITERION_NAMES (asserted at import — drift guard)

def should_stop(ctx, active_names, combine):       # combine ∈ {"any","all"}
    votes = [STOP_CRITERIA[n](ctx, params_for(n)) for n in active_names]
    return any(votes) if combine == "any" else all(votes)

# params_for("bg-floor") selects the dotted-namespaced keys for that criterion and
# strips the prefix:  {"bg-floor.k": 2.5, "peak-prominence.k": 3.0}  ->  {"k": 2.5}
# This is what keeps two criteria that both use "k" from colliding in the flat bag.
def params_for(name):
    return {k[len(name) + 1:]: v for k, v in stop_params if k.startswith(name + ".")}
```

**Layer flow.**

```
CLI flag ─┐                                    Analysis-tab panel ─┐
          ├─► ThresholdingRound(iterative_otsu=IterativeOtsuSettings(...))
          │            │                                            │ Worker(run_iterative_otsu)
          │   _apply_threshold_frame  ──3rd branch──►  _apply_iterative_otsu_groups
          │            │  (build unit masks from scope: groups|per-cell|whole-field)
          │            ▼
          │   domain/measure/iterative_otsu.peel(...)  ◄── STOP_CRITERIA registry
          │            ▼                                            │
          └─► /masks/<round> + /groups/<round>          AcceptPunctaMask → add_mask → select
```

---

## Implementation Units

- U1. **Pure-domain iterative-Otsu core + stopping-criterion registry**

**Goal:** The complete algorithm with no Qt/store/grouping dependencies: the peel loop, Otsu-with-separability, the NaN-stamp removal, the dilation re-clamp, and the pluggable stopping-criterion registry.

**Requirements:** R1, R2, R3, R6, R7

**Dependencies:** None

**Files:**
- Create: `src/percell4/domain/measure/iterative_otsu.py` (peel loop, `RoundContext`, `STOP_CRITERIA`, `otsu_with_separability`)
- Create: `src/percell4/domain/measure/iterative_otsu_names.py` (`STOP_CRITERION_NAMES`, `SCOPE_NAMES` — skimage-free tuples)
- Test: `tests/test_measure/test_iterative_otsu.py`

**Approach:**
- `peel(smoothed, units, settings) -> tuple[NDArray[uint8], IterativeOtsuReport]`: the loop sketched above. `smoothed` is pre-smoothed **once** by the caller; `units` is a list of bool masks; the core never sees labels/groups directly. Define a small frozen `IterativeOtsuReport` (`n_iterations_run`, `units_total`, `units_hit_max_rounds`, `criterion_fires: dict[str,int]`, `n_positive`).
- `otsu_with_separability(residual) -> (thr, eta)`: skimage `threshold_otsu` on finite pixels, plus `η = between-class variance / total variance` from the class means/weights at `thr`. Guard constant/empty → signal "degenerate."
- Removal: `working[dilation(candidate, disk(r)) & unit_mask] = nan`. **Re-clamp to `unit_mask` every iteration** (AE4).
- Degenerate residual (empty / constant / all-NaN) latches the unit done — the opposite of the single-shot "accept all" (learnings).
- `RoundContext` computes expensive fields **lazily / conditionally**: only compute `max_component_area` (connected components) when `min-area-components` is active, only compute `bg`/`sigma` when a criterion that needs them is active. Avoids paying for off criteria (scope-guardian advisory).
- Registry: flat `dict[str, Callable]`; assert `set(STOP_CRITERIA) == set(STOP_CRITERION_NAMES)` at import. `should_stop(ctx, names, combine)`; `params_for(name)` strips the dotted prefix (see High-Level Technical Design).
- Pure: numpy/scipy/skimage only; no workflows/store/qt imports at runtime (duck-type settings like `adaptive_clip.py` does).

**Patterns to follow:** `domain/measure/adaptive_clip.py` (`otsu_first_pass` degenerate guard, pure-domain duck-typed settings); `puncta_detectors.py` / `bg_estimators.py` registry + names drift guard; dilute-phase `dilation(disk(r))` + NaN-stamp.

**Test scenarios:**
- Covers AE1. Happy path: a 100×100 cell with a bright square + a dimmer square; single Otsu captures only the bright one, but `peel(...)` captures both. Assert both squares' centers are positive in the union.
- Covers AE2. Pure-noise unit: residual is Gaussian noise only → method latches done within 1 round; union has 0 (or < min-positive) positive px, **not** ~50%.
- Covers AE3. Termination: a stop config of `["min-positive"]` with `min-positive.min_px=0` (never fires) still returns within `max_rounds` iterations; assert `report.n_iterations_run <= max_rounds`.
- Report contract: on the AE1 fixture, `report.n_iterations_run`, `units_total`, `units_hit_max_rounds`, `criterion_fires`, and `n_positive` are populated and internally consistent (e.g. `n_positive == int(mask.sum())`).
- Covers AE4. Edge bleed: two adjacent cells, a focus touching the shared border in cell A; assert no pixel of cell B is ever positive or NaN-stamped.
- Edge case: constant unit (all one value) → done immediately, empty contribution, no raise.
- Edge case: empty unit mask (no pixels) → skipped, no raise.
- Each stop criterion in isolation: construct a `RoundContext` that should/shouldn't trip each of the seven predicates; assert the boolean. `combine="any"` vs `"all"` over two criteria.
- Output contract: `dtype == uint8`, `set(np.unique(out)) <= {0,1}`.
- Registry drift guard: `set(STOP_CRITERIA) == set(STOP_CRITERION_NAMES)`.

**Verification:** `peel` recovers dim foci that single Otsu misses, always terminates, never marks pure noise as foreground, never crosses unit boundaries, and returns a binary `{0,1}` uint8 mask.

---

- U2. **`IterativeOtsuSettings` dataclass + `ThresholdingRound` field**

**Goal:** A frozen, validated, run-config-round-trippable settings object, attached to `ThresholdingRound` as a sentinel field.

**Requirements:** R2, R6, R7

**Dependencies:** U1 (imports `STOP_CRITERION_NAMES`, `SCOPE_NAMES`)

**Files:**
- Modify: `src/percell4/workflows/models.py` (add `IterativeOtsuSettings`; add `iterative_otsu: IterativeOtsuSettings | None = None` to `ThresholdingRound`; cross-validate not-both-with-`puncta`)
- Test: `tests/test_workflows/test_models.py` (or the existing models test module)

**Approach:**
- `IterativeOtsuSettings(frozen=True)`: `scope: str`, `dilation_radius_px: int = 5`, `max_rounds: int = 10`, `stop_criteria: tuple[str, ...]`, `stop_params: tuple[tuple[str, Any], ...]` (normalized via `_normalize_params`), `stop_combine: str = "any"`.
- **`stop_params` keys are dotted-namespaced by criterion** (`"bg-floor.k"`, `"positive-fraction-high.max_frac"`, …) — this is the contract `params_for(name)` (U1) relies on, and it is what prevents two criteria that share a bare param name (e.g. `k`) from colliding in the single flat bag. Validate in `__post_init__` that every `stop_params` key's prefix-before-the-dot is a known `STOP_CRITERION_NAMES` entry and (optionally) that it appears in `stop_criteria`.
- `__post_init__`: `scope in SCOPE_NAMES`; `dilation_radius_px > 0`; `max_rounds >= 1`; every name in `stop_criteria` ∈ `STOP_CRITERION_NAMES` and non-empty; `stop_combine in {"any","all"}`; normalize `stop_params` and validate its dotted-key prefixes.
- `ThresholdingRound.__post_init__`: reject `puncta is not None and iterative_otsu is not None` (mutually exclusive).
- Validate against the skimage-free names tuples so building a round stays light (no scikit-image import).

**Patterns to follow:** `PunctaDetectorSettings` (frozen + `_normalize_params` + `__post_init__`); `puncta_names.py` validation source.

**Test scenarios:**
- Happy path: a valid `IterativeOtsuSettings` for each scope constructs and round-trips through `config_to_dict`/`config_from_dict` (`workflows/artifacts.py`) byte-stably.
- Error path: unknown scope, `dilation_radius_px <= 0`, `max_rounds < 1`, unknown stop-criterion name, bad `stop_combine`, non-JSON-scalar stop param, a `stop_params` key whose dotted prefix is not a known criterion → `ValueError`.
- Namespacing: `bg-floor.k=2.5` and `peak-prominence.k=3.0` coexist; `params_for("bg-floor")` yields `{"k": 2.5}` and `params_for("peak-prominence")` yields `{"k": 3.0}` (no collision).
- Error path: a `ThresholdingRound` carrying both `puncta` and `iterative_otsu` → `ValueError`.
- Hashability: two equal settings hash equal (frozen + normalized params).

**Verification:** A stale/hand-edited `run_config.json` with a bad iterative-Otsu config fails loudly at load; a valid one round-trips.

---

- U3. **Workflow dispatch branch + scope→unit-mask construction**

**Goal:** Wire the pure-domain core into the headless apply phase as a third branch, building unit masks from the configured scope.

**Requirements:** R1, R3, R6, R7

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/workflows/phases.py` (add `_apply_iterative_otsu_groups(...)`; add the third branch to `_apply_threshold_frame`)
- Test: `tests/test_workflows/test_phases.py` (add an iterative-Otsu round-flavor test, mirroring the puncta-mode test)

**Approach:**
- New branch precedence in `_apply_threshold_frame`: `iterative_otsu is not None` → `_apply_iterative_otsu_groups`; else existing `use_puncta`; else legacy per-group Otsu.
- `_apply_iterative_otsu_groups(smoothed, labels, grouping, settings, combined, round_name) -> str`: build `units` by scope —
  - `groups`: one bool mask per `group_id` (reuse the `np.isin(labels, cells_in_group)` idiom).
  - `per-cell`: one mask per non-zero label in `np.unique(labels)`.
  - `whole-field`: a single `labels > 0` mask.
  - Call `iterative_otsu.peel(smoothed, units, settings)` (caller does the one-time smoothing), `np.maximum` the returned mask into `combined`, finish `np.minimum(combined, 1, out=combined)`. Return `""` or an error string. The `peel` report is logged, not persisted.
- **`group_df` must reflect what actually drove the mask.** In `scope == "groups"`, emit the existing GMM/k-means `group_df` (unchanged). In `scope == "per-cell"` or `"whole-field"`, the grouping did **not** drive the mask, so emit a **degenerate single-group** table (every label → group `1`) with the same `["label", "group_<channel>_<metric>"]` columns. This keeps `/groups/<round>` honest and prevents a misleading `summary_groups`/measurement column that buckets cells by a clustering the mask ignored. (Resolves the cross-section contradiction the reviews flagged: grouping is still *computed* upstream by `threshold_compute_one`, but in non-`groups` scope its assignments are not written.)
- Below `MIN_CELLS_DEFAULT` (10) cells, `groups` scope collapses to a single group anyway (grouper fallback), so it is observationally identical to `whole-field`. Size any `groups`-scope test fixture to ≥10 cells (the existing 12-cell fixture clears this); document the collapse so small-dataset runs are not surprising.
- Time-lapse: the existing `(T,H,W)` stacking in `apply_threshold_headless` is untouched (the branch runs per frame).

**Patterns to follow:** `_apply_puncta_groups` (signature shape, error-string return, `np.maximum`/`np.minimum` discipline); the per-group loop's `np.isin` unit construction.

**Test scenarios:**
- Covers AE1/AE5. For each scope: build a `ThresholdingRound(iterative_otsu=...)`, run `threshold_compute_one` → `apply_threshold_headless` against the 12-cell fixture, then `store.read_mask(name)` asserts `dtype==uint8`, `unique <= {0,1}`; `store.read_dataframe("/groups/<name>")` still has the group column.
- Edge case: per-cell scope on a dataset where some cells are pure background → those cells contribute empty mask, others contribute foreground; mask is still binary.
- Group table honesty: in `whole-field`/`per-cell` scope, `/groups/<round>` maps every label to group `1` (degenerate single group); in `groups` scope (≥10-cell fixture) it carries the real GMM/k-means assignments.
- Integration: the produced mask is consumable by `analyze_particles` (run it, assert no raise and sane component count).
- Error path: a deliberately broken settings (e.g., forced exception in the core) surfaces as the `(None, None, msg)` failure tuple, not a crash.

**Verification:** All three scopes write a binary mask + group table via the unchanged store contract; the legacy and puncta paths remain byte-identical.

---

- U4. **CLI flag on `percell4-batch-threshold`**

**Goal:** A headless entry point that selects iterative-Otsu and exposes its knobs.

**Requirements:** R4

**Dependencies:** U2, U3

**Files:**
- Modify: `src/percell4/interfaces/cli/batch_threshold.py` (add `--strategy {grouped-otsu,iterative-otsu}` + iterative flags; construct `IterativeOtsuSettings`; attach to the round)
- Modify: module docstring (usage examples, new exit-code-neutral flags)
- Test: `tests/test_cli_batch_threshold.py`

**Approach:**
- Add `--strategy` (default `grouped-otsu`, keeping current behavior). When `iterative-otsu`: parse `--iterative-scope`, `--dilation-radius` (default 5), `--max-rounds`, `--stop-criteria` (comma-separated names), `--stop-combine {any,all}`, and per-criterion params (e.g. `--bg-floor-k`, `--positive-fraction-max`, `--min-positive-px`, ...). Build `IterativeOtsuSettings`, set `ThresholdingRound(..., iterative_otsu=settings)`.
- When `scope == "groups"`, `--algorithm`/gmm/kmeans still drive grouping; in `per-cell`/`whole-field` they are accepted but inert for masking (and the group table is degenerate — see U3), documented in `--help`.
- Surface the peel report on the per-dataset `[ok]` line (e.g. `... wrote /masks/r1 (peel: 3 iters, 2/12 units hit cap, 41,203 px)`) so a researcher tuning stop criteria sees convergence behavior without re-opening the file.
- Catch `ValueError` from settings/round construction → exit 1 "invalid round configuration" (existing pattern).
- Preserve: deferred heavy imports (Qt-free `--help`), `resolve_paths`, overwrite guard, exit `0 if n_ok>0 else 1`, `Next: percell4-batch-measure ...` hint.

**Patterns to follow:** the existing `grp` arg-group + `ThresholdingRound` construction (lines 60–129); `test_batch_threshold_import_is_qt_free`.

**Test scenarios:**
- Covers AE5. `main(["ds.h5","--round-name","r1","--channel","GFP","--strategy","iterative-otsu","--iterative-scope","per-cell", ...])` → return 0, `store.list_masks()` contains `r1`, mask `dtype==uint8`/`{0,1}`, hand-off line printed.
- Default `--strategy grouped-otsu` is byte-identical to today (regression).
- Error path: unknown `--stop-criteria` name or `--dilation-radius 0` → exit 1 with a clear message; no mask written.
- Qt-free import guard still passes.

**Verification:** `percell4-batch-threshold --strategy iterative-otsu ...` writes a binary mask measurable by `percell4-batch-measure`; bad configs exit 1 cleanly.

---

- U5. **Analysis-tab Creator panel + settings form + worker body**

**Goal:** An interactive panel that runs iterative-Otsu on the active channel + segmentation and creates an auto-selected mask, all heavy work off the UI thread.

**Requirements:** R5, R7

**Dependencies:** U1, U2, U3

**Files:**
- Create: `src/percell4/gui/_iterative_otsu_settings.py` (`IterativeOtsuConfig` frozen + `IterativeOtsuSettingsWidget`)
- Create: `src/percell4/gui/iterative_otsu_panel.py` (`IterativeOtsuPanel` Creator + pure `run_iterative_otsu(image, labels, gaussian_sigma, settings, scope)` worker body)
- Test: `tests/test_gui/test_iterative_otsu_panel.py` (test the pure worker body + `current_config()` snapshot; qtbot smoke if the suite supports it)

**Approach:**

*Settings form (avoid the 18-widget wall).* Mirror `AdaptiveClipSettingsWidget` for the global knobs (scope combo, dilation-radius spin, max-rounds spin, Gaussian σ), then put the stopping config inside a dedicated `QGroupBox("Stopping criteria")` containing one row per criterion (checkbox + its param spin(s)) and the `stop_combine` combo at the top of that group labeled "Stop a cell when [any|all] checked criteria fire". Ship a **default visual state** so the form reads simply: `bg-floor` and `positive-fraction-high` checked, combine = `any`, the other five unchecked. `config_changed` aggregated signal; frozen `current_config()` snapshots straight into the dotted-namespaced `stop_params`. Per-criterion widget spec (so the implementer does not guess):

| Criterion | Param(s) | Widget | Range / step | Default | Checked by default |
|---|---|---|---|---|---|
| `bg-floor` | `k` | QDoubleSpinBox | 0.0–10.0 / 0.25 | 2.0 | ✅ |
| `positive-fraction-high` | `max_frac` | QDoubleSpinBox | 0.0–1.0 / 0.05 | 0.5 | ✅ |
| `separability` | `min_eta` | QDoubleSpinBox | 0.0–1.0 / 0.05 | 0.5 | ☐ |
| `min-positive` | `min_px` | QSpinBox | 0–100000 / 1 | 5 | ☐ |
| `diminishing-returns` | `min_gain_frac` | QDoubleSpinBox | 0.0–1.0 / 0.01 | 0.02 | ☐ |
| `peak-prominence` | `k` | QDoubleSpinBox | 0.0–10.0 / 0.25 | 3.0 | ☐ |
| `min-area-components` | `min_area_px` | QSpinBox | 0–100000 / 1 | 3 | ☐ |

*(Defaults are a starting point for the user's experimentation, not a tuned recommendation; final defaults track U1's fixture work.)*

*Pure worker body.* `run_iterative_otsu(image, labels, gaussian_sigma, settings, scope) -> (mask uint8, IterativeOtsuReport)` — no Qt: smooth once → build unit masks from `labels` per scope → `iterative_otsu.peel(...)` → return `(mask, report)`. Defer heavy imports inside the function (worker-safe, testable). Returning the report mirrors the adaptive panel's `(mask, window_used)` shape and feeds the status line.

*Panel (Creator).* `IterativeOtsuPanel(data_model, get_repo, get_store, get_viewer_window, show_status)`. Reads `session.active_channel` **and** `session.active_segmentation` (both Selector-owned — panel only reads); never writes any session field except `active_mask` (via `AcceptPunctaMask`). Run-button guard ladder, each with specific status copy:
  1. no viewer/store/dataset → "Open a dataset in the viewer first".
  2. no `active_channel` → "Select a channel in the Session window first".
  3. dataset has **no** segmentation at all (`store.list_labels()` empty) → "No segmentation found — run Cellpose (Segment tab) first".
  4. segmentation(s) exist but `active_segmentation` is unset → "Select a segmentation in the Session window first".
  Then: pull channel image + the active-segmentation labels; time-lapse → current displayed frame (like the adaptive panel; **single-frame by design**, see Scope Boundaries); prompt for a mask name via `prompt_for_resource_name`; disable the Run button + lock the settings; show a running message that sets expectations: `f"Running iterative Otsu — up to {max_rounds} iterations/cell…"` (append `" (frame {t})"` for time-lapse). Run a `gui.workers.Worker`; on `error` re-enable + show `f"Error: {err.exc_type}: {err.message}"`.
  On `finished`: `AcceptPunctaMask(repo, session).execute(mask, name)` then `viewer_win.add_mask(...)`. Success status reports the report, and **distinguishes the empty result** (a meaningful scientific outcome, not a failure): if `report.n_positive == 0` → `f"Saved '{name}': no foreground detected ({report.n_iterations_run} iters)"`; else → `f"Saved '{name}': {report.n_positive:,} px, {report.n_iterations_run} iters, {report.units_hit_max_rounds}/{report.units_total} units hit the {max_rounds}-cap"`.
- **No Cancel button in v1** (accepted limitation, matching the adaptive-clip precedent). Because per-cell runtime scales with `max_rounds × n_cells`, the expectation-setting running message above is the mitigation; a cooperative-cancel pathway is a deferred follow-up if runs prove long in practice.

**Patterns to follow:** `docs/adaptive-local-clipping-core-implementation.md` (all four files); `gui/adaptive_clip_panel.py` worker/Creator flow; `AcceptPunctaMask` reuse; `gui/_grouped_threshold_settings.py` for a settings form that also needs a segmentation.

**Test scenarios:**
- Happy path (pure worker): `run_iterative_otsu` on a synthetic image+labels with bright/dim foci returns `(mask, report)` where the binary mask contains both foci and `report.n_positive == int(mask.sum())` (mirrors U1 AE1 at the worker boundary).
- Config snapshot: the default form state snapshots to `IterativeOtsuConfig` with `bg-floor` + `positive-fraction-high` active and dotted `stop_params` keys (`"bg-floor.k"`, `"positive-fraction-high.max_frac"`); toggling a third criterion + editing its spin adds the right dotted key; scope/dilation/max-rounds captured.
- Empty-result: a pure-noise image yields `report.n_positive == 0`; the panel's success-status branch produces the "no foreground detected" copy (assert the string the panel would set, given the report).
- Error path (pure worker): degenerate input (empty labels) returns an empty mask + zeroed report, no raise.
- Creator contract (if qtbot available): a Run with a stub repo/session writes the mask, refreshes lists, and sets `active_mask` to the new name (assert via `AcceptPunctaMask` result + session state). Otherwise assert at the use-case level (already covered by `AcceptPunctaMask` tests) and keep the panel test to the pure worker.

**Verification:** Clicking Run detects on the active channel/segmentation, writes a binary mask, and auto-selects it without freezing the UI.

---

- U6. **Register the panel in the Analysis tab**

**Goal:** Surface the panel under Analysis, beside Grouped Thresholding and Adaptive Local Clipping.

**Requirements:** R5

**Dependencies:** U5

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py` (construct `IterativeOtsuPanel` with the standard callables, wrap in a `QGroupBox("Iterative Otsu Thresholding")`)

**Approach:** Mirror the `AdaptiveClipPanel` construction block (lines ~193–206): same `get_repo/get_store/get_viewer_window/show_status` wiring, deferred import, `QGroupBox` + `QVBoxLayout` + `layout.addWidget`.

**Patterns to follow:** the existing Adaptive Local Clipping registration block.

**Test scenarios:**
- Test expectation: none beyond a construction smoke test — pure wiring. If the suite has an `analysis_panel` construction test, assert the panel attribute exists after build; otherwise rely on U5's panel tests. (No behavioral logic added here.)

**Verification:** The Analysis tab shows an "Iterative Otsu Thresholding" group whose Run button completes the U5 flow end-to-end on a loaded dataset.

---

- U7. **Docs: registry contract + module notes**

**Goal:** Keep the living docs accurate (current-state only) so the new registry and round flavor are discoverable.

**Requirements:** R2, R7

**Dependencies:** U1, U2, U3, U5

**Files:**
- Modify: `src/percell4/domain/measure/CLAUDE.md` (document the `iterative_otsu` module, `STOP_CRITERIA` registry contract + signature, and the names drift guard, alongside the existing puncta two-axis section)
- Modify: `src/percell4/workflows/CLAUDE.md` (note the `iterative_otsu` sentinel field on `ThresholdingRound`)
- Consider: a `docs/solutions/` entry (via `/ce-compound`) capturing the iterative/convergent thresholding pattern once it lands (no existing learning covers it).

**Approach:** Current-state prose only — no plan/history language (per the project's documentation rules). Mirror the tone and structure of the existing `domain/measure/CLAUDE.md` registry sections.

**Test scenarios:** Test expectation: none — documentation only.

**Verification:** A future contributor reading `domain/measure/CLAUDE.md` can find the stopping-criterion registry signature and the scope semantics without reading the implementation.

---

## System-Wide Impact

- **Interaction graph:** New code branches `_apply_threshold_frame` (one added `if`), adds a CLI flag, and adds one Analysis-tab panel. The legacy per-group and puncta paths are untouched. The GUI panel reuses `AcceptPunctaMask` (no new Creator code path).
- **Error propagation:** Domain core raises only on programmer error; expected degeneracies (empty/constant/noise residual) return cleanly (done-latch). The workflow branch converts any core exception to the existing `(None, None, msg)` failure tuple; the CLI maps construction errors to exit 1; the GUI surfaces worker errors to the status label.
- **State lifecycle risks:** `(mask, groups)` remain a coupled write set under `--overwrite`; the GUI Creator follows the four-step contract via `AcceptPunctaMask`. No new HDF5 paths or dtypes.
- **API surface parity:** Both surfaces construct the same `IterativeOtsuSettings` and delegate to the one `peel` core — a single source of truth for *per-frame* behavior. The one intentional divergence: on time-lapse data the CLI writes a full `(T,H,W)` stack while the GUI runs on the currently-displayed frame and writes `(H,W)` (matching the adaptive-clip precedent). This is documented, not a bug to reconcile.
- **Integration coverage:** The U3 phase test and U4 CLI test prove the store round-trip and downstream `analyze_particles`/`measure` consumption that unit tests of `peel` alone cannot.
- **Unchanged invariants:** `THRESHOLD_METHODS` is not modified; `store.py` is not modified; `/masks` stays `{0,1}` uint8; the puncta and legacy round paths are byte-identical; segmentation is never written.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Stopping criterion misfires and the loop never terminates (the user's central concern). | Always-on `max_rounds` cap + degenerate-residual done-latch independent of the criteria; explicit termination test (AE3). |
| 5 px dilation bleeds into neighboring cells, stamping/detecting their pixels. | Re-clamp the dilated ring to `unit_mask` every round; explicit edge-bleed test (AE4). |
| Accumulating non-binary values across rounds → blank napari mask. | End every union with `np.minimum(·,1)` / `(>0).astype(uint8)`; assert `unique<=({0,1})` at each layer's test. |
| Single-shot "constant unit → accept all" guard, copied naively, spins forever in the loop. | In the iterative core a degenerate residual means **done**, not accept-all; called out in U1 and tested. |
| Per-cell scope on many labels is slower than per-group (one Otsu per cell per round). | Acceptable for a headless/standalone method; `whole-field`/`groups` scopes available; finite-filtering keeps each Otsu cheap; `max_rounds` bounds total work. |
| Configurable stopping registry sprawls into too many half-tested knobs. | Each criterion is a tiny pure predicate with an isolated unit test; a sensible default config is chosen in U1 so most runs need no tuning. |
| GUI panel before `WorkflowConfig` integration could feel orphaned. | Matches the adaptive-clip staging precedent; batch-workflow integration is an explicit deferred follow-up. |
| Smooth-once (vs the dilute twin's per-iteration re-smooth) could leave a stamped focus's smoothed skirt biasing the next Otsu. | Deliberate, documented v1 decision (Key Technical Decisions); finite-filtering carries NaN-safety; dilation guard-ring removes the skirt's core; per-iteration re-smooth is a deferred toggle if tuning shows bias. |
| Long per-cell GUI runs lock the UI with no Cancel. | Accepted v1 limitation (adaptive-clip parity); expectation-setting running message ("up to N iterations/cell"); cooperative-cancel deferred. |
| `groups` scope silently equals `whole-field` below 10 cells (grouper fallback). | Documented in U3; `groups`-scope tests sized ≥10 cells. |

---

## Documentation / Operational Notes

- Update `src/percell4/domain/measure/CLAUDE.md` and `src/percell4/workflows/CLAUDE.md` (U7), current-state only.
- The `--strategy`/iterative flags are additive and default to existing behavior; no migration of existing `/masks` or run configs is required.
- Archive this plan and capture an iterative/convergent-thresholding learning via `/ce-compound` after merge (no existing `docs/solutions/` entry covers it).

---

## Sources & References

- Related prior art: `docs/brainstorms/2026-06-03-headless-grouped-thresholding-puncta-requirements.md` and `docs/plans/2026-06-03-002-feat-headless-puncta-thresholding-plan.md` (same recall problem, heavier pluggable-registry approach — iterative-Otsu is the focused sibling).
- GUI Creator template: `docs/adaptive-local-clipping-core-implementation.md` (untracked, this branch).
- Reuse targets: `src/percell4/workflows/phases.py` (`_apply_threshold_frame`, `_apply_puncta_groups`), `src/percell4/gui/workflows/dilute_phase/controller.py` (dilate + NaN-stamp peel), `src/percell4/workflows/models.py` (`PunctaDetectorSettings`/`ThresholdingRound`), `src/percell4/application/use_cases/accept_puncta_mask.py`, `src/percell4/interfaces/cli/batch_threshold.py`, `src/percell4/interfaces/gui/task_panels/analysis_panel.py`.
- Learnings: `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md`, `docs/solutions/logic-errors/batch-compress-development-lessons.md`, `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`, `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md`, `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`.
