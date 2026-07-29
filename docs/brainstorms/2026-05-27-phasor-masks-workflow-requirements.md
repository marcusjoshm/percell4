---
date: 2026-05-27
topic: phasor-masks-workflow
---

# Automated Phasor-Masks Workflow

## Problem Frame

The researcher's manual phasor-mask protocol takes ~6 GUI clicks per channel per dataset and is the same every time: fit a single-cluster GMM ellipse on a high-intensity subset of the phasor distribution, then apply that ellipse as a mask twice — once at a permissive intensity threshold and once at a conservative one — yielding two binary masks per FLIM channel. Across a study with N datasets × M channels, this is 12·N·M clicks of identical work with no scientific decisions in between.

The pair of masks captures the same lifetime population at two different stringencies. The conservative version (higher intensity threshold) is used downstream for high-confidence per-cell measurements; the permissive version (no intensity threshold) preserves coverage for visualization and validation. Both are needed; they answer different questions.

This workflow replaces the manual loop with a fully unattended batch.

---

## Actors

- **A1. Researcher** — selects datasets and channels, accepts or edits the default threshold/suffix parameters once, starts the run, and reads the end-of-run report.

---

## Key Flows

```
Phase 0: Validate selection (channel intersection + decay presence)   [config dialog]
Phase 1: For each dataset × selected channel:                         [unattended]
           a. Compute phasor (if not cached)
           b. Fit n=1 ellipse via GMM on pixels with intensity ≥ t_fit
           c. Apply ellipse as mask at intensity ≥ t_mask_a → <channel><suffix_a>
           d. Apply ellipse as mask at intensity ≥ t_mask_b → <channel><suffix_b>
Phase 2: End-of-run report (per-item status: succeeded / failed)      [modal]
```

- **F1. Configure and start a run (GUI)**
  - **Trigger:** Researcher clicks "Phasor Masks" entry in the Workflows tab.
  - **Steps:** Pick datasets → channel picker auto-narrows to the intersection of channels that exist in every selected dataset **and** have FLIM decay → researcher selects which of those channels to process → review/edit the three intensity thresholds and two mask-name suffixes → Start.
  - **Outcome:** Each selected channel in each selected dataset gains two new masks under `/masks/<channel><suffix>`.

- **F2. Headless re-run (CLI)**
  - **Trigger:** Researcher invokes `percell4-batch-phasor-masks` with dataset paths + `--channels` + threshold/suffix flags.
  - **Outcome:** Same as F1 with the same end-of-run summary printed to stdout.

---

## Requirements

### Inputs

- **R1.** Workflow accepts a set of `.h5` dataset paths chosen by the user (GUI multi-select; CLI positional paths or directory glob).
- **R2.** Workflow computes the **channel intersection**: only channels that appear in *every* selected dataset's `metadata.channel_names` AND have a `/decay/<channel>` group in every selected dataset are eligible.
- **R3.** The GUI channel picker displays only eligible channels. If the intersection is empty, Start is disabled with an inline explanation.
- **R4.** Three intensity threshold parameters, exposed as editable spinboxes pre-filled with defaults:
  - **t_fit** (GMM-fit intensity threshold) — default `10`
  - **t_mask_a** (first mask intensity threshold) — default `0`
  - **t_mask_b** (second mask intensity threshold) — default `5`
- **R5.** Two mask-name suffix parameters, exposed as editable text fields pre-filled with defaults `_phasor_1` and `_phasor_5`. The user can change these before running.

### Behavior

- **R6.** For each (dataset, channel) pair, the phasor (G, S maps) is computed if not already cached at `/phasor/<channel>/{g,s}`. If a cached phasor exists, it is reused as-is.
- **R7.** The GMM step fits a single ellipse (shape = ellipse, n = 1, criterion = BIC) on the subset of phasor pixels where pixel intensity ≥ t_fit. With n=1, this is a deterministic single-cluster fit and never fails to converge.
- **R8.** The fitted ellipse is applied as a mask twice against the **full** phasor (no GMM re-fit between applications), filtered by intensity ≥ t_mask_a and ≥ t_mask_b respectively. Each application writes a binary mask to `/masks/<channel><suffix>`.
- **R9.** The workflow runs **fully unattended** — no inline confirmation, preview, or approval queue between datasets.
- **R10.** Mask writes **overwrite silently** when a mask of the same name already exists. The end-of-run report flags which datasets had overwrites occur.

### Reporting and failure handling

- **R11.** Per-(dataset, channel) status is tracked in the same taxonomy as the existing batch CLIs: `succeeded` / `failed` (with error string). The workflow continues to the next item on failure.
- **R12.** At end-of-run, present a modal (GUI) or stdout summary (CLI) showing: total items processed, count by status, and the list of any `failed` items with their error strings.
- **R13.** Up-front validation (Phase 0) catches the predictable cases — missing channel, missing decay — by excluding them from the eligible channel list. By the time Phase 1 starts, every (dataset, channel) item has the data it needs.

### Surface and ergonomics

- **R14.** Ships as a new entry in the **Workflows tab** alongside `Single Cell` and `Dilute Phase`, with its own config dialog + runner mirroring those existing patterns.
- **R15.** Also ships as a CLI `percell4-batch-phasor-masks` with flags for: dataset paths (positional, accepts dirs), `--channels`, `--t-fit`, `--t-mask-a`, `--t-mask-b`, `--suffix-a`, `--suffix-b`, `--dry-run`. The use case lives under `src/percell4/application/use_cases/` and is shared by both surfaces.

---

## Scope Boundaries

### Explicit non-goals
- Multi-cluster GMM (n>1) is out of scope. The protocol is single-cluster by design.
- ROI shapes other than ellipse (rectangle, polygon, freehand) are out of scope.
- Variable count of output masks per channel (e.g., 3 or more thresholds) is out of scope — exactly two masks per channel.
- Per-dataset parameter overrides are out of scope — one (t_fit, t_mask_a, t_mask_b) tuple applies to every dataset in the run.

### Deferred for later
- An "inspection-only" end-of-run review window that lets the user visualize each dataset's ellipse + resulting masks for sanity checking. Worth adding if researchers find quality issues in practice; not needed for v1 because GMM at n=1 is deterministic.
- Per-channel parameter overrides (e.g., `--t-fit ch0=10,ch1=12`). Add only if researchers report needing different thresholds for different channels in the same run.

---

## Dependencies and Assumptions

- The existing `Compute Phasor` use case (visible in the FLIM panel) is reusable as a function — not just a button handler — and accepts a channel name plus a dataset handle. Verified by the existence of `src/percell4/workflows/phases.py` and `src/percell4/interfaces/cli/batch_phasor.py`, both of which compute phasors headlessly.
- The existing GMM ellipse fitting code is similarly reusable as a function. Unverified — needs confirmation during planning.
- The "Apply ROI as Mask" action and the "Apply Current Phasor as Mask" action (per the screenshots) write to `/masks/<name>` in the dataset's `.h5`. The mask-writing path is shared with the rest of the app.

---

## Success Criteria

- A researcher selects 10 datasets and 2 channels, accepts defaults, clicks Start, and walks away. ~10 minutes later (driven by phasor computation, not human input), every dataset has 4 new masks (2 per channel) and the end-of-run report shows 20 `succeeded` items.
- A second researcher reproduces the same masks headlessly via `percell4-batch-phasor-masks /path/to/datasets/ --channels mNG Halo`.
- Re-running the workflow on the same datasets overwrites the masks in place; downstream measurements use the latest version automatically.

---

## Open Questions for Planning

- Where exactly does the GMM ellipse-fitting code currently live, and is it function-callable as-is or only wired through GUI signals?
- Does the existing mask-write path emit a `state_changed` event that other open windows respond to? If so, running a 100-item batch could thrash the UI; planning should consider batching or suppressing events during the run.
- CLI naming: `percell4-batch-phasor-masks` vs. `percell4-batch-phasor-segment` vs. just adding a `--gmm-masks` flag to the existing `percell4-batch-phasor`. Leave the choice to planning.
