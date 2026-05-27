---
title: Phasor-Masks Workflow — Shared ROI Across Datasets
type: feat
status: completed
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-phasor-masks-shared-roi-requirements.md
---

# Phasor-Masks Workflow — Shared ROI Across Datasets

## Overview

Extend the Automated Phasor-Masks Workflow (shipped at commit `39190037`) with a per-dataset "ROI source" assignment so treatment groups can share a single fitted GMM ellipse across datasets. Each dataset row in the dialog gets a small dropdown defaulting to "fit own GMM" (current behavior) plus the other datasets in the queue that are themselves self-fitting. When a dataset points at a source, the workflow looks up the source's fitted ellipse geometry and applies it to the target's own phasor maps — only the geometry is shared, the per-dataset phasor distribution still determines the mask. The CLI gains a repeated `--roi-source TARGET=SOURCE` flag.

This is a deltas-to-existing-units plan, not greenfield. All work modifies files that already shipped on `main`.

---

## Problem Frame

Cross-condition phasor comparisons (e.g., Untreated vs As-treated) need a consistent lifetime gate across datasets, otherwise re-fitting per dataset erases the very treatment-effect signal the researcher is measuring. The current workflow fits a fresh ellipse per dataset, so the gate moves with the data. Researchers want to fit on one gold-standard dataset (typically an untreated baseline) and apply that ellipse to every other dataset in the cohort. See origin: `docs/brainstorms/2026-05-27-phasor-masks-shared-roi-requirements.md`.

---

## Requirements Trace

- R1. Per-row "ROI source" `QComboBox` on each dataset in the dialog. *(origin R1)*
- R2. Dropdown shows `fit own GMM` + every other queue dataset that is itself self-fitting; chains and cycles impossible by construction. *(origin R2)*
- R3. Changing any assignment refreshes every other row's dropdown and falls dependents back to `fit own GMM` with an inline message when their source becomes invalid. *(origin R3, R4)*
- R4. ROI assignment scope is per-dataset, applies uniformly to all selected channels. *(origin R5)*
- R5. In-memory ROI cache keyed by `(source_path_resolved, channel)`, populated when a self-fitting dataset's fit succeeds, looked up by targets, discarded at end of run. *(origin R6)*
- R6. Execution order: **interleaved per-source-group**. Iterate `paths` in user list order; when a self-fitting dataset is encountered, fit it AND immediately process any subsequent-in-list-order targets that point at it (and the source's own masks), before moving on to the next self-fitting dataset. Each target's source is fitted strictly before the target. This is a refinement of origin R7's intent ("sources before targets") — sources are always fitted before their dependents, while keeping cancel-safe semantics (cancellation produces complete per-group results, not half-cohorts where sources have masks but their targets don't). *(origin R7, refined)*
- R7. Source-fit failure for channel `<ch>` routes to every dependent target's `errors[ch]` with a message naming the source path and the original reason. *(origin R8)*
- R8. Self-fitting datasets continue to write their own masks (source is target-of-self). *(origin R9)*
- R9. CLI accepts repeated `--roi-source TARGET=SOURCE` flag; unmentioned datasets default to self-fitting. *(origin R10)*
- R10. CLI validation rejects (exit 2): TARGET not in `paths`; SOURCE not in `paths`; SOURCE that appears as a TARGET in some other `--roi-source` (no chains). *(origin R11)*
- R11. End-of-run summary labels each dataset with its ROI provenance (`[source: self]` or `[source: <path>]`) in both dialog `QMessageBox` and CLI stdout. *(origin R12)*

**Origin actors:** A1 (Researcher).
**Origin flows:** F1 (Configure mixed batch), F2 (Run mixed batch), F3 (Headless CLI re-run).

---

## Scope Boundaries

- **Chains** (target points at another target). Source must be self-fitting; trees of depth 1 only.
- **Per-channel source overrides** within a single dataset. One source per dataset, applies to all selected channels.
- **Cross-run ROI persistence.** Cache lives only for the duration of a single run. Not saved as HDF5 attrs, no sidecar files, no "saved ROI library" UI.
- **Different-channel-name mapping** ("use source's mNG for target's Halo"). Channel name must match; the channel-intersection rule already enforces this.

### Deferred to Follow-Up Work

- **Auto-grouping by filename prefix.** A future "Auto-group" button could parse `Untreated_*` / `AsTreated_*` and propose assignments. Not in scope here; researcher assigns manually for v1. *(carried from origin)*
- **Persisting ROI provenance as HDF5 attrs on `/masks/<name>`** (`roi_center`, `roi_radii`, `roi_angle_deg`, `roi_source_path`). Useful long after the run for traceability; no current consumer reads such attrs. *(carried from origin)*

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/domain/segmentation/phasor_masks.py` — current `fit_phasor_ellipse_and_apply_masks` (lines 82–205). The function this plan decomposes.
- `src/percell4/domain/flim/phasor.py` — primitives `single_component_fit_phasor`, `gmm_eigenstructure`, `gmm_to_phasor_roi_geometry`, `phasor_roi_to_mask`. Already composed by the current helper; the two new helpers compose subsets.
- `src/percell4/application/use_cases/batch_fit_phasor_masks.py` — current per-(dataset, channel) loop (line 96). Modified to add the interleaved per-source-group execution shape.
- `src/percell4/application/use_cases/batch_compute_phasor.py` — source of `BatchPhasorItemResult` / `BatchPhasorReport` (unchanged; we route through the existing `errors` dict).
- `src/percell4/gui/phasor_masks_dialog.py` — `PhasorMasksDialog` (line 116), `_PendingDataset` (line 90), `_refresh_channel_picker` (line 446), `_update_start_enabled` (line 578). All extended in U3.
- `src/percell4/interfaces/cli/batch_phasor_masks.py` — current argparse + main shape (~line 1). Extended in U4.
- `src/percell4/gui/flim_fret_dialog.py` — secondary reference for dialog patterns (the FlimFretDialog already-merged pattern this workflow inherited).

### Institutional Learnings

- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — the new `QComboBox` per row must wire its `currentIndexChanged` signal to `_refresh_*` AND `_update_start_enabled`. Programmatic `setCurrentIndex` in tests would bypass that wiring; widget tests must use `qtbot.mouseClick` / `QTest.keyClick` on the actual combo to drive the signal.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` — `Path.resolve()` was the fix for the conditional viewer refresh; same normalization rule applies to the ROI cache key so that `Path("./untreated_a.h5")` and `Path("/abs/.../untreated_a.h5")` collapse to one cache entry.
- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md` — the mandatory `(CLI, GUI)` parity test in U4 already exists; this plan extends it to cover the source-assignment path so the two surfaces stay byte-identical when a target uses a shared ROI.

### External References

None. The work is entirely inside well-established local patterns from the same workflow shipped in this session.

---

## Key Technical Decisions

- **Decompose U1's helper into two functions, keep the combined function as a facade.** New signatures:
  - `fit_phasor_ellipse(g_map, s_map, intensity_map, *, t_fit) -> PhasorEllipseFit`
  - `apply_ellipse_masks(g_map, s_map, intensity_map, fit, *, t_mask_a, t_mask_b) -> PhasorEllipseMasks`
  - `fit_phasor_ellipse_and_apply_masks(...)` becomes a thin composition returning the existing `PhasorEllipseMasksResult` unchanged. Existing callers (the facade's self-fitting path, U3's tests via U2, all 10 current domain tests) continue to work unchanged. Rationale: cleaner separation than threading an optional `geometry` kwarg, and U2 needs the two operations to happen on different datasets — that's exactly what two functions express.

- **New domain type `PhasorEllipseFit` in `domain/segmentation/phasor_masks.py`.** `gmm_to_phasor_roi_geometry` returns a bare `tuple[tuple[float,float], tuple[float,float], float]` — NOT a dataclass. The only dataclass named `PhasorROIGeometry` in the repo lives in the application layer (`run_phasor_gmm.py`), carries 9 fields the phasor-masks workflow doesn't need, and importing it from the domain would violate the pure-domain isolation rule. So U1 introduces a tiny domain dataclass: `PhasorEllipseFit(center, radii, angle_deg, sampled_pixels)` — four fields, no application-layer dependencies. `fit_phasor_ellipse` returns this. `apply_ellipse_masks` accepts this. The combined facade composes them and returns the existing `PhasorEllipseMasksResult` shape unchanged (containing the original `geometry` triple, two masks, and the `sampled_pixels` from the fit).

- **Apply-only return is a smaller dataclass `PhasorEllipseMasks(mask_a, mask_b)`.** No `geometry` field (the caller already supplied it), no `sampled_pixels` field (the caller already has it from the fit). Avoids the "`sampled_pixels` becomes a lie when called standalone" semantic drift that comes with reusing the combined result type. The combined facade composes the apply result + the fit into the existing `PhasorEllipseMasksResult` for backward compatibility with the existing 10 tests.

- **ROI cache lives in the use case, not the domain.** Domain helpers stay pure (no module-level state, no caching). The use case (U2) owns a `dict[tuple[Path, str], PhasorEllipseFit]` keyed by `(source_path.resolve(), channel)`, populated when a source is fitted, looked up when a dependent is processed, garbage-collected when the function returns.

- **Interleaved per-source-group execution, single function call from the dialog/CLI.** U2's public signature still takes a flat `h5_paths` list; the new `roi_sources: Mapping[Path, Path | None]` kwarg declares each dataset's assignment. The use case iterates `paths` in user list order: when a self-fitting dataset is reached, fit it (caching geometry per channel), then immediately process every subsequent target in `paths` whose `roi_sources[t] == p` (in their list order), applying the cached geometry; then move on. A target whose source has not been reached yet is impossible by construction (the dialog enforces that sources are themselves self-fitting; the CLI's validation rejects chains). Rationale: this preserves R6's "sources before their dependents" intent at the per-group level while making cancellation safe — cancel between groups produces complete per-group results, not a half-cohort where some sources have masks but their dependents don't.

- **Path normalization consistency across surfaces.** The dialog already calls `.resolve()` at add-time (`_PendingDataset.h5_path` is always absolute). The CLI's existing `resolve_paths` helper in `_batch_report.py` does NOT call `.resolve()` — it returns paths verbatim from argv (potentially relative). To keep the parity contract honest, U4 normalizes the CLI's positional `paths` via `.resolve()` BEFORE building the `roi_sources` dict. U2's input validation also defensively resolves keys/values via `.resolve()` so any caller (CLI, dialog, future script) gets correct behavior regardless of input form.

- **Source-fit failure surfacing via `errors[ch]` is per-channel granular.** When source `S`'s fit for channel `<ch>` fails (and only that channel), every target `T` with `roi_sources[T] == S` records `errors[ch] = f"ROI source {S.name} fit failed: <original_reason>"` and skips that channel — no `store.write_mask` call. **Other channels of `T` that the source DID successfully fit proceed normally from cache.** Rationale: reuses the existing shared dataclass surface (`BatchPhasorItemResult.errors`), doesn't fork the taxonomy, and the per-(source, channel) cache granularity makes "source's mNG succeeded, source's Halo failed" route correctly — targets' mNG apply from cache, targets' Halo error with the source-failed message. The status classifier (`_classify_status`) already lands `partial` for the mixed-outcome case.

- **Channel selection is captured at Start time and applies uniformly to fit + apply.** The list of channels passed to `batch_fit_phasor_masks(channels=...)` is the authoritative set — the use case never re-reads the dialog's channel picker mid-run. If the user deselects a channel after assigning sources, the next Start invocation will pass the new channel set; sources are fitted only for those channels, targets apply only those channels' cached geometry. There is no "phantom" source-time channel that fits but never applies. This is captured by U3 (channel picker state is read by `start_clicked` to build the use case call).

- **Channels passed to the use case must match across sources and targets.** Validation: at the use case's entry, every channel in `channels=` must be present (with `/decay/`) in every path in `paths`. Existing channel-intersection rule, unchanged.

- **Cycle prevention has two defenses, not one.** Dialog construction prevents the user from constructing cycles via the UI: the dropdown for any row shows only `fit own GMM` + other rows whose current assignment is `fit own GMM` (so the user *cannot select* a non-self-fitting source). U2's input validation independently rejects chains/cycles from any programmatic caller (a notebook user building `{a: b, b: a}` by hand). Both defenses are load-bearing — the dialog protects users from themselves; the use-case validation protects against script callers and prevents future contributors from removing one defense thinking "the other handles it."

- **Self-reference (`roi_sources[p] == p`) is normalized, not rejected.** Treat as equivalent to `None`. Simpler API: callers (CLI, dialog, scripts) don't need to know there's a difference between "explicit self-fit via None" and "self-fit via path-equals-key." The use case's input-validation step contains a single `if roi_sources.get(p) == p: ignore (treat as self-fitting)` step before the chain-detection check.

- **Dependency invalidation on assignment change has a persistent surface, not just a status bar.** When row X changes from "fit own GMM" → "use Y" (or X is removed from the queue), any row currently pointing at X falls back to "fit own GMM" AND marks the row with a persistent visual indicator — italic warning text on the dropdown reading "fit own GMM (was: <old_source>, fell back)". The indicator persists until the user explicitly re-interacts with that row's dropdown (clicks it, even if just to confirm). Rationale: the existing dialog's status-bar message auto-clears and a researcher who immediately clicks Start could miss it — exactly the silent-fallback failure mode the feature is built to prevent. The persistent indicator + a brief status-bar message together cover both the "user glances away" and "user notices later" cases.

- **Start guard on un-acknowledged fallback.** When any row has the "fell back" persistent indicator, the Start button is disabled with a tooltip ("Acknowledge fallback on the highlighted row first"). User clicks the dropdown (any interaction, including selecting the same `fit own GMM` value) → indicator clears → Start re-enables. This makes silent-fallback impossible to ship through without a deliberate user interaction.

- **Validation is inline in `_update_start_enabled`.** Already-existing check chain plus three new rules: (a) every non-`None` `roi_source` must point at a dataset still in the queue AND whose own `roi_source is None`; (b) no row carries the "fell back" persistent indicator; (c) the channel set is non-empty (existing).

- **CLI parses `--roi-source TARGET=SOURCE` strictly.** Both paths must resolve to entries in the positional `paths` list AFTER both sides are normalized via `.resolve()`. Validation surface: argparse with `action="append"`, post-parse splits on `=`, `.resolve()`s each piece, resolves the positional `paths` list too, then compares — set membership now correctly handles relative vs. absolute argv. Enforces "no SOURCE is also a TARGET" rule (chain detection).

---

## Open Questions

### Resolved During Planning

- **U1: decompose vs. extend with optional `geometry` kwarg?** Resolved: decompose. Three public functions (`fit_phasor_ellipse`, `apply_ellipse_masks`, and the existing combined facade). Rationale in Key Technical Decisions.
- **What return type does `fit_phasor_ellipse` produce?** Resolved during doc-review: new `PhasorEllipseFit` dataclass in `domain/segmentation/phasor_masks.py`. The bare 3-tuple returned by `gmm_to_phasor_roi_geometry` is too loose for a cache key; the application-layer `PhasorROIGeometry` would violate domain isolation. A small domain dataclass is the right middle ground.
- **What return type does `apply_ellipse_masks` produce?** Resolved during doc-review: new `PhasorEllipseMasks(mask_a, mask_b)` dataclass — no `geometry`, no `sampled_pixels`. Avoids the "`sampled_pixels` becomes a lie when called standalone" semantic drift.
- **Execution ordering: two-pass (all sources first, all targets second) vs. interleaved?** Resolved during doc-review: interleaved per-source-group. Cancellation produces complete per-group results instead of a half-cohort where some sources have masks but their dependents don't. Sources are still fitted strictly before their dependents (R6).
- **How to surface "source's fit failed" failures in the per-item report?** Resolved: per-`errors[ch]` on each dependent target's item, message names the source path and original reason. No new dataclass field. Per-(source, channel) granularity: if a source's mNG succeeds but Halo fails, targets' mNG applies from cache and targets' Halo errors with the source-failed message — the source's per-channel outcome is faithfully propagated.
- **Dialog validation: explicit Validate button or inline `_update_start_enabled` extension?** Resolved: inline.
- **How to surface dependency fallback when the source is removed or flipped?** Resolved during doc-review: persistent visual indicator on the affected row's dropdown ("fit own GMM (was: <old>, fell back)") + Start guard that disables Start until the user explicitly acknowledges the fallback by re-interacting with the row. The status-bar message alone is too easy to miss for a workflow whose entire purpose is preventing silent re-fitting.
- **Path normalization across CLI and dialog?** Resolved during doc-review: the CLI's positional `paths` are resolved via `.resolve()` before `--roi-source` validation, matching the dialog's existing add-time `.resolve()`. U2's input validation also resolves keys/values defensively. The parity test uses one fixture file passed identically to both surfaces.
- **Self-reference (`roi_sources[p] == p`) — reject or normalize?** Resolved during doc-review: normalize (treat as `None`). Simpler API; no three-way different error message across CLI/use-case/dialog.

### Deferred to Implementation

- The exact label text for the dropdown's "fit own GMM" entry — literal `fit own GMM` works; italic styling via a custom `QStyledItemDelegate` is optional. Pick during U3 based on visual fit.
- Long-path disambiguation in the dropdown. Display filename only; if two queued datasets share a basename, display includes parent dir (`subdir_a/untreated.h5` vs. `subdir_b/untreated.h5`). Decide the truncation behavior during U3 when actual cohort sizes hit.
- Status-bar message persistence duration (default Qt is ~5 seconds; could extend to 10 seconds for the cascade message). Decide during U3.
- Whether the end-of-run summary `QMessageBox` shows a "X used shared ROI, Y self-fitted" line in MAIN text alongside the existing succeeded/partial/failed counts, or keeps the provenance entirely in detail text. Lean toward main-text summary line; final decision during U3.
- Whether the (CLI, GUI) parity test asserts byte-identical mask output (stronger, slower) or just identical `roi_sources` dict (faster, narrower). Lean toward byte-identical for the no-`--roi-source` and one-source-one-target cases (covers the happy path end-to-end). Decide during U4.

---

## High-Level Technical Design

> *This illustrates the interleaved per-source-group loop shape and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
batch_fit_phasor_masks(paths, roi_sources={...}, channels=[...], ...):

    # Normalize input — defends against unresolved-path callers.
    paths = [p.resolve() for p in paths]
    roi_sources = {k.resolve(): (v.resolve() if v is not None else None)
                   for k, v in roi_sources.items()}
    # Treat self-reference as self-fitting.
    roi_sources = {k: (None if v == k else v) for k, v in roi_sources.items()}
    # Validate: no chains, all keys/values in paths, etc.

    # ROI cache lives for the duration of this call.
    roi_cache: dict[(resolved_source_path, channel), PhasorEllipseFit] = {}

    # Interleaved per-source-group: process each self-fitting dataset,
    # then immediately process every later-in-list-order target that
    # points at it. Single pass over `paths`.
    processed_paths: set[Path] = set()
    for path in paths:
        if path in processed_paths:
            continue
        if cancel_check and cancel_check():
            break

        if roi_sources.get(path) is None:
            # Self-fitting: fit, cache, apply own masks.
            item = process_self_fitting(path, channels, roi_cache, ...)
            processed_paths.add(path)
            progress_callback(item)

            # Now process all targets pointing at this path,
            # in their original list order.
            for target in paths:
                if target in processed_paths:
                    continue
                if roi_sources.get(target) != path:
                    continue
                if cancel_check and cancel_check():
                    break
                t_item = process_target(target, path, channels, roi_cache, ...)
                processed_paths.add(target)
                progress_callback(t_item)

    # process_self_fitting per channel:
    #   read g/s/decay → intensity = decay.sum(axis=-1)
    #   try:
    #     fit = fit_phasor_ellipse(g, s, intensity, t_fit=t_fit)
    #     roi_cache[(path, ch)] = fit
    #     masks = apply_ellipse_masks(g, s, intensity, fit, t_mask_a=..., t_mask_b=...)
    #     write masks
    #   except ValueError as e: errors[ch] = str(e)

    # process_target per channel:
    #   fit = roi_cache.get((source_path, ch))
    #   if fit is None:                              # source's ch fit failed
    #     errors[ch] = f"ROI source {source.name} fit failed: see source's item"
    #     continue
    #   read target's g/s/decay → intensity
    #   masks = apply_ellipse_masks(g, s, intensity, fit, t_mask_a=..., t_mask_b=...)
    #   write masks
```

---

## Implementation Units

- U1. **Domain decomposition: split `fit_phasor_ellipse_and_apply_masks` into fit + apply**

**Goal:** Extract two public functions (`fit_phasor_ellipse`, `apply_ellipse_masks`) from the current combined helper, plus two new domain dataclasses (`PhasorEllipseFit`, `PhasorEllipseMasks`). Retain the combined function as a thin facade that composes them. No behavioral change for existing callers; new callers (U2's interleaved loop) can call fit and apply independently against different datasets.

**Requirements:** Enables R5, R6, R8.

**Dependencies:** None.

**Files:**
- Modify: `src/percell4/domain/segmentation/phasor_masks.py`
- Modify: `tests/test_domain/test_phasor_masks.py` (add ~6 tests covering the two new entry points; existing 10 tests stay green via the facade)

**Approach:**
- **New domain dataclass `PhasorEllipseFit`** in `domain/segmentation/phasor_masks.py`:
  - `center: tuple[float, float]` — ellipse center in `(g, s)`.
  - `radii: tuple[float, float]` — `(r_parallel, r_perpendicular)`.
  - `angle_deg: float` — major-eigenvector angle in degrees.
  - `sampled_pixels: int` — number of pixels in the fit subset.
  - Pure-domain (no imports outside `domain/`); equality semantics are field-wise so it works as a `dict` value.
- **New domain dataclass `PhasorEllipseMasks`** in `domain/segmentation/phasor_masks.py`:
  - `mask_a: NDArray[np.uint8]`, `mask_b: NDArray[np.uint8]` — that's it.
- **New public function `fit_phasor_ellipse(g_map, s_map, intensity_map, *, t_fit) -> PhasorEllipseFit`.** Steps 1–4 of the current combined function (build fit subset, fit via `single_component_fit_phasor`, eigenstructure → geometry via `gmm_to_phasor_roi_geometry` which returns a bare tuple, wrap into `PhasorEllipseFit`, degeneracy guard).
- **New public function `apply_ellipse_masks(g_map, s_map, intensity_map, fit, *, t_mask_a, t_mask_b) -> PhasorEllipseMasks`.** Steps 5–6 of the current combined function (`phasor_roi_to_mask(g, s, center=fit.center, radii=fit.radii, angle_rad=radians(fit.angle_deg))` → boolean spatial mask, AND with `intensity_map >= t_mask_a/b`, binarize as `uint8`). Trusts the `fit` it's handed — no re-validation of degeneracy.
- **Existing `fit_phasor_ellipse_and_apply_masks` becomes a facade** that composes the two:
  ```
  fit = fit_phasor_ellipse(g, s, intensity, t_fit=t_fit)
  masks = apply_ellipse_masks(g, s, intensity, fit, t_mask_a=..., t_mask_b=...)
  return PhasorEllipseMasksResult(
      geometry=(fit.center, fit.radii, fit.angle_deg),  # existing tuple shape
      mask_a=masks.mask_a, mask_b=masks.mask_b,
      sampled_pixels=fit.sampled_pixels,
  )
  ```
  All existing callers see the unchanged `PhasorEllipseMasksResult` shape; the existing 10 tests stay green byte-for-byte.
- **Degeneracy guard stays in `fit_phasor_ellipse`** (it's a fit-time concern, not an apply-time concern). `apply_ellipse_masks` trusts the fit it's handed.

**Execution note:** Implement test-first. Add the new tests for `fit_phasor_ellipse` and `apply_ellipse_masks` BEFORE refactoring the combined function — that way the refactor lands on a tree where both the old contract (10 existing tests) and the new contracts are pinned.

**Patterns to follow:**
- The current `fit_phasor_ellipse_and_apply_masks` body — it's the literal source for the decomposition.
- `PhasorEllipseMasksResult` dataclass shape from `src/percell4/domain/segmentation/phasor_masks.py` (existing; unchanged; what the facade returns). The new `PhasorEllipseFit` and `PhasorEllipseMasks` dataclasses live in the same module and follow the existing module's `@dataclass(frozen=True)` convention.

**Test scenarios:**
- *Happy path (`fit_phasor_ellipse`).* Synthetic Gaussian phasor blob → returns `PhasorEllipseFit` with center within tolerance of the seeded mean, positive radii, `sampled_pixels` equal to the count of pixels above `t_fit` (NOT zero).
- *Happy path (`apply_ellipse_masks`).* Hand-crafted `PhasorEllipseFit` (center, radii, angle, sampled_pixels picked by the test) + synthetic phasor + intensity → produces `PhasorEllipseMasks(mask_a, mask_b)` where mask_a has higher coverage than mask_b when `t_mask_a < t_mask_b`. **The result has no `geometry` field and no `sampled_pixels` field** — `PhasorEllipseMasks` is two-field-only.
- *Happy path (cross-dataset).* Fit on phasor distribution A (center near 0.3, 0.5), apply against phasor distribution B (center near 0.7, 0.4). The resulting masks reflect ellipse-membership using A's center applied to B's spatial pattern — i.e., B's mask is sparse because most of B's pixels are outside A's ellipse. Validates that fit and apply are genuinely independent operations.
- *Error path (`fit_phasor_ellipse`).* Single pixel above `t_fit` → raises `ValueError("degenerate fit (ellipse has zero area)")` (the existing guard moves with the fit code).
- *Error path (`fit_phasor_ellipse`).* All pixels below `t_fit` → raises `ValueError` propagated from `single_component_fit_phasor`.
- *Edge case (`apply_ellipse_masks`).* `t_mask_a == 0` → mask_a coverage equals the ellipse-only mask (no intensity filtering at the bottom).
- *Edge case (`apply_ellipse_masks`).* NaN pixels in g/s → excluded from both output masks (the mask is `False` there) regardless of the supplied fit.
- *Integration: facade composition.* `fit_phasor_ellipse_and_apply_masks` returns a `PhasorEllipseMasksResult` whose `geometry` is the (center, radii, angle_deg) tuple, `sampled_pixels` matches the underlying fit, `mask_a`/`mask_b` match what the standalone `apply_ellipse_masks` would produce given the same fit. All 10 existing tests in `tests/test_domain/test_phasor_masks.py` stay green unchanged.
- *Integration: no forbidden imports.* The existing `test_pure_domain_no_forbidden_imports` test continues to pass — `PhasorEllipseFit` and `PhasorEllipseMasks` are defined in `domain/segmentation/phasor_masks.py`; neither new function imports from qtpy/napari/h5py/percell4.application.session.
- *Integration: `PhasorEllipseFit` is hashable-by-equality, usable as a dict value.* Pinned by a test that builds two fits with the same field values and asserts `fit1 == fit2`. (Needed for U2's cache; fields are floats so frozen=True + eq=True on the dataclass is sufficient.)

**Verification:**
- All 10 existing tests + the new tests pass via `pytest tests/test_domain/test_phasor_masks.py -v`.
- `fit_phasor_ellipse` returns `PhasorEllipseFit` (new domain dataclass, 4 fields), `apply_ellipse_masks` returns `PhasorEllipseMasks` (new domain dataclass, 2 fields), the facade returns `PhasorEllipseMasksResult` (existing dataclass, unchanged).

---

- U2. **Use case: `roi_sources` kwarg + interleaved per-source-group execution + per-source cache**

**Goal:** Add a `roi_sources: Mapping[Path, Path | None] | None = None` kwarg to `batch_fit_phasor_masks`. Process datasets in interleaved per-source-group order (each self-fitting source is followed immediately by its dependents), caching the source's fitted `PhasorEllipseFit` per channel for the dependents to reuse. Surface source-fit failures via `errors[ch]` on the dependents.

**Requirements:** R4, R5, R6, R7, R8.

**Dependencies:** U1.

**Files:**
- Modify: `src/percell4/application/use_cases/batch_fit_phasor_masks.py`
- Modify: `tests/test_application/test_batch_fit_phasor_masks.py` (add ~8 tests for the new behavior; existing 20 tests stay green because `roi_sources` defaults to `{}`, which means "everyone self-fits" — equivalent to current behavior)

**Approach:**
- New kwarg: `roi_sources: Mapping[Path, Path | None] | None = None`. Default `None` is normalized to `{}`. Sentinel default avoids the mutable-default-argument pitfall while keeping the signature readable. An empty mapping (or all-`None` values) preserves current behavior.
- **Input normalization** at the top of the function:
  - `paths = [p.resolve() for p in paths]`.
  - `roi_sources = {k.resolve(): (v.resolve() if v is not None else None) for k, v in roi_sources.items()}`.
  - Normalize self-reference: `{k: (None if v == k else v) for k, v in roi_sources.items()}`.
- **Up-front validation** (raise `ValueError` before any I/O):
  - Every key in `roi_sources` must be in (resolved) `paths`.
  - Every non-`None` value in `roi_sources` must be in (resolved) `paths`.
  - No path can appear as both a key with a non-`None` value AND a non-`None` value in some other entry (the "no chains" rule — sources must be self-fitting).
- **Interleaved per-source-group loop** (replaces the prior two-pass design):
  - Iterate `paths` in list order. Maintain `processed_paths: set[Path]` to skip already-handled datasets.
  - For each `path`:
    - Skip if `path in processed_paths` (already handled when its source-group ran).
    - Check `cancel_check()` between datasets.
    - If `roi_sources.get(path) is None` (self-fitting): process it (fit + cache + apply own masks), record `item`, fire `progress_callback(item)`, add to `processed_paths`. Then immediately iterate `paths` again in original order, processing every `target` where `roi_sources.get(target) == path` (and `target not in processed_paths`) — apply cached fit, record `target_item`, fire `progress_callback(target_item)`. Cancel check between each target too.
    - If `roi_sources.get(path) is not None` AND `path not in processed_paths`: shouldn't happen if validation is correct (every target's source comes earlier or is processed in the same group), but defensively log and skip.
- **Per-source-group internals:**
  - Source: for each channel, read decay → `intensity_map = decay.sum(axis=-1)`, read `g`/`s` (unfiltered only, computing via `ComputePhasor` when absent and `ensure_phasor=True`), call `fit_phasor_ellipse(...)`. On success: `roi_cache[(path, ch)] = fit`, call `apply_ellipse_masks(g, s, intensity, fit, ...)`, `store.write_mask` × 2. On `ValueError`: `errors[ch] = str(exc)` (and the cache entry for `(path, ch)` is left unpopulated → dependents detect this).
  - Target: for each channel, look up `fit = roi_cache.get((source_path, ch))`. If `None` → `errors[ch] = f"ROI source {source_path.name} fit failed: see source's item for details"` and skip. If present → read target's decay/g/s, compute intensity, call `apply_ellipse_masks(...)`, `store.write_mask` × 2.
- **Per-channel partial-success preserved.** Source's mNG fits → cache `(source, "mNG")` populated. Source's Halo degenerate → `errors["Halo"]` on source's item, cache `(source, "Halo")` NOT populated. Targets pointing at source: mNG applies from cache → `processed`; Halo: cache miss → `errors["Halo"]` on target with source-failed message. Target's status: `partial`.
- **Status classification** unchanged from current behavior: all-channels processed → `succeeded`; some processed + some errored/skipped → `partial`; all skipped, no errors → `skipped_no_changes`; dataset-level open failure → `failed`.
- `progress_callback(item)` fires once per dataset, in the order datasets are *processed* (source → its dependents → next source → its dependents). The dialog/CLI surfaces accurate state.
- `cancel_check` is consulted between each dataset (and between each target within a source-group). Cancellation produces complete per-group results: if `s1` and its 2 dependents are done when the user clicks Cancel, those 3 datasets have masks; `s2` and its dependents have nothing. No half-cohort state where a source has masks but its dependents don't.

**Execution note:** Implement test-first. Pin the new interleaved-loop behavior + per-(source, channel) partial-success behavior with failing tests before changing the loop.

**Technical design:** *(optional — see the High-Level Technical Design section above; the interleaved per-source-group loop is the directional reference.)*

**Patterns to follow:**
- The existing single-pass loop in `batch_fit_phasor_masks` — the new code is a wrapping reorder + a cache, not a rewrite.
- `Path.resolve()` normalization for cache keys, mirroring U3's existing end-of-run-refresh path resolution.

**Test scenarios:**
- *Happy path (self-fit only, empty `roi_sources`).* One dataset with one channel, no `roi_sources` → identical behavior to the current 20-test suite. Status `succeeded`, one mask of each suffix written. (Existing test surface; must keep passing.)
- *Happy path (one source, one target).* `roi_sources = {target: source}`, both `paths`. Source's fit succeeds. Both items are `succeeded`. Cache reuse pinned by giving source vs. target distinct phasor distributions (source Gaussian centered at (0.5, 0.3); target Gaussian centered at (0.7, 0.4)). Target's mask coverage using source's ROI is materially different from what it would be self-fitting — asserts the cache was actually consulted.
- *Happy path (one source, two targets).* `paths = [src, t1, t2]`, `roi_sources = {t1: src, t2: src}`. Patch `fit_phasor_ellipse` to count calls — exactly one per channel (the source's). Both targets and the source have masks on disk.
- *Mixed batch interleaved.* `paths = [s1, t1, s2, t2]`, `roi_sources = {t1: s1, t2: s2}`. Verify progress callback order via list capture: `[s1, t1, s2, t2]` (source-group interleave, not all-sources-then-all-targets).
- *Per-(source, channel) partial.* `roi_sources = {target: source}`, channels=["mNG", "Halo"]. Source's mNG fits cleanly; source's Halo is degenerate (single pixel above `t_fit`). Expected:
  - Source item: `partial`, `processed=("mNG",)`, `errors={"Halo": "degenerate fit ..."}`, mNG masks on disk, Halo masks NOT on disk.
  - Target item: `partial`, `processed=("mNG",)`, `errors={"Halo": <ROI-source-failed message naming source>}`. Target's mNG masks on disk applying source's mNG ROI; target's Halo masks NOT on disk.
- *Source fit fails on the only requested channel.* `roi_sources = {target: source}`, channels=["mNG"]. Source's mNG degenerate. Source item: `failed` (or `partial` if other channels in metadata; design says `failed` when zero processed). Target item: `partial` or `failed` depending on whether anything else processed.
- *Source not in paths.* `roi_sources = {target: missing.h5}` where `missing.h5 ∉ paths` → `ValueError` raised before any I/O. Error message names which path is missing.
- *Target not in paths.* `roi_sources = {missing.h5: source}` where `missing.h5 ∉ paths` → `ValueError`.
- *Chain rejected.* `roi_sources = {a: b, b: c}` where `b` is both a target and a source → `ValueError` mentions chain.
- *Self-reference normalized.* `roi_sources = {a: a}` → silently treated as `{a: None}`, processes normally as self-fitting. No `ValueError`. (Documents the simpler-API decision.)
- *Cancel mid-source-group preserves group atomicity.* `paths = [s1, t1, s2, t2]`, `roi_sources = {t1: s1, t2: s2}`. Cancel flag returns `True` after `t1` processed. Loop breaks; `s2` and `t2` not processed. Report contains items for `[s1, t1]` — a complete per-group result, not a half-state.
- *Cancel between source-groups.* Cancel flag returns `True` immediately after `s1`'s targets are done, before `s2` starts. Report contains `[s1, t1]`. Same outcome as above; verifies the cancel surface is consulted at the source-group boundary.
- *Path normalization (relative + absolute).* `paths = [Path("./a.h5")]`, `roi_sources = {Path("/abs/a.h5"): None}` where both resolve to the same canonical path → after input normalization, `roi_sources[resolved_a] is None`, processes as self-fitting (no `ValueError` for "target not in paths"). Pins that the normalization is consistent.
- *Channel passed at Start time, not re-read mid-run.* Stub `batch_fit_phasor_masks` is called with `channels=["mNG"]`. Internally, the use case never re-reads any dialog/CLI state — even if a hypothetical "channels changed" signal existed, the use case would ignore it. Pinned by patching `fit_phasor_ellipse` and asserting it's called with `channels=["mNG"]` only, never `["Halo"]`.

**Verification:**
- All 28 tests pass (20 existing, 8 new).
- The empty-`roi_sources` path is byte-identical to current behavior on every existing fixture.

---

- U3. **Dialog: ROI source column + refresh + validation + summary line**

**Goal:** Add a per-row `QComboBox` to `PhasorMasksDialog`'s dataset list (mockup in the origin doc), wire it into the refresh chain, extend `_update_start_enabled` to validate assignments, and tag each dataset in the end-of-run `QMessageBox` summary with its ROI provenance.

**Requirements:** R1, R2, R3, R4, R11.

**Dependencies:** U2.

**Files:**
- Modify: `src/percell4/gui/phasor_masks_dialog.py`
- Modify: `tests/test_gui/test_phasor_masks_dialog.py` (add ~10 tests; existing 24 stay green because the dropdown defaults to "fit own GMM" and the existing flows never touch it)

**Approach:**

*Dataset row widget structure:*
- Current `_PendingDataset` dataclass gains two fields: `roi_source: Path | None = None` and `fell_back: bool = False`. The `fell_back` flag is the "user must acknowledge" sentinel set by the refresh logic when a source disappears.
- Each row uses `setItemWidget(item, custom_row_widget)` on the existing `QListWidget`. The custom widget is a `QWidget` with `QHBoxLayout`:
  - Path label (`QLabel`, `setTextElideMode(Qt.ElideMiddle)`, stretch factor 1 — takes the leftover width). Tooltip is the full path.
  - Spacer (`QSizePolicy.Expanding` so the combo doesn't expand).
  - `QComboBox` for ROI source — fixed width 200px. (Caps verbosity on long filenames; tooltip shows full path.)
  - Remove button (`QPushButton("×")`, fixed 24×24px). Connected directly to a row-local removal handler — no longer needs to go through `_on_remove_selected` and `selectedItems()`.
- The existing `_dataset_list` `QListWidget` stays; selection-based UI (e.g., highlighting on click) still works because the custom widget doesn't swallow the row-level click event by default. (Verify during U3; if it does swallow, set `QSizePolicy(Preferred, Preferred)` on children and let row clicks bubble.)
- The existing bottom "Remove selected" button is removed; row-local `×` is the only Remove surface now. Less ambiguous and avoids the new per-row interaction breaking the prior `selectedItems()` flow.

*Dropdown population (`_refresh_roi_source_dropdowns`):*
- First item (always): `fit own GMM`, `userData = None`. Rendered in italic via a custom `QStyledItemDelegate` (optional — see Open Questions).
- Then: each other dataset where `_PendingDataset.roi_source is None` AND `not _PendingDataset.fell_back`, labelled by filename (shortened with parent dir if duplicate basenames exist in the queue), `userData = full_resolved_path`.
- Selected item: the row's current `roi_source` value (or `fit own GMM` when `None`). If the row's `fell_back` flag is `True`, the displayed text is `"fit own GMM (was: <old_source_name>, fell back)"` rendered in warning color (use `QPalette.Highlight` or a stylesheet); the dropdown's first item label changes accordingly.

*Refresh chain (called when any row's combo changes OR a dataset is added/removed):*
1. Update the source row's `roi_source` field from the new selection's `userData`. Clear `fell_back` if it was set (the user just acknowledged by interacting).
2. Recompute the set of "self-fitting" paths.
3. For every other row whose current `roi_source` no longer appears in the self-fitting set: store the previous `roi_source` value, set `roi_source = None`, set `fell_back = True`. (Persistent indicator turns on.)
4. Rebuild every row's combo from the updated state (the persistent indicator label change is part of this rebuild).
5. Emit a single status-bar message naming all affected rows: `"AsTreated_a, AsTreated_b: fell back to fit own GMM (untreated_a removed)"`. Auto-clear after Qt's default duration; the persistent row indicator is what the user actually relies on.
6. Call `_update_start_enabled()`.

*`_update_start_enabled` extension:*
- Existing check chain plus three new rules:
  - (a) Every non-`None` `roi_source` points at a dataset still in the queue AND whose own `roi_source is None`. (Defense-in-depth; refresh chain enforces this structurally.)
  - (b) No row has `fell_back == True`. Start tooltip when disabled by this rule: `"Acknowledge the highlighted rows first — click their ROI dropdown to clear the warning."`
  - (c) The existing channel-set-non-empty check.

*Channel intersection unchanged:*
- The current `_refresh_channel_picker` logic walks every selected dataset and intersects channels. It does not need to know about ROI sources — sources are themselves selected datasets, so their channels are in the intersection by construction.

*Dataset-removal handling:*
- The row-local `×` button calls a new `_on_remove_row(row_index)`:
  - Capture the removed path.
  - Remove the entry from `_pending_datasets` and the `QListWidget`.
  - Trigger the refresh chain (step 3 above will set `fell_back = True` on any dependents). Status-bar message uses "removed" rather than "no longer self-fitting" wording.
  - Call `_refresh_channel_picker` and `_update_start_enabled`.
- Removing the LAST dataset clears the channel picker (existing behavior) and disables Start (existing).

*Channel-reselection robustness:*
- The channel picker's state is read at Start time only (`_on_start_clicked`). Changing the channel picker after assigning sources does NOT re-fire the refresh chain — channels are an orthogonal selection. The use case receives a snapshot of `(dataset_paths, channels, roi_sources, t_fit, t_mask_a, t_mask_b, suffix_a, suffix_b)` at Start time and never re-reads dialog state mid-run.
- Pinned by a test: assign source, deselect a channel, click Start → use case is called with the post-deselection channel set, NOT the at-assignment-time channel set.

*End-of-run summary:*
- Main text adds a one-line summary alongside the existing succeeded/partial/failed counts: `"<N_self> self-fitted, <N_shared> used shared ROI."`
- Detailed text (collapsible) tags each per-dataset line: `<name.h5> [source: self]` or `<name.h5> [source: <source_name.h5>]`.
- Failure case detail line example: `AsTreated_a.h5 [source: untreated_a.h5] — partial: errors={mNG: "ROI source untreated_a.h5 fit failed: see source's item for details"}`. The researcher reads two lines (target + source) to diagnose.

*QSettings persistence:*
- Do NOT persist `roi_source` across dialog sessions — dataset paths change run-to-run, so persisted assignments would be invalid. Existing QSettings keys (thresholds, suffixes) still persist. Add a comment in the `_save_qsettings` method explaining the omission.

**Execution note:** Implement test-first with `pytest-qt`. Widget tests must drive real signals on the new `QComboBox` (`qtbot.mouseClick` to open, `QTest.keyClick` to select, or `combo.activated.emit(idx)` if direct signal emission is needed) — programmatic `setCurrentIndex` would bypass the wiring per the qt-wire-user-edit-signals learning.

**Patterns to follow:**
- The existing `_refresh_channel_picker` for the rebuild-on-change pattern.
- The existing `_update_start_enabled` for the validation chain.
- `src/percell4/gui/flim_fret_dialog.py` if it has any per-row widget composition worth mirroring (it uses a simpler list, but the QWidget-as-row-content idiom is standard Qt).

**Test scenarios:**
- *Happy path: default state.* Two datasets added → both rows' dropdowns show `fit own GMM` (selected) + the other dataset's filename as an alternative. Nothing is auto-assigned.
- *Happy path: assign a source.* User picks `AsTreated_a` row's dropdown, changes to `untreated_a` → `_PendingDataset.roi_source` for AsTreated_a is set to untreated_a's resolved path. The dropdown on untreated_a's row no longer offers AsTreated_a as a source candidate.
- *Edge case: change source row back to self-fitting.* User clicks AsTreated_a's dropdown again, picks `fit own GMM`. untreated_a's dropdown rebuilds to once again offer AsTreated_a.
- *Persistent fallback indicator + Start guard (the load-bearing UX test).* untreated_a is the source of AsTreated_a. User changes untreated_a to "use AsTreated_c" (in a 3-dataset scenario). Result:
  - AsTreated_a's `_PendingDataset.fell_back == True`, `roi_source == None`.
  - AsTreated_a's combo displays `"fit own GMM (was: untreated_a.h5, fell back)"` with warning styling.
  - Status-bar message fires once: `"AsTreated_a: fell back to fit own GMM (untreated_a is no longer self-fitting)"`.
  - **Start button is disabled.** Tooltip when hovered: `"Acknowledge the highlighted rows first — click their ROI dropdown to clear the warning."`
  - User clicks AsTreated_a's combo (any interaction — re-selecting `fit own GMM` is fine) → `fell_back` clears → combo shows plain `"fit own GMM"` → Start enables again.
- *Edge case: remove a source row.* untreated_a is the source of AsTreated_a. User clicks the `×` button on untreated_a's row. AsTreated_a's `fell_back == True`, combo shows warning-styled `"fit own GMM (was: untreated_a.h5, fell back)"`. Status-bar message uses "removed" wording. Start disabled until acknowledged.
- *Edge case: multiple dependents fall back together.* untreated_a is the source of AsTreated_a AND AsTreated_b. User changes untreated_a's combo to a third dataset. Both AsTreated_a and AsTreated_b get `fell_back = True`. Single status-bar message names both. Start disabled. Both rows must be acknowledged independently.
- *Channel reselection after source assignment.* User adds 2 datasets, both have channels [mNG, Halo], assigns source on AsTreated_a, then DESELECTS Halo in the channel picker. `_PendingDataset.roi_source` is unchanged (no refresh fires). Click Start: `batch_fit_phasor_masks` is called with `channels=["mNG"]` only — the post-deselection set. Verify via monkeypatch capture.
- *Channel intersection isn't affected by source assignment.* `_refresh_channel_picker` runs before and after a source assignment; the channel set is identical. (Sources are selected datasets; their channels are part of the intersection by construction.)
- *Validation: source no longer in queue, Start disabled.* Mutate `_PendingDataset.roi_source` to point at a path that's not in `_pending_datasets` (simulated by direct dataclass mutation without going through the UI). `_update_start_enabled` disables Start independent of the `fell_back` rule. (Defense-in-depth.)
- *Signal wiring.* `qtbot.mouseClick` + `qtbot.keyClick` on the combo (not programmatic `setCurrentIndex`) triggers both `_refresh_roi_source_dropdowns` and `_update_start_enabled`. Use `qtbot.waitSignal(combo.activated)` to confirm the signal fires.
- *Row-local Remove button (`×`).* Clicking the `×` on a row removes that specific row from `_pending_datasets`, regardless of whether any other rows are selected. The old "Remove selected" bottom button is gone.
- *Summary line in main text.* Stub `batch_fit_phasor_masks` returns three `succeeded` items: s1 self, t1 and t2 sourcing from s1. `QMessageBox` main text contains: `"3 succeeded, 0 partial, 0 failed. 1 self-fitted, 2 used shared ROI."`. Detailed text contains tagged lines.
- *Failure summary line.* Source's mNG fits, source's Halo degenerate. Target uses source. Both items are `partial`. Main text: `"0 succeeded, 2 partial, 0 failed. 1 self-fitted, 1 used shared ROI."`. Detail text contains: source's per-channel breakdown + target's `errors[Halo]` line naming the source.
- *Use case kwarg parity.* When the user clicks Start, `batch_fit_phasor_masks` is called with `roi_sources = {t1.path.resolve(): s1.path.resolve(), t2.path.resolve(): s1.path.resolve(), s1.path.resolve(): None}` — keys are resolved, values are resolved. Monkeypatch capture asserts.
- *Integration (end-of-run conditional refresh).* Refresh still fires only when the active dataset is among the processed paths (the existing 3 refresh-related tests stay green).
- *Path-label tooltip.* Each row's path label has a tooltip equal to the full resolved path (the label itself elides the middle when the cohort dir is long).

**Verification:**
- All 34 tests pass (24 existing, 10 new).
- The dialog manually exercised against 4 datasets produces masks consistent with the test scenarios.

---

- U4. **CLI: `--roi-source TARGET=SOURCE` flag**

**Goal:** Add a repeated `--roi-source` flag to `percell4-batch-phasor-masks`. Parse `TARGET=SOURCE` pairs, validate them up-front, pass through to `batch_fit_phasor_masks` as `roi_sources`. Tag stdout output with ROI provenance.

**Requirements:** R9, R10, R11.

**Dependencies:** U2.

**Files:**
- Modify: `src/percell4/interfaces/cli/batch_phasor_masks.py`
- Modify: `tests/test_cli_batch_phasor_masks.py` (add ~6 tests; existing 23 stay green because the flag is optional)

**Approach:**
- argparse: `parser.add_argument("--roi-source", action="append", metavar="TARGET=SOURCE", default=[], ...)`.
- Post-parse, after `resolve_paths` returns the positional paths:
  - **Normalize all positional paths via `.resolve()`** before any further validation. This is a new step in this CLI; the shared `_batch_report.resolve_paths` does NOT resolve, so we resolve locally. (Decided NOT to modify the shared helper because other CLIs work without it.)
  - For each `--roi-source` value: split on `=` (must produce exactly two non-empty pieces; else exit 2 with `"invalid --roi-source: expected TARGET=SOURCE, got '<value>'"`).
  - Resolve each piece via `Path(piece).resolve()`.
  - Validate: every TARGET and SOURCE must be in the resolved positional paths set. Error message names which path is missing.
  - Validate: no SOURCE may appear as a TARGET in some other `--roi-source` (chain rejection). Error: `"chain detected: <path> is both a target and a source — sources must be self-fitting"`.
  - Self-reference (`TARGET == SOURCE`): silently normalized to "no entry" (matches U2's self-reference handling — see Key Technical Decisions).
  - Build `roi_sources: dict[Path, Path | None]`: explicit-`None` form for clarity — every entry in resolved `paths` gets either `roi_sources[p] = <source>` (target) or `roi_sources[p] = None` (self-fitting).
- Pass `roi_sources` through to `batch_fit_phasor_masks(...)`.
- **`--dry-run` output** extends the plan-printing block to show each dataset's ROI source tag:
  ```
  Would process 3 datasets × 1 channel(s) with t_fit=10.0, t_mask_a=0.0, t_mask_b=5.0:
    untreated_a.h5    [source: self]
    AsTreated_a.h5    [source: untreated_a.h5]
    AsTreated_b.h5    [source: untreated_a.h5]
  ```
  Format matches the dialog's detailed-text tagging.
- **Real-run stdout: wrap the existing `_batch_report` call locally.** Do NOT modify the shared `_batch_report.format_item_line` helper (it's used by `batch_rename_resource` + `batch_delete_resource` + the existing phasor-masks CLI; modifying it would affect their output). Instead, this CLI's `_print_progress` function appends the `[source: ...]` tag locally after calling the shared formatter:
  ```
  line = _batch_report.format_item_line(item, verb="processed")
  src = roi_sources.get(item.h5_path.resolve())
  tag = "[source: self]" if src is None else f"[source: {src.name}]"
  print(f"{line}  {tag}")
  ```
- Summary printout at end-of-run mirrors the dialog: `"N succeeded, N partial, N failed. N self-fitted, N used shared ROI."`

**Execution note:** Implement test-first. Pin the new argparse behavior and the validation rejections with failing tests before adding the flag.

**Patterns to follow:**
- Existing argparse + validation pattern in `batch_phasor_masks.py`.
- Existing `(CLI, GUI) parity` test (`test_cli_gui_parity`) for extension — add a parallel test that asserts the same `roi_sources` dict reaches the use case from both surfaces.

**Test scenarios:**
- *Happy path (single source).* `--roi-source target.h5=source.h5` → `roi_sources` dict reaching the use case is `{target_resolved: source_resolved, source_resolved: None}`. Keys resolved, values resolved.
- *Happy path (two targets, one source).* Two `--roi-source` flags both pointing at the same source → both targets in the dict; source's value is `None`.
- *Path normalization: relative argv.* Run from a directory containing `untreated_a.h5`; positional `paths` arg is `untreated_a.h5` (no slash), `--roi-source AsTreated_a.h5=untreated_a.h5`. Both halves resolve to absolute paths. Use case receives resolved keys/values; no "TARGET not in paths" error spuriously fires.
- *Validation: malformed value.* `--roi-source foo` (no `=`) → exit 2, stderr message names the offending value.
- *Validation: empty TARGET or SOURCE.* `--roi-source =source.h5` or `--roi-source target.h5=` → exit 2.
- *Validation: TARGET not in paths.* `--roi-source missing.h5=source.h5` where `missing.h5` isn't positional → exit 2, message names `missing.h5`.
- *Validation: SOURCE not in paths.* `--roi-source target.h5=missing.h5` → exit 2.
- *Validation: chain.* `--roi-source a.h5=b.h5 --roi-source b.h5=c.h5` → exit 2, message mentions `b.h5` is both source and target.
- *Self-reference normalized.* `--roi-source a.h5=a.h5` → no error, equivalent to omitting the flag for `a.h5`. Pinned by capturing the use case kwarg: `roi_sources` does not contain `a.h5 → a.h5`.
- *Dry-run output.* `--dry-run --roi-source target.h5=source.h5` prints exactly:
  ```
  Would process 2 datasets × ... with ...:
    source.h5  [source: self]
    target.h5  [source: source.h5]
  ```
  Exit 0. Use case not invoked (verified via monkeypatch).
- *Real-run stdout.* Stub `batch_fit_phasor_masks` to return `succeeded` items for each path; verify per-line output includes the `[source: ...]` tag. The shared `_batch_report.format_item_line` is NOT modified — verify by checking other CLIs' output is unchanged (a snapshot test against `batch_rename_resource` or just a string check that the helper signature is unchanged).
- *Summary printout.* `succeeded, partial, failed` counts + `"N self-fitted, N used shared ROI."` line.
- *(CLI, GUI) parity with shared fixture.* Build ONE tmp `.h5` fixture. Drive the same operation via the CLI and via the dialog. Assert the `roi_sources` kwarg captured by a monkeypatched `batch_fit_phasor_masks` is byte-equal in both invocations. Then also assert the produced masks on disk are `np.array_equal` between the two runs. This is the stronger parity claim — pinned for both the no-`--roi-source` case (existing test extended) and the one-source-one-target case.

**Verification:**
- All 29 tests pass (23 existing, 6 new).
- `percell4-batch-phasor-masks --help` shows `--roi-source` in the options list.
- The (CLI, GUI) parity test passes for both the no-`roi-source` case (existing behavior) and the with-`roi-source` case (new behavior).

---

## System-Wide Impact

- **Interaction graph:** The new code is contained to four files. U1's facade preserves the existing call surface; U2's new kwarg defaults to "no ROI sources"; U3's dropdown defaults to "fit own GMM"; U4's flag is optional. No upstream caller of any of these surfaces has to change.
- **Error propagation:** Source-fit failures appear in the source's own item (existing path) AND in every dependent target's item via `errors[ch]` (new path). The status taxonomy doesn't change; the classifier already routes `errors`-but-no-`processed`-channels to `partial` or `failed` appropriately.
- **State lifecycle risks:**
  - The ROI cache lives only for the duration of one `batch_fit_phasor_masks` call. No cross-run leakage by construction.
  - Path normalization via `Path.resolve()` is critical for cache key correctness — same risk and mitigation as the existing end-of-run viewer refresh.
- **API surface parity:** Dialog and CLI both build `roi_sources` from the same conceptual shape (target → source) and pass it through to the same use case. The (CLI, GUI) parity test in U4 is the contract enforcement.
- **Integration coverage:** The interleaved per-source-group loop crosses unit boundaries (U1 helpers, U2 orchestration). U2's "Mixed batch interleaved" test (`s1`, `t1`, `s2`, `t2` with progress callback order `[s1, t1, s2, t2]`) is the cross-layer integration scenario.
- **Unchanged invariants:**
  - `BatchPhasorItemResult` / `BatchPhasorReport` dataclasses are NOT modified.
  - The shared `_batch_report.format_item_line` helper is NOT modified (the new `[source: ...]` tag is appended at the CLI call site, not in the helper).
  - The channel-intersection rule, the collision check, the unfiltered-g/s rule, and the end-of-run conditional viewer refresh all carry forward unchanged.
  - U2's existing happy/partial/failed/skipped classifications are unchanged for the empty-`roi_sources` case (the existing 20 tests pin this).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Decomposing U1 inadvertently changes facade behavior; 10 existing tests pass but a subtle numerical drift slips through. | The facade is a literal composition: `fit = fit_phasor_ellipse(...); masks = apply_ellipse_masks(g, s, intensity, fit, ...); return PhasorEllipseMasksResult(geometry=(fit.center, fit.radii, fit.angle_deg), mask_a=masks.mask_a, mask_b=masks.mask_b, sampled_pixels=fit.sampled_pixels)`. No reordering, no extra steps. Tests pin the existing input → output mapping byte-for-byte. |
| `PhasorROIGeometry` confusion — the application-layer dataclass shares a similar name. | U1 introduces `PhasorEllipseFit` (domain) — a distinct name. Code review for U1 explicitly checks that the new dataclass is in `domain/segmentation/phasor_masks.py` (not `application/use_cases/run_phasor_gmm.py`), has 4 fields (not 9), and no caller in U2 imports `PhasorROIGeometry`. |
| Path equality issues — `Path("./a.h5")` vs `Path("/abs/.../a.h5")` cache key mismatch across surfaces. | Three layers of `.resolve()` normalization: (1) dialog `_PendingDataset.h5_path` already resolved at add-time (existing); (2) U4 resolves positional `paths` and `--roi-source` halves before validation (new); (3) U2 defensively resolves keys/values at input-normalization step (new). U2 test "Path normalization (relative + absolute)" + U4 test "Path normalization: relative argv" pin both surfaces. |
| Dialog dropdown signal not wired → tests pass with programmatic `setCurrentIndex` but the real signal never fires in manual use. | U3 tests use `qtbot.mouseClick` + `qtbot.waitSignal(combo.activated)` instead of `setCurrentIndex`. Per the qt-wire-user-edit-signals learning. |
| (CLI, GUI) drift on the `roi_sources` dict — easy to introduce when both surfaces independently build the same shape. | U4's parity test uses ONE shared fixture path driven through both the CLI and the dialog. Asserts the captured `roi_sources` dict is byte-equal AND the resulting masks on disk are `np.array_equal`. Exercises both no-`roi_sources` and one-source-one-target cases. |
| Source-fit failure leaves dependents stuck with a vague error. | Error message format is specific: `f"ROI source {src.name} fit failed: see source's item for details"` — and the source's own item carries the actual `ValueError` message in its `errors[ch]`. Researcher reads both lines (source item + target item) in the end-of-run detail text. Per-(source, channel) granularity means the target's other channels still process if the source's other channels succeeded. |
| User assigns a source, then removes the source or changes its `roi_source` → dependents silently fall back; researcher misses the message and ships wrong-gated masks. | Persistent visual indicator on the affected rows (`fit own GMM (was: ..., fell back)` in warning color) + Start guard that disables Start with a tooltip until the user explicitly re-interacts with each affected row. This makes silent-fallback impossible to ship through. U3 test "Persistent fallback indicator + Start guard" pins the full UX. |
| Cancellation mid-source-group could leave a single dataset half-processed (e.g., one mask of two written). | `cancel_check` is consulted between datasets and between targets within a source-group, NOT mid-dataset. The interleaved per-source-group order ensures cancellation produces complete per-group results: if `s1 + its dependents` are done, those have masks; `s2` and its dependents have nothing. U2 test "Cancel mid-source-group preserves group atomicity" pins this. |
| `sampled_pixels` semantic drift between fit and apply standalone. | Resolved by giving `apply_ellipse_masks` its own smaller dataclass `PhasorEllipseMasks(mask_a, mask_b)` — no `sampled_pixels` field on the apply-only path. The facade composes both into the existing `PhasorEllipseMasksResult` (which still carries `sampled_pixels` from the fit) for backward compatibility. |
| `_batch_report.format_item_line` modified for the source tag → affects other CLIs. | U4 deliberately does NOT modify the shared helper. The CLI wraps the call locally and appends the tag in its own print function. Verified by a snapshot test that other CLIs' output is unchanged. |
| Channel-set drift between dialog and use case — user changes channel selection after assigning sources. | Channel set is captured at Start time and frozen for the use case call. Use case never re-reads dialog state. U3 test "Channel reselection after source assignment" pins this. |

---

## Documentation / Operational Notes

- No `docs/solutions/` capture warranted unless the implementation surfaces a genuinely new pattern. The ROI-source-cache is straightforward enough to live in the use case's docstring.
- `src/percell4/CLAUDE.md` and `src/percell4/gui/CLAUDE.md` may want a one-line mention of the new dialog feature if they reference the workflow at all today — check during U3 implementation. (No `CLAUDE.md` files reference the phasor-masks workflow today per a grep of the current tree, so probably nothing to update.)
- Audit retrieval gate (per project CLAUDE.md R15/R16): the implementer should run `python3 scripts/learnings_applicability.py <path>` for U2's file before committing (it's in `src/percell4/application/use_cases/`, a T1 path).

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-phasor-masks-shared-roi-requirements.md](../brainstorms/2026-05-27-phasor-masks-shared-roi-requirements.md)
- **Parent workflow plan:** [docs/plans/2026-05-27-001-feat-phasor-masks-workflow-plan.md](2026-05-27-001-feat-phasor-masks-workflow-plan.md) (already merged; status `completed`)
- Related code:
  - `src/percell4/domain/segmentation/phasor_masks.py` — U1
  - `src/percell4/application/use_cases/batch_fit_phasor_masks.py` — U2
  - `src/percell4/gui/phasor_masks_dialog.py` — U3
  - `src/percell4/interfaces/cli/batch_phasor_masks.py` — U4
- Related commit: `39190037` (last commit on the parent workflow, the baseline this plan extends).
