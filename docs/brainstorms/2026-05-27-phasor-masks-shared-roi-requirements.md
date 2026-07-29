---
date: 2026-05-27
topic: phasor-masks-shared-roi
extends: docs/brainstorms/2026-05-27-phasor-masks-workflow-requirements.md
---

# Phasor-Masks Workflow: Shared ROI Across Datasets

## Problem Frame

The current Automated Phasor-Masks Workflow fits a fresh GMM ellipse per dataset, so each dataset's masks are gated by a phasor population that was fit on that dataset alone. For treatment-group comparisons (e.g., Untreated vs As-treated), this is the wrong shape: the scientific question is "how does the As-treated population shift relative to the Untreated baseline?", which requires applying the **same** lifetime gate (same ROI center, radii, angle) to every dataset in the comparison. Re-fitting per dataset makes the gate move with the data and erases the very signal the researcher is trying to measure.

The fix is a per-dataset assignment: each dataset declares either "fit your own GMM" (current behavior) or "use the ROI from a specific other dataset in the run." Targets still apply that ROI against **their own** phasor maps to produce masks — only the ROI geometry is shared.

---

## Actors

- **A1. Researcher** — picks which dataset is the gold-standard ROI source for each cohort (typically one untreated sample), assigns the rest of that cohort's datasets to use it.

---

## Key Flows

```
Dialog config:
  [datasets list with per-row ROI dropdown]
       ↓
  Source: self-fit  |  Source: untreated_a.h5  |  ...
       ↓
Run loop:
  Pass 1: process self-fitting datasets, cache geometry by (source_path, channel)
  Pass 2: process target datasets, look up geometry by their assigned source
       ↓
End-of-run summary
```

- **F1. Configure a mixed batch.** Researcher picks 4 datasets (1 untreated, 3 treated), the channel picker shows the intersection. The ROI column starts with all four rows reading "fit own GMM". Researcher changes rows 2–4 to point at untreated_a. Start enables.
- **F2. Run a mixed batch.** Self-fitting datasets fit and apply first (the source's masks are written using its own freshly-fitted ROI). Then target datasets reuse the cached ROI geometry, apply against their own phasor maps, and write their masks.
- **F3. Headless re-run via CLI.** Researcher reruns the same study with `percell4-batch-phasor-masks *.h5 --channels mNG --roi-source AsTreated_a.h5=untreated_a.h5 --roi-source AsTreated_b.h5=untreated_a.h5 --roi-source AsTreated_c.h5=untreated_a.h5`.

---

## Requirements

### Configuration

- **R1.** Each dataset row in the dialog gains an "ROI source" `QComboBox` to the right of the existing path label.
- **R2.** The dropdown for any row shows `fit own GMM` (default, on top) plus every **other** dataset in the queue whose own assignment is currently `fit own GMM`. Datasets pointing at a source are not eligible to themselves be a source (no chains, no cycles).
- **R3.** When the user changes any row's assignment, all other rows' dropdowns refresh — if a row was pointing at a now-non-self-fitting dataset, that option disappears from its menu and its current selection falls back to `fit own GMM` (with a brief inline message that names what changed).
- **R4.** Removing a self-fitting dataset that other rows depend on falls those dependents back to `fit own GMM` with the same inline message.

### Behavior

- **R5.** ROI assignment scope is **per-dataset, applies to every selected channel uniformly**. When dataset X points at dataset Y, every selected channel `<ch>` of X uses Y's `<ch>` ROI. (The channel-intersection rule already guarantees Y has every selected channel.)
- **R6.** Persistence is **in-memory within a single run only**. The ROI cache is keyed by `(source_path, channel)` and populated when a self-fitting dataset's fit succeeds. Targets look up by their assigned source path + the channel being processed. The cache is discarded when the run completes; the next run re-fits.
- **R7.** Execution order: **all self-fitting datasets are processed first**, then all target datasets. Within each group, order matches the user's dataset list order. This guarantees every target's lookup finds the source's geometry in the cache.
- **R8.** If a self-fitting source's fit fails (degenerate ellipse, missing phasor, etc.) for channel `<ch>`, every target that points at that source records a channel-level error for `<ch>`: `errors[ch] = "ROI source <source_path> failed: <reason>"`. Other channels of the same target proceed independently.
- **R9.** Self-fitting datasets still write their own masks — they are both source *and* target of themselves.

### CLI

- **R10.** The CLI accepts repeated `--roi-source TARGET=SOURCE` flags. Both `TARGET` and `SOURCE` are `.h5` paths. Order-independent. Unmentioned datasets default to self-fitting.
- **R11.** CLI validation rejects (exit 2): a `TARGET` that isn't in the resolved positional `paths`; a `SOURCE` that isn't in `paths`; a `SOURCE` that itself appears as a `TARGET` in some other `--roi-source` flag (no chains).

### End-of-run report

- **R12.** The summary `QMessageBox` (and CLI stdout) labels each dataset with its ROI provenance: `<dataset.h5> [source: self]` or `<dataset.h5> [source: untreated_a.h5]`. Lets the researcher confirm the assignments at a glance after the run.

---

## Scope Boundaries

### Explicit non-goals
- **Chains** (target points at another target). Source must be self-fitting; trees of depth 1 only.
- **Per-channel source overrides** (e.g., "use Y's mNG ROI for X's mNG, but fit X's Halo independently"). One source assignment per dataset, applies to all selected channels.
- **Cross-run ROI persistence.** ROIs are computed and discarded per run. Not saved as HDF5 attrs, not saved to sidecar files, not browseable in a "saved ROI library".
- **Different-channel-name mapping** (e.g., "use source's mNG ROI for target's Halo"). Channel name must match between source and target. The channel-intersection rule already enforces this implicitly.

### Deferred for later
- Auto-grouping by filename prefix or by an external manifest file. Right now the researcher manually picks the source per row; a future "auto-group" button could parse `Untreated_*` / `AsTreated_*` prefixes and propose assignments.
- Persisting ROI provenance as HDF5 attrs on each `/masks/<name>` group (`roi_center`, `roi_radii`, `roi_angle_deg`, `roi_source_path`). Useful for traceability long after the run, but no downstream consumer reads such attrs today.

---

## Dependencies and Assumptions

- The existing channel-intersection rule (every selected channel must be in every selected dataset, including any source) carries over unchanged. Sources are selected datasets, so their channels are part of the intersection by construction.
- The U1 domain helper currently does fit + apply as one function. Planning will decide whether to split it into `fit_phasor_ellipse(...)` and `apply_ellipse_masks(geometry, ...)` or thread an optional `geometry` parameter through. Either path is feasible; impacts U1's test surface but not its contract for callers that don't supply a geometry.
- The use case (`batch_fit_phasor_masks`) gains an additional kwarg — likely `roi_sources: dict[Path, Path | None]` — and its loop becomes two-pass (or single-pass with topological ordering).

---

## Success Criteria

- A researcher selects 4 datasets and 1 channel, assigns the As-treated rows to use untreated_a.h5's ROI, clicks Start. The four resulting masks all reflect the same ellipse position (verifiable by comparing the per-mask coverage shape — they should look like the As-treated phasor populations gated by the Untreated's lifetime distribution).
- Running the workflow against a single "self-fit" dataset produces identical output to the pre-feature behavior — the new column has no effect when nothing points at anything.
- A researcher reproduces the same study headlessly with the documented `--roi-source` flag pattern.
- Removing the untreated_a row from the dataset list while As-treated rows depended on it cleanly falls those rows back to "fit own GMM" with a visible message — no orphaned state, no silent re-fit.

---

## Open Questions for Planning

- Should U1 be **decomposed** into two functions (`fit_phasor_ellipse` returning geometry; `apply_ellipse_masks` taking geometry + phasor + intensity) or extended with an optional `geometry` parameter (skip fit if supplied)? Decomposition is cleaner separation of concerns; extension is a smaller diff. Decide during planning.
- How to surface "ROI source's fit failed" failures in the per-item report — do they go in `errors` keyed by the target's channel (so the user sees the target line as `partial`), or in a new dataclass field (so the user sees them as "couldn't run, blocked on source")?
- Whether the dialog needs an explicit "validate assignments" step before Start enables, or whether the existing `_update_start_enabled` extension is sufficient.
