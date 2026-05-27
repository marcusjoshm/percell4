---
title: Automated Phasor-Masks Workflow
type: feat
status: completed
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-phasor-masks-workflow-requirements.md
---

# Automated Phasor-Masks Workflow

## Overview

Add a fourth entry to the PerCell4 Workflows tab — **"Automated phasor-masks workflow"** — plus a matching CLI `percell4-batch-phasor-masks`. Both surfaces fan out a fixed unattended recipe across N HDF5 datasets × M selected FLIM channels: for each (dataset, channel) pair, fit a single-cluster GMM ellipse on phasor pixels above an intensity threshold, then apply that ellipse twice (at two different intensity thresholds) to produce two binary masks under `/masks/<channel><suffix>`. Both surfaces are backed by a single new use case under `src/percell4/application/use_cases/`.

The GUI follows the **`FlimFretDialog` pattern** (self-driving modal dialog with an inline `QProgressDialog`-driven loop on the main thread), **not** the `BaseWorkflowRunner` pattern. Rationale: this workflow has no QC phases, no thresholding rounds, no run-folder provenance need — the outputs land in each dataset's `.h5`. `BaseWorkflowRunner`'s contract is tightly bound to `WorkflowConfig` (which requires `datasets`/`cellpose`/`thresholding_rounds` fields that don't apply here) and its inherited summary dialog reads `runner._config.datasets` directly. Adopting the dialog pattern dissolves four would-be architectural concessions in one move.

---

## Problem Frame

The researcher's manual phasor-mask protocol — fit n=1 GMM ellipse, apply twice at permissive and conservative intensity thresholds — is identical every run and takes ~6 GUI clicks per channel per dataset. Across a 20-dataset × 2-channel study that's ~240 clicks of mechanical work, with no scientific decisions interleaved. The pair of masks captures one lifetime population at two stringencies; both are needed downstream. See origin: `docs/brainstorms/2026-05-27-phasor-masks-workflow-requirements.md`.

---

## Requirements Trace

- R1 (origin R1–R3). Workflow accepts N `.h5` paths; channel picker auto-narrows to channels present in **every** selected dataset AND backed by `/decay/<channel>` in every selected dataset.
- R2 (origin R4–R5). Three intensity thresholds (defaults 10 / 0 / 5) and two mask-name suffixes (defaults `_phasor_1` / `_phasor_5`) are exposed as editable form fields in the GUI and as CLI flags.
- R3 (origin R6–R8). For each (dataset, channel) pair: ensure phasor exists (idempotent pre-flight), fit single-cluster ellipse on pixels with `intensity_map ≥ t_fit`, apply ellipse twice to produce two `uint8` masks. `intensity_map` is derived from `decay.sum(axis=-1)` of the same `/decay/<channel>` used to compute the phasor — never read from sibling `/intensity` per the FLIM cross-layer alignment learning.
- R4 (origin R9). Fully unattended — no per-dataset preview, approval, or progress dialog beyond the standard runner progress label.
- R5 (origin R10). Existing masks of the same name are overwritten silently; the end-of-run report flags which datasets had overwrites.
- R6 (origin R11–R13, enhanced). Per-(dataset, channel) status follows the 4-state taxonomy `succeeded` / `partial` / `skipped_no_changes` / `failed` (matching `BatchPhasorItemResult`). The origin spec listed only `succeeded` / `failed`; the 4-state taxonomy is an **enhancement** to capture the natural per-item failure mode where one of two mask writes succeeds and the other fails (`partial`) or where a channel is absent from a dataset entirely (`skipped_no_changes`). Validation in the dialog removes the predictable cases (missing channel / missing decay / `<channel><suffix>` collides with a real channel name) up-front by excluding them from the eligible channel list.
- R7 (origin R14). New `PhasorMasksDialog` (modal `QDialog` that combines configuration and inline run on the main thread, following the `FlimFretDialog` pattern); registered as a fourth button in the Workflows tab.
- R8 (origin R15). New CLI `percell4-batch-phasor-masks` registered in `[project.scripts]`. Shares the use case with the GUI dialog.

**Origin actors:** A1 (Researcher).
**Origin flows:** F1 (Configure and start a run, GUI), F2 (Headless re-run, CLI).

---

## Scope Boundaries

- Multi-cluster GMM (n>1), non-ellipse ROI shapes (rectangle, polygon, freehand), variable mask count (>2), per-dataset parameter overrides, per-channel parameter overrides — all out of scope (origin: Explicit non-goals).
- A shared dataset-picker widget between `PhasorMasksDialog` and `FlimFretDialog` — out of scope here; tracked as a follow-up refactor. The two dialogs are the only `QDialog`-driven workflow surfaces; `single_cell` and `dilute_phase` use different patterns (full `BaseWorkflowRunner` + QC queues, and a panel-based single-dataset flow respectively).
- Provenance attrs on written masks (e.g., recording `t_fit`, `t_mask_a`, `t_mask_b`, `suffix`, timestamp as HDF5 attrs on each `/masks/<name>` group via `store.write_mask(..., attrs=...)`). Would let downstream measurements recover which parameters produced a given mask. Out of scope for v1 because no downstream consumer currently reads such attrs; defer until one does.

### Deferred to Follow-Up Work

- An "inspection-only" end-of-run review window that surfaces each dataset's fitted ellipse + resulting masks for visual sanity-check. Origin notes this as worth adding if quality issues appear in practice; v1 ships without it because the n=1 ellipse fit is deterministic. *(Carried from origin.)*
- Per-channel parameter overrides (e.g., `--t-fit ch0=10,ch1=12`). Add only if researchers report needing different thresholds for different channels in the same run. *(Carried from origin.)*
- `--strict-channels` / non-strict mode on the CLI. The current up-front intersection check rejects a batch if any dataset lacks the requested channel. A "warn-but-proceed" variant (skip the missing dataset, continue with the rest) would mirror the per-item skip pattern already in use *inside* a run. Add when researchers report needing this for heterogeneous cohorts.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/domain/flim/phasor.py` — **canonical fitting/geometry/masking primitives**: `single_component_fit_phasor`, `gmm_eigenstructure`, `gmm_to_phasor_roi_geometry`, `phasor_roi_to_mask`. The new domain helper composes these.
- `src/percell4/application/use_cases/batch_compute_phasor.py` — **shape to mirror** for the new batch use case. Defines `BatchPhasorItemResult` + `BatchPhasorReport` (4-state taxonomy with `partial`), `progress_callback` contract, per-dataset try/except isolation, calibration gate. The new use case imports both dataclasses directly rather than redefining.
- `src/percell4/application/use_cases/compute_phasor.py` — canonical source for `derived-layer-staleness-invalidation` + `fresh-metadata-read-in-use-cases`. Read metadata fresh through the port, not from frozen handle snapshots.
- `src/percell4/store.py::write_mask` (lines 541–566) — the canonical mask write. Does **not** emit Session events; safe to call N times in a tight loop. Binarize at the boundary: `(arr > 0).astype(np.uint8)`.
- `src/percell4/workflows/channels.py::intersect_channels` — order-preserving channel intersection over `list[(dataset_name, channel_names)]`. Reusable for the dialog's channel picker once each dataset's channel list is gathered (with the with-decay filter applied per dataset before intersecting).
- `src/percell4/gui/flim_fret_dialog.py` — **the primary pattern to mirror** for U3. Self-driving modal dialog with a `QProgressDialog`-driven per-item loop on the main thread; `_on_start_clicked` (line 674) orchestrates the run; `QProgressDialog.wasCanceled()` is checked between items for true per-item cancel; results stashed in `self.last_run_folder` for the launcher to read after `exec_()` returns. Importantly: NO `BaseWorkflowRunner`, NO `WorkflowConfig`, NO `RunMetadata`.
- `src/percell4/gui/workflows/single_cell/config_dialog.py::WorkflowConfigDialog` — secondary reference for groupbox-builder patterns, `wrap_in_scroll` + `cap_to_screen`, and `_read_h5_channels` (lines 1037–1051) for bytes/str channel-name normalization. Mine for widgets; do not inherit run orchestration from this file.
- `src/percell4/interfaces/gui/main_window.py::_create_workflows_panel` (lines 329–378) — insertion point for the fourth button (between FLIM-FRET button and `layout.addStretch()`). `_on_open_flim_fret_workflow` (lines 380–407) is the slot pattern to clone: ~25 lines, just instantiates the dialog and reads results after `exec_()`. Gated by the launcher's `is_workflow_locked`.
- `src/percell4/interfaces/cli/batch_phasor.py` — argparse shape to mirror, though the new CLI uses the canonical `_batch_report` helpers instead of in-file copies.
- `src/percell4/interfaces/cli/_batch_report.py` — `resolve_paths`, `format_item_line(verb=...)`, `print_item_status(verb=...)`. Canonical going forward.
- `src/percell4/application/use_cases/batch_rename_resource.py` + `batch_delete_resource.py` — argparse + use case symmetry pattern from the most recent CLI work (commit b454b65d era).

### Institutional Learnings

- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` — **severity: critical.** Derive the intensity threshold from `decay.sum(axis=-1)` of the same `/decay/<channel>` used to compute (g, s). **Never** read `/intensity[ch_idx]` for this purpose.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` — five-layer staleness chain after in-session HDF5 writes. The batch writes ~200 masks while the GUI is alive; the active dataset's model + peer views must see fresh `/masks/*` after the run. Emit exactly one `Session.refresh_resource_lists(mask_names=...)` at end-of-run (only when the currently-loaded dataset was among the processed paths), not one per write.
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — Creator contract. In batch mode the use case performs **step 1 only** (`store.write_mask`); steps 2 (`viewer_win.add_mask`) and 4 (`session.set_active_mask`) are skipped (no auto-select in batch); step 3 (`session.refresh_resource_lists`) fires once at end-of-run from the runner.
- `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md` — masks whose names collide with viewer Image layers crash on reload. Validate `<channel><suffix>` never equals a channel name and `<suffix>` is non-empty; reject empty suffixes in dialog + CLI input validation.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — binarize masks at the write boundary `(arr > 0).astype(np.uint8)`; discovery scopes / processing consumes (per-dataset isolation); design dialogs around how users think (channels × datasets, not file-tree checkboxes).
- `docs/solutions/runtime-errors/multi-channel-dataset-load-numpy-array-truth-value-2026-05-22.md` — channel-name arrays from h5 must be normalized to `list[str]` before any set/intersection operation. The channel-intersection helper must not rely on numpy truthiness.
- `docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md` — **mandatory test**: at least one end-to-end test per `(GUI, CLI)` path asserting both pass identical kwargs to the use case. The view-bin bug shipped because two unit tests passed and no integration test exercised the wiring.
- `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md` — never pass a GUI-touching callback into a function running on a `Worker` QThread. Progress is a Qt signal only; the use case takes a thread-safe callback that re-emits via the signal.
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — every editable widget in the new dialog (threshold spinboxes, suffix line edits, channel checkboxes) must connect its `textChanged` / `valueChanged` / `itemChanged` signal to the Run-button enable/disable logic. Tests that programmatically `setText`/`setValue` won't catch unwired signals.

### External References

None — the work is entirely inside well-established local patterns.

---

## Key Technical Decisions

- **GUI uses the `FlimFretDialog` pattern, not `BaseWorkflowRunner`.** The runner contract is built around `WorkflowConfig` (frozen dataclass with mandatory `datasets`/`cellpose`/`thresholding_rounds`) and a phase generator. This workflow has none of those concerns — no QC, no thresholding rounds, no Cellpose. Adopting `FlimFretDialog`'s self-driving modal dialog pattern (run loop on the main thread with a `QProgressDialog`) means: no synthesis of a fake `WorkflowConfig`, no `DatasetFailure` enum extension to populate `RunMetadata.failures`, no override of `_finish`, no reliance on `_on_workflow_event`'s `runner._config.datasets` access. The dialog owns its own progress UI, cancel semantics (per-item via `QProgressDialog.wasCanceled()`), and end-of-run summary (`QMessageBox` built from the report). Trade-off: no inherited summary dialog and no run-folder provenance — both acceptable because outputs are masks inside the dataset's `.h5`, not files in a run folder, and the inherited summary's text ("excluded from measurement. Surviving datasets were exported.") doesn't apply to a mask-writing workflow.
- **Use case stays domain-pure, not Session-bound.** The existing `RunPhasorGMM` use case reads from `Session` (active_mask, filter_ids, active_segmentation). For an unattended batch over many datasets, building a fresh `Session` per dataset adds ceremony without value. The new batch use case calls the domain primitives in `domain/flim/phasor.py` directly and operates on a `DatasetStore`. Rationale: same shape as `batch_compute_phasor`, which is the closest sibling and the most recent canonical pattern.
- **Reuse `BatchPhasorItemResult` / `BatchPhasorReport` from `batch_compute_phasor`.** Verified field shape: `{h5_path, status, processed, skipped, errors, error}` — `skipped` and `errors` are both `dict[str, str]` keyed by channel name. The 4-state taxonomy (`succeeded` / `partial` / `skipped_no_changes` / `failed`) fits because a (dataset, channel) item writes two masks and either can fail independently → `partial`. Importing rather than redefining keeps the CLI's progress UI consistent across phasor-related tools. The dialog tracks overwrites in a **local list** during the loop (not on the dataclass) and surfaces them in the summary `QMessageBox` — this avoids extending the shared dataclass for a workflow-specific concern.
- **Per-dataset ensure-then-fit, single pass.** Inside the dialog's loop (and inside the use case for the CLI path): for each dataset, first ensure phasor exists for the selected channels (call `compute_phasor` / `apply_wavelet` if absent — same primitives that `batch_compute_phasor` uses, but per-dataset inside the loop), then fit + apply masks for those channels. Resolves the "Phase A and Phase B surface the same dataset as two unrelated failures" problem; the dataset has one outcome line in the report regardless of where it failed.
- **Phasor map source: prefer `g_filtered` / `s_filtered` when present**, fall back to `g` / `s`. This matches `RunPhasorGMM.execute(..., use_filtered_gs=True)` and is the higher-SNR path the FLIM panel defaults to. When phasor must be computed inside the loop, the wavelet step is included so filtered maps exist for the fit. When phasor was previously cached without wavelet maps, the use case falls back to raw `g` / `s` with no warning.
- **Wavelet `filter_level=9` is a single named constant** (`DEFAULT_WAVELET_FILTER_LEVEL` in the use case module) referenced from both the dialog and the CLI. Matches the current FLIM panel default. If the FLIM panel default changes in the future, the workflow's constant is the single source of update.
- **Degenerate fit ⇒ explicit channel-level failure**, not silent success. If `single_component_fit_phasor` raises (empty subset above `t_fit`), the channel is classified as a `failed` entry in `errors`. If the fit returns but produces a degenerate ellipse (`lambda_minor == 0` ⇒ zero-area mask), the use case detects this **after computing each mask** and downgrades the channel to `errors[ch] = "degenerate fit (ellipse has zero area)"` rather than calling `store.write_mask` with an all-False array. This protects against the silent "all-zero masks marked succeeded" path.
- **Mask-name collision check** runs in both surfaces. Validation up-front: for each (dataset, channel) pair, reject if `f"{channel}{suffix_a}"` or `f"{channel}{suffix_b}"` equals any name in that dataset's `channel_names`. The dialog excludes those channels from the eligible list with an explanation; the CLI exits 2 with a message naming the offending dataset/channel/suffix combination. Closes the add-mask name-collision crash documented in `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`.
- **End-of-run viewer refresh is conditional, one-shot.** The dialog emits one `session.refresh_resource_lists(mask_names=store.list_groups("masks"))` only if the active dataset (resolved via `Path.resolve()`) is among the processed paths (also resolved). No emission if the user processed datasets that are not currently open.
- **CLI is a separate file**, not a `--gmm-masks` flag bolted onto `batch_phasor`. The argument shape diverges sharply (`--channels`, three thresholds, two suffixes vs. `batch_phasor`'s single `--filter-level`) and would muddy `--help`. Separate file, separate entry point.
- **`percell4-batch-phasor` retroactively added to `[project.scripts]`.** Research surfaced that the existing CLI is only invocable via `python -m percell4.interfaces.cli.batch_phasor` — not as a top-level command. Folding its registration into U5 alongside the new CLI's registration resolves the asymmetry without a separate PR.

---

## Open Questions

### Resolved During Planning

- **Where does GMM ellipse-fitting code live, and is it reusable headlessly?** Resolved: `src/percell4/domain/flim/phasor.py` has pure-domain primitives (`single_component_fit_phasor`, `gmm_to_phasor_roi_geometry`, `phasor_roi_to_mask`) callable with no Session or Qt dependencies. The new use case composes them directly. The exact signature of `gmm_to_phasor_roi_geometry` requires `shape="ellipse"` (positional/keyword) and does NOT accept an `anchor` parameter — see U1.
- **Does the mask-write path emit `state_changed` events that thrash the UI during a long batch?** Resolved: `store.write_mask` is a pure HDF5 wrapper with no Session reference. No events fire per write. The only Session contact happens once at end-of-run, via `session.refresh_resource_lists`.
- **CLI naming: new tool vs. `--gmm-masks` flag on `batch_phasor`?** Resolved: new tool `percell4-batch-phasor-masks`. Rationale in Key Technical Decisions.
- **Can `BaseWorkflowRunner` host this workflow?** Resolved during doc-review: **No.** `BaseWorkflowRunner.start(config: WorkflowConfig, ...)` is bound to `WorkflowConfig`'s fields (`datasets`, `cellpose`, `thresholding_rounds`) and invariants ("at least one thresholding round"). The inherited summary dialog reads `runner._config.datasets`. The plan adopts the `FlimFretDialog` pattern instead — same as the existing FLIM-FRET workflow, no runner, no `WorkflowConfig`.
- **How is the "overwrite occurred" signal carried to the end-of-run report?** Resolved during doc-review: the dialog tracks overwrites in a **local set** (populated by probing `store.list_groups("masks")` before the run, intersected against `<channel><suffix>` per dataset). The reused `BatchPhasorItemResult` dataclass does NOT need an `overwrites` field. The CLI doesn't expose overwrite reporting (silent overwrite per origin R10; users wanting safety read the CLI's per-line output and compare).
- **What happens when a single-pixel or rank-deficient fit produces a zero-area ellipse?** Resolved during doc-review: U1 has an explicit degeneracy guard (`if radii[0] <= 0 or radii[1] <= 0: raise ValueError`). U2 catches and routes to `errors`. The "silent all-zero masks marked succeeded" path is closed.

### Deferred to Implementation

- Whether the use case captures the GMM fit's `sampled_pixels` count for diagnostic value. Decide after implementing U2 — if the report already feels informative, skip.
- The exact widget for the channel picker — `QListWidget` with checkboxes vs. `QComboBox` with checkable items vs. two columns (available / selected) with arrows. Pick during U3 implementation based on what looks cleanest at typical channel counts (1–5).
- Whether `DEFAULT_WAVELET_FILTER_LEVEL = 9` should be imported from the FLIM panel module (so they cannot drift) or stand as a workflow-local constant with a comment "matches FLIM panel default as of YYYY-MM". Decide during U2 — import is cleaner if the FLIM panel exposes the constant; otherwise comment is fine.

---

## Output Structure

```
src/percell4/
├── domain/
│   └── segmentation/
│       └── phasor_masks.py                          # U1: pure helper (new)
├── application/
│   └── use_cases/
│       └── batch_fit_phasor_masks.py                # U2: orchestrator (new)
├── gui/
│   └── phasor_masks_dialog.py                       # U3: self-driving dialog (new)
├── interfaces/
│   ├── gui/
│   │   └── main_window.py                           # U3: launcher wiring (modify)
│   └── cli/
│       └── batch_phasor_masks.py                    # U5: CLI (new)
tests/
├── test_domain/
│   └── test_phasor_masks.py                         # U1
├── test_application/
│   └── test_batch_fit_phasor_masks.py               # U2
├── test_gui/
│   └── test_phasor_masks_dialog.py                  # U3
└── test_cli_batch_phasor_masks.py                   # U5
pyproject.toml                                       # U5: scripts (modify)
```

Note: U4 is intentionally absent (gap from the doc-review revision; per the U-ID stability rule, IDs are not renumbered when a unit is consolidated). The merged dialog-driven design absorbs what was previously the U4 runner unit into U3.

---

## Implementation Units

- U1. **Domain helper: ellipse fit + dual-threshold masks**

**Goal:** A single pure function that takes phasor maps (`g`, `s`), an intensity map, three thresholds, and returns the fitted ellipse geometry plus two boolean masks. No I/O, no Session, no Qt. The unit no other piece can be built without.

**Requirements:** R3

**Dependencies:** None.

**Files:**
- Create: `src/percell4/domain/segmentation/phasor_masks.py`
- Test: `tests/test_domain/test_phasor_masks.py`

**Approach:**
- Compose existing primitives from `src/percell4/domain/flim/phasor.py`:
  1. Build the GMM-fit subset: pixels where `intensity_map ≥ t_fit` AND `g` / `s` are finite.
  2. Call `single_component_fit_phasor(g_subset, s_subset, intensity_subset)` → `GMMFitResult` (mean, cov, weighted-cov). Raises `ValueError("Cannot fit single component on empty input")` if the subset is empty — let it propagate; U2 catches.
  3. Call `gmm_eigenstructure(cov)` → `(lambda_major, lambda_minor, principal_angle_rad)`.
  4. Call `gmm_to_phasor_roi_geometry(mean=..., lambda_major=..., lambda_minor=..., principal_angle_rad=..., stretch_parallel=2.0, stretch_perpendicular=2.0, shift_parallel=0.0, shift_perpendicular=0.0, shape="ellipse")` → `PhasorROIGeometry(center, radii, angle_deg, ...)`. Note: `shape` is a required positional/keyword argument (see canonical call site at `src/percell4/application/use_cases/run_phasor_gmm.py:260–270`); there is no `anchor` parameter — the GMM mean is the implicit anchor.
  5. **Degeneracy guard:** if `radii[0] <= 0 or radii[1] <= 0` (a single pixel or rank-deficient cov collapses one eigenvalue to zero), raise `ValueError("degenerate fit (ellipse has zero area)")`. This is the explicit downgrade path that prevents the silent "two all-False masks marked succeeded" bug — without it, `phasor_roi_to_mask` returns all-False (per its `if rx <= 0 or ry <= 0: return zeros` branch) and the caller has no signal that anything went wrong.
  6. Call `phasor_roi_to_mask(g_map, s_map, center, radii, angle_rad=np.radians(angle_deg))` → `bool` array shaped like `g_map`. This is the "pixel lies inside the ellipse on the phasor plot" mask, applied across the spatial grid.
  7. AND with `intensity_map ≥ t_mask_a` → mask A; AND with `intensity_map ≥ t_mask_b` → mask B. Cast both to `uint8` via `(m & i).astype(np.uint8)`.
- Return a small dataclass: `PhasorEllipseMasksResult(geometry, mask_a, mask_b, sampled_pixels)`.
- The function is intentionally I/O-free. The caller is responsible for deriving `intensity_map = decay.sum(axis=-1)` (the cross-layer-alignment rule lives at the call site, not buried here).

**Execution note:** Implement test-first. Use small synthetic phasor distributions (a Gaussian blob with known mean/cov) where the expected ellipse + mask are computable.

**Patterns to follow:**
- `src/percell4/application/use_cases/run_phasor_gmm.py` (composition pattern for the primitives)
- `src/percell4/domain/flim/phasor.py` (call signatures and return types of the primitives)

**Test scenarios:**
- *Happy path.* Synthetic G/S with a known 2D Gaussian blob and a flat intensity map → fit recovers center within tolerance, mask_a coverage > mask_b coverage when `t_mask_a < t_mask_b`, both masks are `uint8` and ≥ 1 wherever pixels lie inside the ellipse on the phasor plot.
- *Happy path.* Two synthetic populations at distinct phasor coordinates; only the high-intensity one survives the `t_fit` cut → ellipse centers on the high-intensity population, low-intensity population is largely outside the mask.
- *Edge case.* `t_mask_a == t_mask_b` → mask_a and mask_b are identical.
- *Edge case.* `t_mask_a == 0` → mask_a coverage equals the ellipse-only mask (no intensity filtering).
- *Edge case.* `intensity_map` is all zeros below `t_fit` → fit subset is empty; function raises `ValueError("Cannot fit single component on empty input")` (from `single_component_fit_phasor`).
- *Edge case.* `g_map` / `s_map` contain NaN pixels → those pixels are excluded from both the fit and the resulting masks (mask is `False` there).
- *Error path.* Single non-NaN pixel above `t_fit` → `single_component_fit_phasor` returns `cov=zeros`; `gmm_eigenstructure` returns `lambda_minor=0`; `gmm_to_phasor_roi_geometry` returns `radii=(>0, 0)` or `(0, 0)`; **U1's degeneracy guard raises `ValueError("degenerate fit (ellipse has zero area)")`** before reaching `phasor_roi_to_mask`. Crucial: without this guard, the all-False mask returned by the primitive looks like a successful "no pixels match" outcome to the caller, hiding the actual failure.
- *Error path.* Two pixels collinear in (G, S) above `t_fit` → cov is rank-deficient (`lambda_minor=0`) → degeneracy guard raises. Same path as single-pixel case.
- *Integration.* Output `mask_a` and `mask_b` are `dtype=np.uint8`, shape matches `g_map`, max value is 1 — feeds `store.write_mask` cleanly.

**Verification:**
- All test scenarios pass.
- No import from `qtpy`, `napari`, `h5py`, or `percell4.application.session` (pure-domain isolation).

---

- U2. **Application use case: `batch_fit_phasor_masks`**

**Goal:** Per-(dataset, channel) orchestrator. Reads `/decay/<ch>` + `/phasor/<ch>/g_filtered` (fallback `/phasor/<ch>/g`) + `/phasor/<ch>/s_filtered` (fallback `/phasor/<ch>/s`), derives intensity, calls U1, writes two masks per channel. Returns a `BatchPhasorReport`.

**Requirements:** R3, R5, R6

**Dependencies:** U1.

**Files:**
- Create: `src/percell4/application/use_cases/batch_fit_phasor_masks.py`
- Test: `tests/test_application/test_batch_fit_phasor_masks.py`

**Approach:**
- Public entry point:
  ```
  batch_fit_phasor_masks(
      h5_paths: Iterable[Path],
      *,
      channels: Sequence[str],
      t_fit: float,
      t_mask_a: float,
      t_mask_b: float,
      suffix_a: str,
      suffix_b: str,
      ensure_phasor: bool = True,
      progress_callback: Callable[[BatchPhasorItemResult], None] | None = None,
      cancel_check: Callable[[], bool] | None = None,
  ) -> BatchPhasorReport
  ```
- Import `BatchPhasorItemResult`, `BatchPhasorReport` from `batch_compute_phasor` (do not redefine). Verified shape: `{h5_path, status, processed, skipped, errors, error}` where `skipped` and `errors` are `dict[str, str]` keyed by channel name.
- `ensure_phasor=True` (default): if a requested channel lacks `/phasor/<ch>/g`, call `ComputePhasor` + `ApplyWavelet` (same primitives `batch_compute_phasor` uses) inside the loop **with the channel's calibration metadata** before fitting. Set `ensure_phasor=False` in tests that want to verify the no-phasor skip path explicitly.
- `cancel_check`: optional callable returning `True` if the dialog's `QProgressDialog.wasCanceled()` is true. Checked between datasets (not mid-dataset) — cancel mid-dataset would leave one dataset half-processed.
- Validate inputs up-front: empty `channels` → `ValueError`; `suffix_a == ""` or `suffix_b == ""` → `ValueError("suffix must be non-empty")`; `suffix_a == suffix_b` → `ValueError("suffixes must differ")`.
- Per dataset (try/except isolation, mirrors `batch_compute_phasor._process_one_dataset`):
  1. If `cancel_check and cancel_check()` → break loop, return what's been accumulated so far.
  2. Open `DatasetStore(path)`. On open failure → `BatchPhasorItemResult(status="failed", error=str(exc))`.
  3. **Collision check:** for each requested channel, reject if `f"{channel}{suffix_a}"` or `f"{channel}{suffix_b}"` is in this dataset's `channel_names`. Add to `errors[ch] = "mask name collides with channel '<offending name>'"` and skip this channel. (Defense in depth — the dialog filters these out up-front; this protects the CLI and any future caller.)
  4. For each surviving channel: check `metadata.channel_names` includes it and `f"decay/{channel}"` exists. Missing → contribute to `skipped` dict on the item with a reason. Channel-level skips don't fail the dataset.
  5. Read decay (`store.read_decay(channel)` or equivalent), compute `intensity_map = decay.sum(axis=-1)`.
  6. Read phasor: prefer `f"phasor/{channel}/g_filtered"` / `s_filtered`, fall back to `g` / `s`. If neither exists AND `ensure_phasor=True` → compute now (calling the same use cases `batch_compute_phasor` uses). If neither exists AND `ensure_phasor=False` → channel-level skip (`"phasor not computed"`).
  7. Call U1 → `PhasorEllipseMasksResult`. On `ValueError` from U1 (empty subset or degenerate-fit guard) → `errors[ch] = str(exc)`; do NOT call `store.write_mask`. This is the explicit downgrade path that prevents all-zero masks from being marked as processed.
  8. Write masks: `store.write_mask(f"{channel}{suffix_a}", result.mask_a)` and `store.write_mask(f"{channel}{suffix_b}", result.mask_b)`. Binarize at the boundary via `(arr > 0).astype(np.uint8)` per the batch-compress-development-lessons learning. Track which channels had **both** writes succeed (`processed`) vs. only-one or none. On `store.write_mask` raising → `errors[ch] = f"write failed: {exc}"`.
  9. Classify dataset status:
     - all requested channels processed both masks → `succeeded`
     - some channels processed, some skipped/errored → `partial`
     - all requested channels skipped, no errors → `skipped_no_changes`
     - dataset-level open failure or unhandled exception → `failed`
- Fire `progress_callback(item)` once per dataset after classification.

**Execution note:** Implement test-first against real `.h5` fixtures (not mocks). The `batch_compute_phasor` / `batch_rename_resource` test suites are the model.

**Technical design:** *(optional, directional)*

```
For each h5_path in h5_paths:
    item = open + classify dataset:
        - 'failed' if open raises
        - else iterate channels:
            for ch in channels:
                if ch not in channel_names or no /decay/ch:
                    skipped[ch] = "channel not present"
                    continue
                if no /phasor/ch/g(_filtered):
                    skipped[ch] = "phasor not computed"
                    continue
                intensity = decay.sum(axis=-1)
                try:
                    result = fit_phasor_ellipse_and_apply_masks(
                        g, s, intensity,
                        t_fit=t_fit, t_mask_a=t_mask_a, t_mask_b=t_mask_b
                    )
                except ValueError as e:
                    errors[ch] = str(e); continue
                store.write_mask(f"{ch}{suffix_a}", result.mask_a)
                store.write_mask(f"{ch}{suffix_b}", result.mask_b)
                processed.append(ch)
            classify(processed, skipped, errors) -> status
    progress_callback(item)
return BatchPhasorReport(items=tuple(items))
```

**Patterns to follow:**
- `src/percell4/application/use_cases/batch_compute_phasor.py` (per-dataset isolation, progress_callback, status classification, error string format)
- `src/percell4/application/use_cases/batch_rename_resource.py` (input validation pattern)

**Test scenarios:**
- *Happy path.* Two `.h5` fixtures with the same channel + phasor + decay; one channel; `t_fit=10`, `t_mask_a=0`, `t_mask_b=5` → both items `succeeded`, four masks on disk (2 per dataset), names match `<ch><suffix>`.
- *Happy path: prefers filtered.* `.h5` has both `g` and `g_filtered`; result uses `g_filtered` (verifiable by writing distinct values into each and checking the fit center).
- *Happy path: falls back to unfiltered.* `.h5` has only `g` / `s`; the use case still produces masks.
- *Edge case.* Two channels requested, only one present in the dataset → `partial`; `processed=("ch0",)`, `skipped={"ch1": "channel not present"}`.
- *Edge case.* Channel exists in metadata but `/decay/<ch>` is missing → channel-level skip with `"channel not present"` reason.
- *Edge case.* `/phasor/<ch>/g` exists, `g_filtered` does not → fall-back path used; result has masks.
- *Edge case.* All requested channels absent → item status `skipped_no_changes`.
- *Error path.* Missing `.h5` → item `failed` with error string containing `"open"`.
- *Error path.* Existing mask `<ch><suffix_a>` is overwritten silently (verify by writing a sentinel mask first and checking it changed after the run).
- *Error path.* Degenerate phasor (single pixel above `t_fit`) → U1 raises; channel goes to `errors`, no `store.write_mask` call. Other channels in the same dataset still succeed; item status `partial`. **Verify on disk** that no mask was written for the degenerate channel.
- *Error path.* `<channel><suffix_a>` collides with an existing channel name in the dataset → channel goes to `errors` with the collision message; no masks written for that channel; other channels proceed.
- *Error path.* `store.write_mask` raises during the second mask write → first mask is already on disk, second isn't; `errors[ch] = "write failed: ..."`, `processed` does NOT include this channel; item status `partial`.
- *Cancel.* `cancel_check` returns `True` after the second dataset of three → loop breaks; report contains items for datasets 1 and 2 only.
- *Input validation.* `suffix_a == ""`, `suffix_b == ""`, `suffix_a == suffix_b`, `channels=()` → `ValueError` raised before any I/O.
- *Integration.* Progress callback fires exactly once per dataset, in input order; `len(report.items) <= len(h5_paths)` (equal when not cancelled).
- *Integration: ensure_phasor.* `.h5` fixture has decay but no phasor maps; `ensure_phasor=True` (default) → phasor computed on the fly, masks produced; `ensure_phasor=False` → channel skipped with `"phasor not computed"`.

**Verification:**
- All test scenarios pass.
- No import from `qtpy`, `napari`, or any GUI module.
- The function can be called from the CLI test with no Qt event loop.

---

- U3. **`PhasorMasksDialog` — config + inline run + launcher wiring**

**Goal:** A single self-driving modal `QDialog` (mirroring `FlimFretDialog`) that combines configuration capture and the run loop on the main thread. Plus the launcher wiring: a fourth button in the Workflows tab and a `_on_open_phasor_masks_workflow` slot in `main_window.py` (~25 lines, mirroring `_on_open_flim_fret_workflow`).

**Requirements:** R1, R2, R3, R5, R6, R7

**Dependencies:** U2 (calls `batch_fit_phasor_masks`).

**Files:**
- Create: `src/percell4/gui/phasor_masks_dialog.py`
- Modify: `src/percell4/interfaces/gui/main_window.py`
- Test: `tests/test_gui/test_phasor_masks_dialog.py`

**Approach:**

*Dialog layout* (mirrors `WorkflowConfigDialog` for sections, `FlimFretDialog` for run mechanics):
- Outer scroll area via `wrap_in_scroll`, `cap_to_screen`; Start/Cancel button row outside scroll. Groupbox builders for each section.
- Sections:
  1. **Datasets** — list view + buttons "Add .h5 files…" and "Add folder of .h5…". Inline `_PendingDataset` dataclass (single `.h5` path; tiff sources NOT supported). Remove-selected button.
  2. **Channels** — multi-select `QListWidget` with checkboxes. Populated by `_refresh_channel_picker()` whenever the dataset queue changes:
     - For each `.h5`, gather `set(channel_names) ∩ set(store.list_groups("decay"))` (bytes → str normalization per `multi-channel-dataset-load` learning).
     - Intersect across datasets using `workflows.channels.intersect_channels`.
     - **Pre-filter for collision**: for each channel in the intersection, check whether `f"{ch}{suffix_a}"` or `f"{ch}{suffix_b}"` equals any channel name in any selected dataset. If so, exclude the channel from the picker and surface in the explanatory label below.
     - If the result is empty, display: "No channels are present (with decay) in every selected dataset." + reason rows for any excluded channels (channel-name collision, missing decay).
  3. **Phasor mask parameters** — three `QDoubleSpinBox` (range 0–65535, step 1.0):
     - **"Fit threshold (intensity)"** (default 10) — tooltip: "Only pixels with intensity ≥ this value are used to fit the ellipse. Higher = cleaner ellipse on noisier data."
     - **"Mask A threshold (permissive)"** (default 0) — tooltip: "Intensity threshold for the first output mask. Lower = larger coverage."
     - **"Mask B threshold (conservative)"** (default 5) — tooltip: "Intensity threshold for the second output mask. Higher = tighter, higher-confidence coverage."
     - Two `QLineEdit`: **"Mask A suffix"** (default `_phasor_1`) and **"Mask B suffix"** (default `_phasor_5`). Tooltip on each: "Mask written as `<channel>{suffix}` in the dataset's `/masks/` group."
- Input validation in `_update_start_enabled()`: Start disabled unless (a) ≥1 dataset, (b) ≥1 channel selected, (c) both suffixes non-empty, (d) suffixes differ.
- Wire **every** editable widget's `valueChanged` / `textChanged` / `itemChanged` to `_update_start_enabled()` AND `_refresh_channel_picker()` (per the `qt-wire-user-edit-signals` learning). Suffix edits re-run the collision pre-filter.
- QSettings persistence for last-used parameters: `"phasor_masks/t_fit"`, `"phasor_masks/t_mask_a"`, `"phasor_masks/t_mask_b"`, `"phasor_masks/suffix_a"`, `"phasor_masks/suffix_b"`. Default to 10/0/5/`_phasor_1`/`_phasor_5` when keys absent.

*Run mechanics* (mirrors `FlimFretDialog._on_start_clicked` lines 674–800):
- Disable form controls, capture frozen parameters.
- Create `QProgressDialog` with `Qt.WindowModal`, label "Phasor-masks workflow", max = number of datasets, Cancel button enabled.
- Define an inline `progress_cb(item: BatchPhasorItemResult)` closure that:
  1. Increments the QProgressDialog value.
  2. Updates label text: `f"Dataset {i+1}/{N}: {item.h5_path.name} — {item.status}"`.
  3. Calls `QApplication.processEvents()` to keep UI responsive and pick up cancel clicks.
  4. Tracks overwrites locally: for each channel in `item.processed`, check whether either mask name pre-existed (probe via a sentinel: maintain a `seen_existing: set[tuple[Path, str]]` populated up-front by enumerating `store.list_groups("masks")` per dataset before the run).
- Define an inline `cancel_check()` closure: `lambda: progress.wasCanceled()`.
- Call `batch_fit_phasor_masks(dataset_paths, channels=..., t_fit=..., t_mask_a=..., t_mask_b=..., suffix_a=..., suffix_b=..., ensure_phasor=True, progress_callback=progress_cb, cancel_check=cancel_check)`. The use case handles per-dataset isolation.
- After the call returns:
  - Close the `QProgressDialog`.
  - If the active dataset (from `self._host.get_session().dataset.path.resolve()` if available) is among `[p.resolve() for p in dataset_paths]`, emit a single `session.refresh_resource_lists(mask_names=store.list_groups("masks"))` for that store. Otherwise no emission.
  - Build a summary `QMessageBox`:
    - Title: "Phasor-masks workflow"
    - Icon: `Information` if all items `succeeded`, `Warning` if any `partial`/`failed`.
    - Text: `f"Processed {N_total} datasets: {N_succeeded} succeeded, {N_partial} partial, {N_failed} failed."`
    - Detailed text (collapsible): per-dataset breakdown + the locally-tracked overwrite list ("Overwrote N existing masks across M datasets").
  - Call `self.accept()` on completion (whether or not there were failures — failures are reported, not abortive).

*Launcher wiring* in `main_window.py`:
- Insert `QPushButton("Automated phasor-masks workflow")` in `_create_workflows_panel` between the FLIM-FRET button (~line 375) and `layout.addStretch()` (~line 377).
- New slot `_on_open_phasor_masks_workflow` (~25 lines), structure-identical to `_on_open_flim_fret_workflow` (lines 380–407): re-entrance guard via `self.is_workflow_locked`, instantiate `PhasorMasksDialog(parent=self)`, `dialog.exec_()`, optional status-bar message on completion, `dialog.deleteLater()` in `finally`.

**Execution note:** Implement test-first using `pytest-qt`. Widget-level tests drive real user-edit signals (`qtbot.keyClicks`, `qtbot.mouseClick`), not programmatic `setValue` — otherwise unwired signals slip through. Run-loop tests monkeypatch `batch_fit_phasor_masks` to a deterministic stub that fires the progress callback synchronously.

**Patterns to follow:**
- `src/percell4/gui/flim_fret_dialog.py` — overall pattern: modal dialog with inline run, `QProgressDialog` per-item loop, `wasCanceled()` check, `QMessageBox` summary.
- `src/percell4/gui/workflows/single_cell/config_dialog.py` — section builders, `wrap_in_scroll`, `cap_to_screen`, `_read_h5_channels` (bytes/str normalization).
- `src/percell4/interfaces/gui/main_window.py::_on_open_flim_fret_workflow` (lines 380–407) — launcher slot shape.
- `src/percell4/workflows/channels.py::intersect_channels` — channel intersection.

**Test scenarios:**

*Configuration & validation:*
- *Happy path.* Add two `.h5` fixtures sharing channels `[mNG, Halo]`; channel picker shows both; defaults are 10/0/5/`_phasor_1`/`_phasor_5`.
- *Edge case: intersection empty.* Two datasets with disjoint channel sets → picker is empty, explanatory label visible, Start disabled.
- *Edge case: channel-name collision.* Dataset has channels `[mNG, mNG_phasor_1]`; user keeps default suffix `_phasor_1` → `mNG` is excluded from the picker with reason text "would overwrite channel 'mNG_phasor_1'". Changing the suffix to `_p1` re-enables `mNG`.
- *Edge case: dataset has channel but no decay.* Dataset 1 has `mNG` with decay, Dataset 2 has `mNG` in `channel_names` but no `/decay/mNG` group → `mNG` excluded from the picker.
- *Edge case: bytes channel names.* `metadata.channel_names` contains `np.bytes_("mNG")` entries → display as `"mNG"`, intersect correctly.
- *Error path: empty suffix.* User clears Suffix A line edit → Start disables.
- *Error path: identical suffixes.* User enters `_phasor_1` in both fields → Start disables.
- *Integration: signals wired.* `qtbot.keyClicks` on the Suffix A line edit (not `setText`) triggers both `_update_start_enabled` and `_refresh_channel_picker` (the latter because suffix changes affect the collision pre-filter).

*Run mechanics:*
- *Happy path.* Monkeypatch `batch_fit_phasor_masks` to return one `succeeded` item for each input path, firing the progress callback as it goes. Click Start; `QProgressDialog` opens and closes; `QMessageBox` summary shows "N datasets succeeded".
- *Partial.* Stub returns one `partial` item; summary `QMessageBox` uses `Warning` icon; detailed text contains the channel-level error.
- *Cancel.* Stub fires progress for the first dataset; test calls `progress.cancel()`; the stub's next iteration sees `cancel_check()=True` and breaks; summary shows "Cancelled after N datasets".
- *End-of-run refresh: dataset open.* Active dataset path matches one of the processed paths (using `Path.resolve()` for normalization); `session.refresh_resource_lists` is called exactly once.
- *End-of-run refresh: dataset not open.* Active dataset is unrelated; `refresh_resource_lists` is NOT called.
- *End-of-run refresh: no active dataset.* No session dataset; `refresh_resource_lists` is NOT called.
- *Overwrite tracking.* Pre-populate a dataset with `/masks/mNG_phasor_1`; run with defaults; summary detailed text says "Overwrote 1 existing mask in 1 dataset".

*Launcher slot:*
- *Integration: launcher slot.* `pytest-qt` test clicks the new button → `PhasorMasksDialog` instance is created. Inspect `self.is_workflow_locked` while open.
- *Integration: re-entrance guard.* While the dialog is open, clicking the button again is a no-op (or shows a status-bar message — match `_on_open_flim_fret_workflow`'s behavior; per the source it sets a status-bar message).

**Verification:**
- All scenarios pass.
- The new button appears in the Workflows tab when the launcher opens.
- A real run against two-dataset fixtures produces four masks on disk + the summary `QMessageBox`.

---

- U5. **CLI: `percell4-batch-phasor-masks` + pyproject scripts registration**

**Goal:** A new CLI mirroring `batch_phasor.py`'s shape but using the canonical `_batch_report` helpers. Argparse with `paths` (positional, expands directories non-recursively), `--channels` (one-or-more, required), `--t-fit` (default 10.0), `--t-mask-a` (default 0.0), `--t-mask-b` (default 5.0), `--suffix-a` (default `_phasor_1`), `--suffix-b` (default `_phasor_5`), `--dry-run`, `--quiet`, `--verbose`. Calls `batch_fit_phasor_masks` (which handles phasor compute internally via `ensure_phasor=True`). Registers both this CLI and the previously-missing `percell4-batch-phasor` in `[project.scripts]`.

**Requirements:** R1, R2, R3, R5, R6, R8

**Dependencies:** U2.

**Files:**
- Create: `src/percell4/interfaces/cli/batch_phasor_masks.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli_batch_phasor_masks.py`

**Approach:**
- `main(argv: list[str] | None = None) -> int` entry point.
- argparse: `prog="percell4-batch-phasor-masks"`. Required: positional `paths`, `--channels` (nargs='+'). Optional: `--t-fit` (default 10.0), `--t-mask-a` (default 0.0), `--t-mask-b` (default 5.0), `--suffix-a` (default `_phasor_1`), `--suffix-b` (default `_phasor_5`), `--dry-run`, `--quiet`, `--verbose`.
- Resolve `paths` via `_batch_report.resolve_paths` (non-recursive `.h5` expansion).
- Up-front validation (exit code 2 on any failure, with a clear error message):
  - Channel intersection: for each `.h5`, intersect `metadata.channel_names` with `/decay/*` groups; intersect across datasets; reject if any requested channel is missing from any dataset.
  - Suffix sanity: reject empty suffixes / identical suffixes.
  - Collision check: for each (dataset, channel) pair, reject if `f"{channel}{suffix_a}"` or `f"{channel}{suffix_b}"` matches any channel name in that dataset. Error names the offending (dataset, channel, suffix) triple.
- Dry-run: print the planned operations (dataset count, channels, per-dataset action) and exit 0 without calling the use case.
- Real run: invoke `batch_fit_phasor_masks(paths, channels=..., t_fit=..., t_mask_a=..., t_mask_b=..., suffix_a=..., suffix_b=..., ensure_phasor=True, progress_callback=printer)`. The use case computes phasor on the fly for any dataset missing it; no separate Phase A is needed.
- Printer uses `_batch_report.print_item_status(item, verb="processed")` (or "fit" — pick once for consistency). Exit code 0 if any item processed at least one channel, else 1.
- `pyproject.toml` `[project.scripts]`:
  ```
  percell4-batch-phasor       = "percell4.interfaces.cli.batch_phasor:main"
  percell4-batch-phasor-masks = "percell4.interfaces.cli.batch_phasor_masks:main"
  ```
- `percell4-batch-phasor`'s existing `main` should already conform — confirm during U5 implementation; if not, surface it as in-unit work.

**Execution note:** Implement test-first. Test pattern from `tests/test_cli_batch_rename_resource.py` (monkeypatch the use case, assert argparse propagation; plus one end-to-end test against real `.h5` fixtures).

**Patterns to follow:**
- `src/percell4/interfaces/cli/batch_phasor.py` (overall argparse + main shape)
- `src/percell4/interfaces/cli/batch_rename_resource.py` (use of `_batch_report` helpers)
- `tests/test_cli_batch_rename_resource.py` (test layout: stub_use_case fixture + end-to-end test)

**Test scenarios:**
- *Argparse plumbing.* CLI passes `paths`, `channels`, `t_fit`, `t_mask_a`, `t_mask_b`, `suffix_a`, `suffix_b` through to the stubbed use case unchanged.
- *Defaults.* Omitting threshold/suffix flags yields `t_fit=10.0`, `t_mask_a=0.0`, `t_mask_b=5.0`, `suffix_a="_phasor_1"`, `suffix_b="_phasor_5"`.
- *Channel-intersection rejection.* Two `.h5` fixtures, one missing the requested channel → CLI exits 2 with an error message naming the missing channel and the dataset path.
- *Empty suffix / identical suffix rejection.* Each → exit 2 with a clear message.
- *Directory glob.* `paths` is a directory containing two `.h5` files → resolved to both files; expansion does not recurse.
- *Dry-run.* `--dry-run` prints planned operations and does **not** invoke either use case (assert via monkeypatch).
- *Exit code.* All items succeed → exit 0. All items skipped (no channels processed) → exit 1.
- *Output text.* `--quiet` suppresses per-item lines; `--verbose` adds detail; default mode prints one line per dataset using the `processed` verb.
- *Help.* `--help` lists `--channels`, all three thresholds, both suffixes, and `--dry-run`.
- *Integration (`(CLI, GUI)` parity test).* Run the CLI against a fixture with one dataset, one channel; separately, run the GUI runner against the same fixture with the same config. Assert both produce identical mask arrays on disk byte-for-byte. (Required by the `phasor-view-bin-not-forwarded-from-gui-callers` learning.)
- *End-to-end real h5.* One `.h5` fixture with a known channel + decay + phasor; CLI runs to completion; assert masks exist with correct names and shapes.

**Verification:**
- All scenarios pass.
- `percell4-batch-phasor-masks --help` runs cleanly after `pip install -e .`.
- `percell4-batch-phasor --help` also runs (registration verification).

---

## System-Wide Impact

- **Interaction graph:** The dialog runs on the main thread; `batch_fit_phasor_masks` is invoked synchronously and drives the progress callback inline. `QApplication.processEvents()` between items keeps the UI responsive and allows cancel clicks to register. No new Qt signals are introduced. `BaseWorkflowRunner` and `WorkflowEvent` are not touched by this work.
- **Error propagation:** Per-item failures (dataset-level or channel-level) are accumulated into `BatchPhasorItemResult` / `BatchPhasorReport`. The dialog converts the report into a `QMessageBox` summary; the CLI converts it into per-line stdout output. Exceptions inside the use case that escape per-item handling propagate to the caller; the dialog catches and surfaces them as a `QMessageBox.critical`. The CLI lets them bubble (exit code 1, traceback to stderr).
- **State lifecycle risks:**
  - **Mask overwrite races.** If the same `.h5` is currently open in the launcher viewer when the batch overwrites its masks, the napari Labels layer for that mask name holds a stale reference. The end-of-run `session.refresh_resource_lists(mask_names=...)` re-reads from disk and forces the bridge to fire `ACTIVE_MASK_CHANGED` if the active mask was among the overwritten ones. Path resolution uses `Path.resolve()` to avoid false negatives from symlinks, `./` prefixes, or case-insensitive filesystem differences.
  - **Phasor cache staleness across `filter_level`.** When the use case skips on-the-fly phasor compute because `/phasor/<ch>/g` already exists, it uses whatever filter_level was used previously. If the cached value differs from `DEFAULT_WAVELET_FILTER_LEVEL`, the fit lands on slightly different filtered maps than a fresh-cache dataset. Mitigation: users who need fresh phasor maps run `python -m percell4.interfaces.cli.batch_phasor --overwrite` first. The new CLI does NOT expose `--overwrite` to keep its `--help` focused.
  - **Multi-process .h5 access.** The dialog opens its own `DatasetStore` per dataset; if the launcher viewer holds a read handle to one of the same files, h5py file-locking behavior depends on the OS (macOS uses advisory locks; Linux is more permissive). Mitigation: this is the same regime the existing `single_cell` workflow operates in — no change. Two PerCell4 instances opening the same file simultaneously is unsupported; not introduced by this work.
- **API surface parity:** Dialog and CLI both call `batch_fit_phasor_masks` with identically-typed kwargs derived from the same parameter set. The mandatory `(CLI, GUI)` parity test in U5 enforces this: same fixture, same parameters, byte-identical mask output.
- **Integration coverage:** The cross-layer chain — decay → intensity_map → ellipse fit → phasor mask → spatial mask → `store.write_mask` — is exercised in U1 (boundary) and U2 (integration) without mocks.
- **Unchanged invariants:**
  - `store.write_mask` continues to not emit events; the long-standing rule that the store is event-free is preserved.
  - `RunPhasorGMM` use case is untouched; the FLIM panel's "Run GMM" button continues to behave exactly as before.
  - `BatchPhasorItemResult` / `BatchPhasorReport` dataclasses are imported, not modified; existing consumers (CLI, tests) are unaffected.
  - `BaseWorkflowRunner`, `WorkflowConfig`, `RunMetadata`, `DatasetFailure`, `_on_workflow_event` are all untouched. The new workflow does not extend or modify them.
  - The other workflow surfaces (`single_cell`, `dilute_phase`, `flim_fret`) are untouched.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Degenerate GMM fit (single pixel above `t_fit`, rank-deficient cov) produces all-zero masks but `store.write_mask` succeeds — channel falsely marked `processed`. | U1 raises `ValueError("degenerate fit (ellipse has zero area)")` when `radii[0] <= 0 or radii[1] <= 0`. U2 catches and routes to `errors`, skipping the `store.write_mask` calls. Tested explicitly in U1 + U2 error-path scenarios. |
| Mask name collision with an existing channel name in the dataset (e.g., suffix `_phasor_1` on channel `mNG` collides with channel `mNG_phasor_1`) crashes the napari layer manager on reload. | Defense in depth: U3 excludes colliding channels from the picker with an explanatory label; U2 (and U5 via U2) re-validates and routes collisions to `errors` for any caller that bypasses the dialog. Documented in `docs/solutions/ui-bugs/add-mask-name-collision-image-layer-crash-2026-05-15.md`. |
| Stale viewer state when the user has the active dataset open during a batch. | End-of-run `session.refresh_resource_lists` is conditional + one-shot, with `Path.resolve()` normalization on both sides of the membership test. Per-write events are intentionally not emitted. |
| Dialog signals not wired → tests pass but manual use shows a stuck Start button. | U3 test scenarios drive real `qtbot.keyClicks` / `qtbot.mouseClick` rather than programmatic `setValue`. Per the `qt-wire-user-edit-signals` learning. |
| CLI/GUI drift (one passes a kwarg the other doesn't). | Mandatory `(CLI, GUI)` parity test in U5. Both surfaces invoke the same use case (`batch_fit_phasor_masks`) with kwargs derived from the same parameter set. The test asserts byte-identical mask output for the same inputs. |
| Phasor cache staleness across `filter_level`. A dataset previously computed at filter_level=5 fits on those (smoother) maps when the current default is 9. | `DEFAULT_WAVELET_FILTER_LEVEL = 9` is a single named constant in the use case module referenced from both dialog and CLI. Researchers needing fresh maps run `python -m percell4.interfaces.cli.batch_phasor --overwrite` first. Documented in the dialog's tooltip on the Start button: "Phasor maps cached from prior runs are reused as-is." |
| Cancel mid-dataset would leave one dataset half-processed (one of the two masks written, the other not). | `cancel_check` is consulted only between datasets, not mid-dataset. The behavior is documented in the QProgressDialog label text; per-mask atomicity is not promised by this workflow. |

---

## Documentation / Operational Notes

- Update `src/percell4/CLAUDE.md` (or the relevant subdir's `CLAUDE.md`) to document the new workflow only if existing workflows already get a mention. If not, no docs change.
- After landing, capture any genuinely new patterns surfaced (e.g., end-of-run conditional refresh shape, intersection-with-decay helper if it ends up shared) via `/ce-compound`. Skip if everything mirrors existing canonical sources.
- Audit retrieval gate (per project CLAUDE.md R15/R16): the implementer should run `python3 scripts/learnings_applicability.py <path>` for each new/modified T1 file (`store.py`, `application/use_cases/*`, anything under `domain/`) before committing. The relevant learnings are already cited in Context & Research above.

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-phasor-masks-workflow-requirements.md](../brainstorms/2026-05-27-phasor-masks-workflow-requirements.md)
- Related code:
  - `src/percell4/domain/flim/phasor.py` (pure-domain primitives)
  - `src/percell4/application/use_cases/batch_compute_phasor.py` (shape + dataclasses to mirror for the use case)
  - `src/percell4/gui/flim_fret_dialog.py` (primary GUI pattern: self-driving modal dialog with `QProgressDialog` loop)
  - `src/percell4/gui/workflows/single_cell/config_dialog.py` (secondary GUI reference: section/widget patterns)
  - `src/percell4/interfaces/cli/batch_phasor.py` (sibling CLI to clone)
  - `src/percell4/application/use_cases/run_phasor_gmm.py:260–270` (canonical `gmm_to_phasor_roi_geometry` call signature)
- Related canonical sources: `docs/audits/canonical-sources-matrix.yaml` entries for `atomic-write-contract`, `fresh-metadata-read-in-use-cases`, `derived-layer-staleness-invalidation`, `session-state-event-emission`.
