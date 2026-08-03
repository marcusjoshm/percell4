---
title: "feat: CHANGELOG with dated feature history + README Features refresh"
type: feat
status: active
date: 2026-06-25
---

# feat: CHANGELOG with dated feature history + README Features refresh

## Overview

Add a root `CHANGELOG.md` that records PerCell4's features and **when each was
implemented**, and refresh the README `## Features` section so it reflects the
current capability set (the recent stitching, time-lapse, adaptive-clipping, and
CNR work is not yet represented). The README stays the "what it does / how to run
it" document; the CHANGELOG becomes the "when it landed" document, linked from the
README.

Source of truth for the timeline is the repository's own history: the `feat:`
commit log (`2026-03-25` → `2026-06-25`) and the ~55 dated `docs/plans/*-plan.md`
files. This is documentation-only work — no source code, no tests.

---

## Problem Frame

PerCell4 has grown from initial scaffolding (2026-03-25) to a full single-cell
FLIM platform over three months, but there is **no changelog or feature history**
— a user or collaborator cannot see what the app does over time or when a given
capability appeared. The README `## Features` list (README.md:449–462) is curated
but already stale: it predates overlap-aware tile registration + fusion (the
2026-06 stitching work), the CNR subpopulation classifier, and the adaptive local
clipping module, and the workflow-protocol "Tile Stitching" paragraph
(README.md:73) describes only non-overlapping grid stitching even though the GUI
now exposes Register + Fusion controls.

The user asked for "an update to the documentation [including] a features list and
when new features were implemented." Chosen shape (confirmed): a new root
`CHANGELOG.md` plus a README Features refresh.

---

## Requirements Trace

- R1. A root `CHANGELOG.md` lists PerCell4's user-facing features grouped by the
  month they were implemented, with each milestone dated from the repository's
  own history (commits + `docs/plans/`).
- R2. The README `## Features` section is refreshed to cover the current capability
  set, including the additions that postdate the last Features edit (overlap-aware
  stitching registration + fusion, adaptive local clipping, CNR classification).
- R3. The README links to the CHANGELOG so a reader can move from "what" to "when".
- R4. The dated history is accurate — every milestone's date is traceable to a
  commit date or a `docs/plans/` filename date, not invented.

---

## Scope Boundaries

- Not a semantic-version release process. The project is `0.1.0` with no tagged
  releases; the CHANGELOG groups by **month/milestone**, not by released versions.
  No version bump, no git tags, no release automation.
- Not an exhaustive per-commit log. The CHANGELOG curates **user-facing
  capabilities**; internal refactors (e.g. the hexagonal-architecture migration)
  get at most a one-line "Internal" note, not a blow-by-blow.
- No rewrite of the README's Workflow Protocol, Installation, CLI, or Tech Stack
  sections beyond the targeted stitching-paragraph correction in U3.
- No new tooling to auto-generate the CHANGELOG from git. Going-forward
  maintenance guidance is a doc note, not a script (see Documentation Notes).
- No changes to per-module `CLAUDE.md` files (those describe current state by
  design and are not a user-facing changelog).

---

## Context & Research

### Relevant Code and Patterns

- `README.md` — the primary doc. Existing `## Features` bullets at README.md:449–462;
  `## Tech Stack` at 436–445; `## Command-line Tools` at 152–431; Table of Contents
  at 13–38 (must gain a CHANGELOG / Feature-History entry). The "Tile Stitching"
  protocol paragraph at README.md:73 is the stale stitching text.
- `docs/plans/*-plan.md` — ~55 dated plans, each a `feat`/`fix`/`refactor` milestone
  with an ISO date in the filename. These are the highest-signal "when" source and
  map almost 1:1 to user-facing capabilities.
- `CLAUDE.md` (root) + per-module `src/percell4/**/CLAUDE.md` — describe current
  architecture and feature areas; useful to phrase Feature bullets accurately
  (e.g. the stitching summary in `src/percell4/domain/io/CLAUDE.md`).
- `git log --grep=^feat --date=short` — authoritative commit-date source for R4.

### Derived feature timeline (directional content for U1)

> Directional guidance for the CHANGELOG's content, not a spec to transcribe
> verbatim. Dates are from commit history / plan filenames; the implementer should
> spot-verify each against `git log` per R4.

- **2026-03 — Foundation (Mar 25–31):** project scaffolding; HDF5 `DatasetStore`
  + `ProjectIndex`; TIFF import pipeline (scanner/assembler/readers/importer);
  Cellpose segmentation + ROI import + interactive label cleanup; per-cell
  measurements; FLIM phasor computation + wavelet filter + phasor-plot window;
  TCSPC `.sdt`/`.bin` import with per-channel calibration; multi-window GUI
  (launcher/viewer/data-plot/cell-table) on one `CellDataModel`; cross-window
  selection + cell filter; single-pass multi-ROI phasor measurement.
- **2026-04 — Workflows & architecture (Apr):** grouped segmentation (cluster →
  per-group Otsu → threshold QC); batch TIFF compression (`CompressDialog`,
  auto/manual, channel→layer assignment); Add-Layer-to-Dataset dialog; Export
  Images dialog; single-cell thresholding workflow (`BaseWorkflowRunner` state
  machine + interactive QC); hexagonal-architecture refactor *(internal)*; napari
  multi-label selection tool; TCSPC append + cross-format token matching; phasor
  UX (mask filter, PNG save, `nipy_spectral`); structured worker errors + torch
  error dialog; Windows MSVC-redistributable startup warning.
- **2026-05 — Phasor, time-lapse, batch CLI (May):** phasor GMM segmentation;
  phasor cache + `.npz` I/O; phasor clear-within-ROI; napari viewer presets;
  phasor "apply current as mask"; channel override for Cellpose/FLIM; Session
  selection window; Windows-via-WSL install path; dataset-wide spatial binning;
  batch phasor/wavelet + batch export-images + dilute-phase mask generation;
  binned-TIFF export option; pixel-size visibility + TIFF-metadata roundtrip;
  README revamp with workflow protocol; **time-lapse tracking + lineage
  (laptrack)**; workflow tracking + `percell4-batch-cellpose-laptrack` CLI; FLIM
  filter options; FLIM-FRET analysis workflow; seg-QC recovery options;
  `percell4-batch-rename` / `-delete` CLIs; phasor-masks workflow + shared ROI;
  whole-field multichannel analyses; per-particle multichannel CSV.
- **2026-06 — Puncta, adaptive clipping, CNR, stitching (Jun):** Segment-tab
  Cellpose-settings parity; headless puncta thresholding (`percell4-batch-threshold`
  path); adaptive local clipping (ALC) GUI module; multi-timepoint feature parity;
  large-file load-time improvements; existing-mask reuse + threshold CLI; iterative
  Otsu thresholding; adaptive-clip thresholding workflow; CNR subpopulation
  classification (discover/guided/forced); ALC auto-extraction mode; interactive
  CNR segmenter; ALC + CNR per-timepoint workflow; **overlap-aware mosaic stitching
  — phase-correlation registration on the overlap region, grid-prior band + outlier
  rejection, nominal-overlap grid fallback, and None / Linear-Blending fusion**;
  channel-rename reference fix for registration.

### Institutional Learnings

- `docs/solutions/architecture-patterns/overlap-aware-stitching.md` — phrasing for
  the stitching Feature bullet and CHANGELOG entry (registration solved once on a
  reference channel, reused for every channel + the FLIM decay stream).
- Documentation Rules in root `CLAUDE.md`: "Active docs contain ONLY what IS, not
  what WAS." A CHANGELOG is the sanctioned home for "what WAS" — keep historical
  narrative in `CHANGELOG.md`, keep `CLAUDE.md`/README describing current state.

### External References

- "Keep a Changelog" (keepachangelog.com) — informs the file's shape (reverse
  chronological, grouped headings), adapted to month-milestones since there are no
  semver releases.

---

## Key Technical Decisions

- **Group by month-milestone, newest first.** Version is `0.1.0` with no releases,
  so semver headings would be fiction. Month headings (`## 2026-06`, `## 2026-05`,
  …) honor "when implemented" honestly. Rationale: R1, R4.
- **Curate user-facing capabilities, not commits.** Each month lists the
  capabilities a user would notice; internal refactors collapse to one line.
  Rationale: a changelog read by lab users, not a git mirror (Scope Boundaries).
- **Dates traceable to repo history.** Every milestone's date comes from a commit
  date or a `docs/plans/` filename; the implementer cross-checks rather than
  estimating. Rationale: R4.
- **README Features stays a bulleted capability list; CHANGELOG carries the
  timeline.** Avoid duplicating the full history into the README — link instead.
  Rationale: R2, R3; keeps the README scannable.
- **`## Unreleased` lead section retained** for in-flight work, so the going-forward
  maintenance habit has an obvious home. Rationale: Documentation Notes.

---

## Open Questions

### Resolved During Planning

- Where should the features list + timeline live? → New root `CHANGELOG.md` +
  refresh the README Features section (user-confirmed).
- Semver vs month grouping? → Month-milestone grouping (no releases exist).
- Exhaustive vs curated? → Curated user-facing capabilities.

### Deferred to Implementation

- Exact wording of each month's bullets — refined while writing against `git log`
  and the plan filenames; the timeline above is the scaffold, not final copy.
- Whether the dilute-phase / FLIM-FRET / whole-field items each warrant their own
  bullet or fold into a parent capability — a copy-editing call made in-unit.

---

## Implementation Units

- U1. **Create `CHANGELOG.md` with dated feature history**

**Goal:** A root `CHANGELOG.md` recording user-facing features grouped by month,
newest first, each milestone dated from repo history.

**Requirements:** R1, R4

**Dependencies:** None

**Files:**
- Create: `CHANGELOG.md`

**Approach:**
- Header: project name + one-line note that this tracks notable user-facing
  changes, with a pointer that dates derive from commit history / `docs/plans/`.
- Lead `## Unreleased` section (empty or with anything merged but unlisted), then
  `## 2026-06`, `## 2026-05`, `## 2026-04`, `## 2026-03 — Foundation`, newest first.
- Under each month, group lines by intent using lightweight subheads where it
  helps — `Added`, `Changed`, `Fixed` (Keep-a-Changelog vocabulary) — but do not
  force all three when a month is purely additive.
- Populate from the **Derived feature timeline** in Context & Research; before
  finalizing each month, run `git log --date=short --grep=^feat` filtered to that
  month and reconcile (R4). Collapse internal refactors (hexagonal migration,
  audits) to a single `Changed (internal)` line.
- Give the 2026-06 stitching entry its own clearly-worded bullet (registration on
  the overlap region, grid fallback, None/Linear-Blending fusion, channel-rename
  fix) since it is the newest and most-requested capability.

**Patterns to follow:**
- "Keep a Changelog" structure adapted to month headings (see External References).
- Capability phrasing mirrored from `README.md:449–462` and
  `docs/solutions/architecture-patterns/overlap-aware-stitching.md`.

**Test scenarios:**
- Test expectation: none — documentation file with no executable behavior.
  Correctness is covered by the Verification checks below.

**Verification:**
- File renders as valid Markdown (headings nest, lists parse) in a previewer.
- Every `## YYYY-MM` heading has at least one entry, ordered newest-first.
- Spot-check: pick 5 entries across different months; each entry's month matches a
  real `feat:` commit date or `docs/plans/` filename date (R4).
- The newest month includes the overlap-aware stitching capability.

---

- U2. **Refresh README `## Features` and link the CHANGELOG**

**Goal:** The README Features list reflects current capabilities and points readers
to the CHANGELOG for the dated history.

**Requirements:** R2, R3

**Dependencies:** U1 (link target must exist)

**Files:**
- Modify: `README.md`

**Approach:**
- Update the `## Features` bullets (README.md:449–462):
  - Revise the stitching story: add overlap-aware tile registration (phase
    correlation on the overlap region), the nominal-overlap grid fallback, and the
    None / Linear-Blending fusion options. Today no Feature bullet mentions
    registration or fusion at all.
  - Add bullets (or fold into existing ones) for capabilities added since the last
    Features edit: **adaptive local clipping (ALC) puncta detection**, **CNR
    subpopulation classification + interactive CNR segmenter**, and confirm
    **time-lapse tracking/lineage** and **dilute-phase mask** remain represented.
  - Keep each bullet one-to-two sentences, matching the existing voice; avoid
    turning Features into a changelog (that is U1's job).
- Add a short pointer at the end of `## Features` (or a one-line `## Changelog`
  stub) linking to `CHANGELOG.md` for "when each feature landed".
- Update the Table of Contents (README.md:13–38) to include the new
  Changelog/Feature-History anchor.

**Patterns to follow:**
- Existing `## Features` bullet voice and length (README.md:449–462).
- TOC anchor style already used throughout README.md:13–38.

**Test scenarios:**
- Test expectation: none — documentation edit with no executable behavior.

**Verification:**
- The Features section names registration + fusion and the ALC/CNR capabilities.
- The CHANGELOG link resolves to `CHANGELOG.md` (relative link works on GitHub).
- The new TOC entry's anchor jumps to the right heading.
- No capability listed in Features is contradicted by the README protocol text.

---

- U3. **Correct the Workflow-Protocol stitching paragraph**

**Goal:** The protocol's stitching instructions match the current GUI (Register +
Fusion controls), so the README does not describe a stale, grid-only flow.

**Requirements:** R2

**Dependencies:** None (independent of U1/U2; can land in the same PR)

**Files:**
- Modify: `README.md`

**Approach:**
- Revise the "Tile Stitching" paragraph (README.md:73) so it mentions: enabling
  **Register overlapping tiles** for overlap-aware registration; the **Overlap %**
  field; the **Fusion** choice (None — measurement-correct default — vs Linear
  Blending for a seamless display, with the note that FLIM datasets force None);
  and that channels renamed in Manual mode are valid registration references. Keep
  it to a few sentences in the existing protocol voice.
- Do not expand other protocol steps; this is a targeted correction.

**Patterns to follow:**
- Surrounding protocol-step prose voice (README.md:67–75).
- Control semantics from `src/percell4/gui/compress_dialog.py` and
  `docs/solutions/architecture-patterns/overlap-aware-stitching.md`.

**Test scenarios:**
- Test expectation: none — documentation edit with no executable behavior.

**Verification:**
- The paragraph names the Register, Overlap %, and Fusion controls as they appear
  in the GUI, and the None-for-FLIM rule.
- No contradiction remains between the protocol paragraph and the refreshed
  Features bullet (U2).

---

## System-Wide Impact

- **Interaction graph:** Documentation only — no code paths, signals, or runtime
  behavior affected.
- **API surface parity:** None. No CLI, config, or interface changes.
- **Cross-reference integrity:** New relative links (README → `CHANGELOG.md`, TOC →
  new anchor) must resolve on GitHub and in local Markdown previewers — covered in
  U2 verification.
- **Unchanged invariants:** README Installation, Tech Stack, CLI reference, and all
  per-module `CLAUDE.md` files are untouched; only the Features bullets, TOC, and
  the one stitching protocol paragraph change.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Invented or wrong dates in the CHANGELOG | Cross-check every milestone against `git log --date=short` and `docs/plans/` filenames (R4); U1 verification spot-checks 5 entries. |
| CHANGELOG drifts out of date after this PR | Retain a `## Unreleased` lead section and add a one-line "update on notable changes" habit note (Documentation Notes); keep entries curated so the burden stays low. |
| Features list duplicates the CHANGELOG and the two diverge | Keep README Features a capability list and link out for history; do not copy the dated timeline into the README. |
| Stitching protocol text re-staled by future GUI changes | Scope the correction to current controls; future control changes are a future doc task, not this plan's concern. |

---

## Documentation / Operational Notes

- Going forward, append notable user-facing changes to `CHANGELOG.md` under
  `## Unreleased` as part of the PR that ships them, promoting them to a dated
  month heading when convenient. This is a convention note, not an enforced hook.
- If a release is ever tagged, the month headings can be retro-grouped under a
  version heading; nothing in this plan blocks that.

---

## Sources & References

- Current docs: `README.md` (Features README.md:449–462, TOC 13–38, stitching
  protocol README.md:73), root `CLAUDE.md` Documentation Rules.
- Timeline sources: `git log --date=short --grep='^feat'`; `docs/plans/*-plan.md`
  filenames (2026-03-25 → 2026-06-25).
- Capability phrasing: `docs/solutions/architecture-patterns/overlap-aware-stitching.md`,
  `src/percell4/domain/io/CLAUDE.md`, `src/percell4/gui/CLAUDE.md`.
- Latest capability landed: `docs/plans/2026-06-24-002-feat-mosaic-merge-overlap-stitching-plan.md`.
