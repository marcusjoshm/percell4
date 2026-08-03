---
title: User-facing docs — strip jargon, mirror UI order, order results by audience
date: 2026-05-21
category: conventions
module: documentation
problem_type: convention
component: documentation
severity: medium
applies_when:
  - Writing or editing README sections aimed at non-technical lab users
  - Documenting a workflow that has a corresponding configuration GUI
  - Listing output files in user-facing instructions
  - Reviewing user-facing docs for implementation jargon (HDF5 paths, phase numbers, file extensions, Qt terms)
tags:
  - documentation
  - readme
  - user-facing
  - plain-language
  - ui-mirroring
  - results-ordering
  - non-technical-audience
---

# User-facing docs — strip jargon, mirror UI order, order results by audience

## Context

The percell4 README contains a "Workflow Protocol" section intended as a step-by-step guide for wet-lab researchers (the user's lab colleagues) running the single-cell analysis app. Initial drafts leaked implementation-level vocabulary directly from the codebase — phase numbers, HDF5 paths, terms like "modal," "QC," "NaN-subtracted," "circular ROI," "concatenates per-dataset staging parquets," "unattended." The intended audience is non-technical; they read the protocol with the app open and click through the configuration window.

Two distinct problems surfaced over two rounds of user feedback:

1. **Jargon leakage** — code-level terms made the protocol unreadable for the lab audience even though every term was technically accurate.
2. **Step misordering** — the protocol's step sequence did not mirror the order of widgets in the configuration window, so a researcher could not read top-to-bottom while clicking top-to-bottom. A required step ("Include particle analysis") was also missing entirely.

Three durable conventions emerged for writing wet-lab-facing protocol prose.

## Guidance

**Convention 1 — Strip implementation jargon.** Translate every code-level term to what the user sees on screen and what they do with the mouse or keyboard. Drop file extensions, HDF5 paths, internal state-file names, and developer shorthand. If a sentence describes internal mechanics the user neither controls nor observes, redraft it to describe what the app does on their behalf, or delete it.

**Convention 2 — Protocol step order mirrors the configuration window's widget order.** When the protocol describes filling in a configuration UI, its step sequence must match the visual top-to-bottom order of widgets in that UI. Verify by reading the relevant `_build_*_group()` call sequence in the config dialog source. A researcher must be able to read the protocol top-to-bottom and click straight down the configuration window without flipping back and forth.

**Convention 3 — In a results listing, order files by audience relevance, not codebase canonicity.** List the files the lay reader will actually open first (CSVs that open in Excel / Numbers / Google Sheets). The canonical primary output (e.g. `measurements.parquet`) comes last, tagged with its audience ("for Python or R users"). Source-of-truth ranking is a developer concern; the README serves the reader.

## Why This Matters

Lab researchers and developers read protocol docs with different goals. A developer wants precision: which HDF5 path, which phase, which in-memory transformation. A wet-lab researcher wants to know what to click, what to expect on screen, and when their input is needed. Jargon that is *correct* for a developer is *noise* for the researcher — it raises reading effort without conveying anything the reader can act on, and it makes the doc feel like it was written for someone else.

UI-mirrored step order matters because the README is a walkthrough, not a reference. When the protocol's step 5 corresponds to the configuration window's fifth widget from the top, the reader stops translating between two representations and just follows along. A missing or out-of-order step forces the reader to scan the UI for the matching widget, breaking flow.

Audience-ordered results listings matter because "what is canonical" is a property of the codebase; "what I open next" is a property of the reader. The README's job is to answer the reader's question, not the codebase's.

Lean, audience-targeted docs also serve the project's broader context-hygiene goal: docs that mix audiences accumulate contradictions and force readers (and future agents) to filter out content not meant for them.

## When to Apply

**Apply these conventions to:**

- The README's "Workflow Protocol" section and any equivalent user-facing walkthrough.
- Other docs whose explicit audience is wet-lab colleagues or other non-developer users.
- Any prose that will be read alongside the running app by someone clicking through the UI.

**Do NOT apply to:**

- `CLAUDE.md` files (per-module or top-level) — these are for Claude Code and developers; they require technical precision.
- Plan, brainstorm, or audit docs under `docs/audits/`, `docs/brainstorms/`, `docs/plans/`, etc.
- Solutions docs under `docs/solutions/` — these document bugs, conventions, and patterns for developers and agents and must keep code-level terminology, file paths, and class names.
- Inline code comments and docstrings.
- The README's `Batch TIFF Export (CLI)` section and `Features` list — these intentionally retain technical language for a developer / power-user audience. After the lay-language pass a residual-jargon grep (`Phase [0-9]|modal|/labels/|/masks/|run_state\.json|staging parquet|NaN|autothresh|concatenate|/intensity/|chNN`) confirmed remaining hits were confined to those sections by design. (session history)

In short: strip jargon and mirror UI order when the audience is the bench scientist using the app. Keep precision when the audience is a developer or an agent maintaining the code.

## Examples

### Translation table (Convention 1)

| Jargon | Lay term |
|---|---|
| "Phase 0 (compress TIFFs to `.h5`)" | "first compresses your TIFFs into datasets" |
| "Phase 1 (Cellpose segmentation across every dataset)" | "runs Cellpose to find every cell in every dataset" |
| "`/labels/<name>`" / "`/masks/<name>`" paths | dropped — no mention of HDF5 paths |
| ".h5" file extension | "dataset" or "datasets" |
| "modal" / "Threshold QC modal" | "review window" |
| "QC the segmentation (interactive)" | "Review the cell outlines (your input needed)" |
| "circular ROI" | "circular region" |
| "autothresholded mask" / "candidate mask" | "proposed mask" |
| "NaN-subtracted in memory" | "automatically expanded slightly and removed from the input for the next round" |
| "concatenates per-dataset staging parquets" | "saves the results" |
| "unattended" | "you do not need to do anything during this part" |
| "configuration window locks in" | dropped — sentence redrafted to describe what the app does next |
| "`run_state.json`" | "the app saves its progress after each step" |

### Verifying widget order (Convention 2)

Grep the config dialog's `_build_*_group()` calls to recover the canonical widget sequence, then mirror it in the protocol:

```python
# src/percell4/gui/workflows/single_cell/config_dialog.py:297-303
layout.addWidget(self._build_datasets_group(), stretch=3)
layout.addWidget(self._build_cellpose_group())
layout.addWidget(self._build_rounds_group(), stretch=2)
layout.addWidget(self._build_particles_group())
layout.addWidget(self._build_dilute_group())
layout.addWidget(self._build_columns_group())
layout.addWidget(self._build_output_group())
```

The protocol's steps 3–9 follow this exact sequence: Add datasets → Configure Cellpose → Choose edge-cell mode → Define thresholding rounds → Include particle analysis → Enable dilute-phase → Pick output.

### Before / after — step 10 (Start the run)

**Before (jargon-heavy):**

> Click **Start**. The configuration window locks in. The workflow runs Phase 0 (compress TIFFs to `.h5`) and Phase 1 (Cellpose segmentation across every dataset) unattended. Watch progress in the launcher status bar.

**After (lay):**

> Click **Start**. The app first compresses your TIFFs into datasets, then runs Cellpose to find every cell in every dataset. You do not need to do anything during this part — watch progress in the launcher status bar.

### Before / after — step 13 (dilute round inner loop)

**Before:**

> The accepted mask is dilated and NaN-subtracted in memory.

**After:**

> The accepted mask is automatically expanded slightly and removed from the input for the next round.

## Related

- `docs/plans/2026-05-21-002-feat-readme-revamp-with-workflow-protocol-plan.md` — the plan that motivated this work; defines the README structure but does not encode the three conventions above.
- `README.md` — the artifact these conventions govern.
- `src/percell4/gui/workflows/single_cell/config_dialog.py` — read-only reference for Convention 2 (widget-order verification).
- Reference README used as a structural model: PerCell v1 at `https://github.com/marcusjoshm/percell`. (session history)
