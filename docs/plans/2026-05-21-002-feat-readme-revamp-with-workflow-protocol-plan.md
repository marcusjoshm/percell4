---
title: Revamp README with logo, workflow protocol, and per-OS install
type: feat
status: active
date: 2026-05-21
---

# Revamp README with logo, workflow protocol, and per-OS install

## Overview

The current `README.md` (210 lines) covers install + batch-export CLI but does not introduce the app, show the logo, list the tech stack, give a table of contents, or walk a new researcher through the analysis workflow. The percell (v1) README is the reference: header with logo, TOC, "Typical Use Case" walkthrough, per-OS install. PerCell4 needs the same shape, adapted for the Qt/GUI architecture.

This plan rewrites `README.md` end to end. The result is a single document that lets a new researcher (a) understand what the app is, (b) read the canonical end-to-end workflow protocol top-to-bottom before opening the app, (c) know exactly how to name channels/masks/segmentations so downstream consumers don't break, (d) install on macOS, Linux, or Windows, and (e) reach for the batch TIFF export CLI when they need it.

---

## Problem Frame

New users currently arrive at `README.md` and immediately get install instructions. There is no app introduction, no logo, no description of what the workflow looks like, and no guidance on file-naming contracts that other parts of the system rely on. Linux install is omitted entirely, even though the stack runs on Linux. The batch-export CLI is documented but buried; researchers who only want headless TIFF dumps have to scroll past the GUI install to find it.

The user pointed at `/Users/leelab/percell/README.md` as a structural reference: logo header → TOC → typical use case → install per OS → troubleshooting. We adopt that shape but rewrite the body for PerCell4's Qt+napari multi-window architecture and HDF5-per-experiment data model.

---

## Requirements Trace

- R1. Header block with the logo image (centered, scaled), app title, one-sentence tagline, and a short badge row (Python version, platform, license).
- R2. A "Tech Stack" section listing the core dependencies and what each does (Qt + napari + pyqtgraph, h5py, Cellpose, tifffile/sdtfile, scikit-image/scipy/numpy). Versions sourced from `pyproject.toml`.
- R3. A Table of Contents linking every top-level section. Anchors must match the rendered GitHub-flavored markdown slugs.
- R4. A "Workflow Protocol" section sitting **above** install (per the user's "at the top of the readme" instruction), containing four subsections in order: (a) how to load TIFF files, (b) channel/mask/segmentation naming conventions, (c) step-by-step walkthrough with QC steps, (d) pointer to the batch TIFF export CLI for headless runs.
- R5. The TIFF-loading subsection explains the I/O panel entry points (Import, Load existing `.h5`, Batch TCSPC append) and the compress dialog's role.
- R6. The naming-convention subsection codifies: channel names are stored with a `chNN` prefix in `/metadata.channel_names` (importer-enforced); segmentation layers live at `/labels/<name>`; mask layers live at `/masks/<name>`; workflow round names become `group_<round>` parquet columns; dilute mask names must not collide with thresholding round names (R14 from the end-to-end workflow brainstorm).
- R7. The step-by-step walkthrough covers the canonical end-to-end workflow phases as a numbered narrative: Compress → Cellpose Segment → Segmentation QC → Grouped Thresholding (1..N) → Threshold QC (1..N) → optional Dilute-phase mask → Measure → Aggregate/Export. Each phase identifies the launcher panel/button to click and the QC interaction (modal, viewer keystrokes) the researcher will see.
- R8. A "Batch TIFF Export (CLI)" section, lifted and lightly refined from the existing README, with the CLI invocation, options table, and two worked examples. It must reference the launcher's GUI export equivalent so researchers know which to use when.
- R9. A "Features" section enumerating each major capability with one to two sentences of description (HDF5 projects, Cellpose+QC, FLIM phasors, multi-ROI masks, per-cell measurements, batch workflows, image/measurement export, CLI tools).
- R10. Installation instructions for **macOS, Linux, and Windows**, each as a self-contained subsection. Linux is currently absent and must be authored from scratch. macOS and Windows content is preserved from the current README (it's already detailed and accurate), reorganized under the new headings.
- R11. Existing Windows-specific content (PyTorch/Cellpose, MSVC redist, PowerShell vs Command Prompt vs Git Bash, troubleshooting bullets) is preserved verbatim or near-verbatim. No regressions on accuracy.
- R12. A "Troubleshooting" section retained at the tail; existing Windows triage list is preserved.
- R13. License + repo metadata (author, links) end the README.
- R14. The logo file is added to the repo at `art/percell4_logo.png` (copied from `/Users/leelab/Downloads/percell_logo.png`). README references it via a relative path so GitHub renders it.

---

## Scope Boundaries

- No code changes outside README + logo asset. The CLI, workflows, and importer are documented as-is.
- No new badges that require external services (CI, coverage) until those are wired up — limit badges to static facts (Python version, platform, license).
- No screenshots of the GUI in this pass. The walkthrough is text-driven; adding screenshots is deferred.
- No changes to the percell (v1) README — it is a reference only, not a target.
- No translation of the protocol into a separate `docs/protocol.md`. The user explicitly wants the protocol **in** the README.
- No changes to channel-naming behavior, the importer, or the workflow runner. The README documents the existing contract; if the contract is wrong, that is a separate bug, not this plan.
- No PyInstaller / standalone-bundle revisions beyond preserving the existing section.

### Deferred to Follow-Up Work

- Screenshots of the GUI walkthrough (segmentation QC modal, threshold QC modal, dilute panel): future PR once we have a clean demo dataset to reuse.
- Animated GIF or video of one end-to-end run: future, optional.
- A `CONTRIBUTING.md` link in the README header: depends on writing CONTRIBUTING.md first.

---

## Context & Research

### Relevant Code and Patterns

- `README.md` — current 210-line README. Install + CLI sections are accurate and reused; everything else is new or reorganized.
- `pyproject.toml` — authoritative source of Python version, dependencies, and version pins for the Tech Stack section.
- `src/percell4/interfaces/gui/main_window.py:206-216` — launcher sidebar categories (I/O, Viewer, Segment, Analysis, FLIM, Scripts, Workflows, Data). The walkthrough refers to these by name.
- `src/percell4/gui/workflows/single_cell/config_dialog.py` — the workflow config dialog the protocol points to.
- `src/percell4/adapters/importer.py:~211` — the `chNN` channel-name convention. The naming subsection mirrors what the importer writes to HDF5.
- `src/percell4/interfaces/cli/batch_export.py` — the CLI surface documented in the Batch TIFF Export section.
- `docs/brainstorms/2026-05-20-end-to-end-single-cell-workflow-requirements.md` — phase ordering (compress → segment → seg-QC → thresh × N → thresh-QC × N → dilute → measure → aggregate). Walkthrough phases mirror this.
- `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md` — codifies the `chNN` prefix contract; cite as the canonical reason channel names look the way they do.
- `/Users/leelab/percell/README.md` — structural reference for header layout, TOC placement, "Typical Use Case" voice, and per-OS install organization.

### Institutional Learnings

- `docs/solutions/conventions/` and `docs/solutions/integration-issues/` — scan for any naming contracts beyond `chNN` that should be mentioned (e.g., dilute mask name collision rule).
- Channel-name prefix solution (2026-05-21) is the authoritative source for the naming-convention subsection's claim about `chNN`.
- CLAUDE.md "Documentation Rules" (project root) says active docs describe state, not history. README is state — no changelog, no "previously we did X" prose.

### External References

- Not required. The percell v1 README is local. Tech stack docs are linked but not summarized inline.

---

## Key Technical Decisions

- **Single README, no companion protocol file.** The user explicitly wants the protocol in the README. Splitting would force researchers to context-switch.
- **Protocol section above install.** Mirrors percell's "Typical Use Case" placement and matches the user's "at the top of the readme" instruction. Install moves below the protocol.
- **Logo lives at `art/percell4_logo.png`.** Mirrors percell's `percell/art/` convention but at the repo root, since percell4's `src/percell4/` layout already nests code under `src/`. Repo-root `art/` is the conventional GitHub location for README media.
- **Tech Stack section is a definition list, not a table.** Tables are noisy for short "X — does Y" pairs. Definition-list-style bullets read better and match the existing README's bullet voice.
- **Walkthrough is panel-by-panel, not phase-by-phase abstract.** A researcher reads the walkthrough next to the open GUI; concrete "click the Workflows tab" beats abstract "begin the segmentation phase".
- **Linux install is documented as a third sibling subsection, not folded into "macOS/Linux".** Linux Qt/Wayland quirks differ enough (system Python, distro-specific Qt packages, headless display) that combining them would force "if Linux, do X" footnotes throughout.
- **Existing Windows section is preserved with minimal edits.** It's accurate, battle-tested by the WSL plan and the torch c10.dll plan, and rewriting it risks regressions. Reorganization only.
- **Batch TIFF Export keeps its own top-level section.** It is the user's stated "command line tool" requirement and deserves visibility, not a paragraph in Features.
- **Features section is short, one-line-per-feature.** Detailed behavior lives in the walkthrough and in per-module CLAUDE.md files. The README's Features section is a scannable inventory.

---

## Open Questions

### Resolved During Planning

- *Logo placement?* Centered at the top of the README, ~200px width, no caption. The image is square (834×834) and the tagline carries the verbal framing.
- *Badges?* Python 3.12+, Platform (macOS/Linux/Windows), License (MIT). No CI/coverage badges until those pipelines exist.
- *Where does the workflow protocol live?* In the README, above install, exactly as the user asked. Not split into `docs/protocol.md`.
- *Does the README cover the FLIM phasor workflow specifically?* Yes, in the Features section and as a one-paragraph "FLIM phasor analysis" subsection inside the walkthrough — but only at the level of "open the Phasor window, draw an ROI, save as a mask". Full FLIM tutorial is out of scope.
- *Does it cover the dilute-phase mask workflow?* Yes, as an optional step in the walkthrough (per the end-to-end requirements doc — dilute is Phase 5, optional).
- *Linux install: which distros to call out?* Ubuntu 22.04+ as the primary worked example; note the system-package equivalents for `python3.12-venv` and the Qt/xcb libraries. Other distros are derivatives.

### Deferred to Implementation

- *Exact wording of the channel-naming subsection.* The contract is clear (`chNN` prefix); the prose phrasing is a writing pass during implementation.
- *Whether to include a tiny ASCII or Mermaid diagram of the workflow phases at the top of the walkthrough.* Lean yes — a single Mermaid flowchart of the 8 phases helps scannability. Final call when drafting.
- *Whether the TOC includes every H3 or only H2.* Lean H2-only for a clean TOC; H3s are reachable via in-section scroll.

---

## Output Structure

This plan does not create a new directory hierarchy. The two artifacts are:

    README.md            (rewritten in place)
    art/
        percell4_logo.png  (new, copied from /Users/leelab/Downloads/percell_logo.png)

---

## High-Level Technical Design

> *This illustrates the intended README structure for review, not the literal markdown to paste. The implementer should treat it as section-ordering guidance.*

```
# PerCell4
[centered logo]
[badges row]
One-sentence tagline.

## Table of Contents
- Tech Stack
- Workflow Protocol
  - Loading TIFF files
  - Channel, mask, and segmentation naming
  - Step-by-step workflow walkthrough
  - Batch TIFF export (CLI)
- Features
- Installation
  - macOS
  - Linux
  - Windows
- Standalone bundle (PyInstaller)
- Troubleshooting
- License

## Tech Stack
- Python 3.12, Qt (PyQt5 + qtpy), napari, pyqtgraph
- HDF5 (h5py), pandas, pyarrow
- Cellpose, scikit-image, scipy, numpy
- tifffile, sdtfile (FLIM)
- click + rich (CLI)

## Workflow Protocol
[Mermaid flowchart of 8 phases, optional]

### Loading TIFF files
[I/O panel → Import → compress dialog; or Batch TCSPC append for .sdt]

### Channel, mask, and segmentation naming
- Channels: chNN prefix, enforced by importer
- Segmentation layers: /labels/<name>
- Mask layers: /masks/<name>
- Workflow round names → group_<round> parquet columns
- Dilute mask name must not collide with any thresholding round name

### Step-by-step workflow walkthrough
1. Compress TIFFs → .h5 (I/O → Import)
2. Cellpose segmentation (Workflows → Single-cell ... or Segment tab)
3. Segmentation QC (modal, per-dataset queue)
4. Grouped thresholding round 1..N
5. Threshold QC round 1..N
6. Optional: Dilute-phase mask
7. Measurement (per-cell, multi-channel)
8. Aggregate + export (parquet, CSVs)

### Batch TIFF export (CLI)
[existing content, refined]

## Features
- HDF5-backed projects
- Cellpose segmentation + grouped thresholding QC
- FLIM phasor analysis with ROI-to-mask
- Per-cell measurements across channels
- Multi-window Qt+napari UI
- Batch workflows (segment, threshold, dilute, measure, export)
- Image and measurement export
- Headless TIFF export CLI

## Installation
### macOS [existing, preserved]
### Linux [NEW]
### Windows [existing, preserved]

## Standalone bundle (PyInstaller) [existing, preserved]

## Troubleshooting [existing, preserved]

## License [existing]
```

---

## Implementation Units

- U1. **Copy the logo asset into the repo**

**Goal:** Place the percell4 logo at a stable, GitHub-renderable path.

**Requirements:** R14

**Dependencies:** none

**Files:**
- Create: `art/percell4_logo.png` (copy of `/Users/leelab/Downloads/percell_logo.png`, 834×834 PNG with alpha)

**Approach:**
- Create `art/` at the repo root (does not exist yet).
- Copy the file via `cp /Users/leelab/Downloads/percell_logo.png art/percell4_logo.png` — no resizing, no format change. GitHub will scale it down via the `<img width="...">` attribute in the README.
- Verify with `file art/percell4_logo.png` that the PNG header is intact.

**Patterns to follow:**
- percell v1 stores its logo at `percell/art/percell_terminal_window.png`. The repo-root `art/` location is the more common GitHub convention and avoids nesting media under `src/`.

**Test scenarios:**
- Test expectation: none — asset add, verified by `git ls-files art/` and by README render (U6).

**Verification:**
- File exists at the expected path, is a valid PNG, and is committed to git.

---

- U2. **Rewrite the README header, badges, and TOC**

**Goal:** Replace the current `# PerCell4` + Features paragraph with a centered logo, badge row, one-sentence tagline, and a Table of Contents.

**Requirements:** R1, R3

**Dependencies:** U1

**Files:**
- Modify: `README.md` (top of file through end of TOC)

**Approach:**
- Replace the first `# PerCell4` + paragraph block with:
  - Centered `<img src="art/percell4_logo.png" width="200" align="center" alt="PerCell4 logo">` block.
  - `# PerCell4` heading below the logo.
  - One-sentence tagline derived from `pyproject.toml`'s description: "Single-cell FLIM microscopy analysis platform — segmentation, per-cell measurements, and phasor workflows in one Qt app."
  - Static badge row: Python 3.12+ (shields.io static), Platform (macOS/Linux/Windows), License MIT.
- Insert a `## Table of Contents` section with bulleted links to every H2 below. Anchors match GitHub's auto-slugs (lowercase, hyphenated). Verify slugs match — if a heading has parens or special chars, drop them in the anchor.

**Patterns to follow:**
- percell v1 README opens with a centered image, then a badge row, then the tagline. Same shape here.

**Test scenarios:**
- Test expectation: none -- documentation change. Verified by rendering the README on a feature branch via `gh pr create --draft` and visually checking GitHub's render, or by `grip README.md` locally.

**Verification:**
- Every TOC link resolves to a heading in the same file (no 404 anchors).
- Logo renders at ~200px width on GitHub.
- Badges render and are not broken images.

---

- U3. **Add the Tech Stack section**

**Goal:** Communicate the dependency stack at a glance, sourced from `pyproject.toml`.

**Requirements:** R2

**Dependencies:** U2

**Files:**
- Modify: `README.md` (new `## Tech Stack` section, immediately below TOC)

**Approach:**
- Bullet list (definition-list voice) of the major dependencies grouped by role:
  - **GUI:** Qt (PyQt5, qtpy), napari (≥0.5,<0.8), pyqtgraph (≥0.13,<0.15)
  - **Data:** HDF5 (h5py ≥3.10), pandas (≥2.0), pyarrow (≥14)
  - **Imaging:** numpy (≥1.26), scikit-image (≥0.22), scipy (≥1.12), tifffile, sdtfile
  - **Segmentation:** Cellpose (≥3.0,<5.0), scikit-learn
  - **CLI:** click (≥8.1), rich (≥13.0)
  - **Python:** 3.12+
- Version ranges quoted as `>=X,<Y` exactly as in `pyproject.toml` so the README does not drift.
- One terse sentence after the bullet list: "Dependency versions are pinned in `pyproject.toml`."

**Patterns to follow:**
- Tech-stack sections in other Python scientific tools (e.g., napari's own README) lean on grouped bullets, not tables.

**Test scenarios:**
- Test expectation: none -- documentation change. Verified by spot-checking that every listed dependency appears in `pyproject.toml`.

**Verification:**
- No dependency in this section that is absent from `pyproject.toml`.
- No dependency present in `pyproject.toml [project.dependencies]` that is missing from this section (omissions of optional extras are fine).

---

- U4. **Author the Workflow Protocol section (the centerpiece)**

**Goal:** Give a researcher who has never opened the app a single, top-to-bottom protocol covering TIFF loading, naming conventions, and the step-by-step walkthrough including QC.

**Requirements:** R4, R5, R6, R7

**Dependencies:** U3

**Files:**
- Modify: `README.md` (new `## Workflow Protocol` section, immediately below Tech Stack)

**Approach:**
- Open the section with a one-paragraph "what you are about to do" framing: input is a directory of `.tif` files (or `.sdt` for FLIM), output is an HDF5 dataset + parquet/CSV measurements + optional exported TIFFs.
- Optional Mermaid flowchart of the 8 phases. Keep it small enough to render on GitHub mobile.
- `### Loading TIFF files`:
  - Step 1: launch the app (`python main.py` or `percell4-gui`).
  - Step 2: click the **I/O** tab in the launcher.
  - Step 3a (new dataset from TIFFs): click **Import → Compress TIFFs**, point at the input directory, confirm the channel layout in the compress dialog, accept defaults or rename channels. Output: a new `.h5` file.
  - Step 3b (existing HDF5): click **Load** and pick the `.h5`.
  - Step 3c (append FLIM): click **Batch TCSPC append** to add `.sdt` files to an existing `.h5`.
- `### Channel, mask, and segmentation naming`:
  - **Channels** are stored under `/intensity/<channel_name>` and named in `/metadata.channel_names`. The importer enforces a `chNN` prefix (e.g., `ch00`, `ch01`, `ch02`). If you rename channels in the compress dialog, the importer still writes the `ch` prefix. Downstream code (workflow config, threshold-compute) looks up channels by this exact name — do not strip the prefix.
  - **Segmentation layers** live at `/labels/<name>`. Cellpose writes `/labels/cellpose`; QC writes `/labels/cellpose_qc`. Custom segmentations land under `/labels/<your_name>`. The Workflows dialog reads the layer list at config time.
  - **Mask layers** live at `/masks/<name>`. Thresholding rounds write `/masks/<round_name>`. Dilute-phase masks write `/masks/<dilute_name>`. The dilute mask name must not collide with any thresholding round name in the same run (workflow validates at Start).
  - **Workflow round names** become `group_<round>` columns in the output parquet and CSVs. Pick names that read well in downstream pandas code.
  - Worked example block: a 3-channel acquisition (DAPI nuclear, GFP feature, RFP marker) renamed to `ch00_dapi`, `ch01_gfp`, `ch02_rfp` (the `ch` prefix is preserved) with thresholding rounds named `puncta_bright` and `puncta_dim`, optionally a dilute mask named `dilute`.
- `### Step-by-step workflow walkthrough`:
  Numbered phases. For each, the launcher panel/button, what runs unattended, and what QC the researcher sees.
  1. **Compress** (I/O → Import). Source TIFFs → one `.h5` per dataset. Unattended.
  2. **Cellpose segmentation** (Workflows → Single-cell thresholding analysis workflow). Configure once per run; segmentation runs unattended over all datasets in the queue.
  3. **Segmentation QC** (interactive queue, one dataset at a time). The Viewer window opens with the labels overlaid on the chosen channel. Use napari shortcuts to add/remove labels, draw, paint. Press the workflow "Accept" control to advance to the next dataset.
  4. **Grouped thresholding (rounds 1..N)** (unattended within each round). The workflow clusters cells by intensity, applies per-group autothresholding, and writes `/masks/<round_name>`.
  5. **Threshold QC (rounds 1..N)** (interactive queue). For each dataset, the Threshold QC modal opens; review the mask overlay, draw an ROI to refine if needed, accept to advance.
  6. **Dilute-phase mask (optional)** (interactive queue). If enabled in the workflow config, this phase opens the dilute panel per dataset. Run the compute → QC → accept → dilate → NaN-subtract loop for as many rounds as the dataset needs. Accepted condensed mask is dilated and persisted to `/masks/<dilute_name>`.
  7. **Per-cell measurement** (unattended). Reads every `/labels/<seg>` and `/masks/<mask>`, computes per-channel metrics, writes a per-dataset staging parquet.
  8. **Aggregate + export** (unattended). Concatenates staging parquets into `measurements.parquet`, writes `combined.csv` and `per_dataset/<DS>.csv`, plus `summary_groups.csv` and `summary_datasets.csv`. Output lands in `run_<timestamp>/` under the chosen output parent.
  - Tail note: pausing and resuming a run is supported via the **Resume run...** Workflows entry; the workflow writes `run_state.json` after each phase.
- `### Batch TIFF export (CLI)`:
  - One paragraph: "If you only need TIFFs out of an existing `.h5` — for ImageJ, custom downstream code, or sharing — use the headless CLI. The GUI's I/O → Export Images dialog drives the same lens; pick whichever fits your workflow."
  - Link down to the dedicated `## Batch TIFF Export (CLI)` section for the full options reference.

**Patterns to follow:**
- percell v1 README "Typical Use Case" voice: numbered, concrete, panel-by-panel.
- End-to-end workflow requirements doc phase list (R1–R19) for authoritative phase ordering and naming.

**Test scenarios:**
- Test expectation: none -- documentation change. Verified by walking through the protocol with the app open and confirming every named button/panel exists.

**Verification:**
- Every launcher panel name in the walkthrough matches `src/percell4/interfaces/gui/main_window.py:206-216` exactly (I/O, Viewer, Segment, Analysis, FLIM, Scripts, Workflows, Data).
- Every HDF5 path in the naming subsection (`/labels/<name>`, `/masks/<name>`, `/metadata.channel_names`) appears in the codebase.
- The `chNN` prefix claim is consistent with `src/percell4/adapters/importer.py` and the 2026-05-21 solution doc.

---

- U5. **Add the Batch TIFF Export (CLI) section**

**Goal:** Promote the existing CLI block into a top-level section so it is discoverable from the TOC.

**Requirements:** R8

**Dependencies:** U4

**Files:**
- Modify: `README.md` (refactor existing CLI content into a `## Batch TIFF Export (CLI)` section, position it after Workflow Protocol)

**Approach:**
- Lift the current "Batch TIFF export (CLI)" content (invocation, options table, two examples) into the new top-level section.
- Add a one-line cross-reference back to the Workflow Protocol: "See the Workflow Protocol section above for when to reach for this vs the GUI's I/O → Export Images dialog."
- Preserve the options table verbatim — `--output-dir`, `--view-bin`, `--quiet`, `--verbose` — including the descriptive prose about bin lenses (`sum_bin_2d`, `mode_labels`, `majority_vote_mask`).

**Patterns to follow:**
- Existing CLI documentation in the current README (already accurate and tested).

**Test scenarios:**
- Test expectation: none -- documentation change. Verified by running the CLI examples on a known `.h5` after the README change.

**Verification:**
- The CLI invocation in the README matches `python -m percell4.interfaces.cli.batch_export --help` output.
- Both worked examples are syntactically valid shell commands.

---

- U6. **Add the Features section**

**Goal:** A short, scannable inventory of capabilities.

**Requirements:** R9

**Dependencies:** U5

**Files:**
- Modify: `README.md` (new `## Features` section, after Batch TIFF Export)

**Approach:**
- Bulleted list, one short sentence per feature. Cover:
  - HDF5-backed projects (one `.h5` per experiment).
  - Cellpose segmentation with grouped-thresholding interactive QC.
  - FLIM phasor analysis with multi-ROI selection and preview-to-mask.
  - Per-cell measurements across multiple channels with configurable metrics.
  - Multi-window Qt + napari UI synchronized through a shared `CellDataModel`.
  - Batch workflows: end-to-end single-cell pipeline, batch TIFF compression, dataset-wide spatial binning, batch TCSPC append.
  - Image and measurement export (TIFF, CSV/XLSX, parquet).
  - Headless batch TIFF export CLI for downstream pipelines.
  - Dataset lifecycle: import, append, resume, close.
- Keep the section short — no expansion beyond one or two sentences per item. Detailed behavior belongs in the walkthrough and per-module CLAUDE.md.

**Patterns to follow:**
- The current README's "Features" bullets are a reasonable starting voice; preserve and extend.

**Test scenarios:**
- Test expectation: none -- documentation change.

**Verification:**
- Every feature listed corresponds to a real launcher entry, dialog, or CLI command in the current codebase.

---

- U7. **Rewrite Installation as a per-OS section with Linux added**

**Goal:** Three sibling subsections — macOS, Linux, Windows — each self-contained.

**Requirements:** R10, R11

**Dependencies:** U6

**Files:**
- Modify: `README.md` (reorganize existing Install content into `## Installation` with `### macOS`, `### Linux`, `### Windows` subsections)

**Approach:**
- Keep the existing macOS bash block (venv + `pip install -e .`) verbatim under `### macOS`.
- Write a new `### Linux` subsection covering Ubuntu 22.04+ as the primary worked example:
  - System prerequisites: `sudo apt install python3.12 python3.12-venv python3.12-dev build-essential`.
  - Qt/xcb runtime libraries needed by PyQt5: `sudo apt install libxcb-xinerama0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-sync1 libxcb-xfixes0`.
  - Same venv + `pip install -e .` flow as macOS.
  - Note: For headless/SSH use, X11 forwarding or a virtual framebuffer (`xvfb-run`) is required to launch the GUI; the batch-export CLI runs headless without any display.
  - For other distros (Fedora, Arch), point readers at the equivalent system packages without enumerating them: "Use your distro's equivalents for the `python3.12-venv` and `libxcb-*` packages above."
- Preserve the existing Windows section in full, including Command Prompt / PowerShell / Git Bash subsubsections, MSVC redist requirement, CPU-only torch install path, and the entire PyTorch / Cellpose subsubsection. Reorganize headings only where needed to fit under `### Windows`.
- Preserve the `## Install from a wheel` and `## Optional extras` sections as their own H2 sections below `## Installation` — they apply to all OSes and would clutter each per-OS subsection.

**Patterns to follow:**
- Existing Windows install content is the model for thoroughness: name the symptom, name the fix, link to the issue or aka.ms URL.
- percell v1 README per-OS structure (Windows + macOS/Linux as siblings) — here split into three.

**Test scenarios:**
- Test expectation: none -- documentation change. Verified on a clean Ubuntu 22.04 VM (or a teammate's Linux box) before declaring the section done.

**Verification:**
- macOS commands match what currently works on the user's machine (no regressions).
- Linux instructions install percell4 cleanly on Ubuntu 22.04 from a fresh user account.
- Windows instructions are byte-identical to the current README's Windows content (modulo heading depth).

---

- U8. **Preserve Standalone bundle and Troubleshooting sections; finalize License**

**Goal:** Carry forward the PyInstaller bundle section, the Windows troubleshooting list, and the License line so nothing already-documented is lost.

**Requirements:** R12, R13

**Dependencies:** U7

**Files:**
- Modify: `README.md` (last few sections — Standalone bundle, Troubleshooting, License)

**Approach:**
- `## Standalone bundle (PyInstaller)` — preserve verbatim.
- `## Troubleshooting` — preserve the existing Windows triage list verbatim. Add a one-line subhead `### Linux` at the bottom with a placeholder for distro-specific bugs ("If `Qt platform plugin "xcb" not loaded`: install the `libxcb-*` packages from the Linux install section."). macOS troubleshooting is currently empty in the README; leave it that way until specific issues surface.
- `## License` — preserve.

**Patterns to follow:**
- Existing README troubleshooting bullets — one sentence symptom, one or two sentence fix, link to deeper plan when relevant.

**Test scenarios:**
- Test expectation: none -- documentation change.

**Verification:**
- Diff against the current README shows no content lost from the Standalone bundle, Troubleshooting, or License sections.

---

- U9. **Final pass: link audit, heading-slug verification, render check**

**Goal:** Catch broken anchors, mismatched heading slugs, and rendering glitches before merging.

**Requirements:** R3 (TOC integrity)

**Dependencies:** U8

**Files:**
- Modify: `README.md` (any final corrections discovered in the audit)

**Approach:**
- Verify every TOC link resolves: run a local markdown link checker (`markdown-link-check README.md` if available, or eyeball each `#anchor` against the actual H2 headings).
- Render the README in a GitHub preview tool (or push to a draft PR) to confirm:
  - Logo displays at the expected width and centered.
  - Badges render.
  - Mermaid flowchart in the Workflow Protocol section renders (or fall back to a numbered list if Mermaid does not render reliably).
  - Tables (CLI options, optional extras) render.
  - No orphaned heading levels (`### foo` with no parent `##`).
- Cross-check every code path mentioned in the README — `python main.py`, `percell4-gui`, `python -m percell4.interfaces.cli.batch_export ...` — still works from a fresh checkout.

**Patterns to follow:**
- Documentation Rules from project CLAUDE.md: README describes current state only, no history or future.

**Test scenarios:**
- Test expectation: none -- documentation verification, not feature code.

**Verification:**
- Zero broken anchors in the rendered TOC.
- Every command in the README executes on a fresh venv install without further setup.
- A reviewer can read the README top-to-bottom and reach the same mental model of the app as someone who has used it for a month.

---

## System-Wide Impact

- **Interaction graph:** README references the launcher panels (`src/percell4/interfaces/gui/main_window.py`), the importer (`src/percell4/adapters/importer.py`), the workflow runner (`src/percell4/gui/workflows/`), and the CLI (`src/percell4/interfaces/cli/batch_export.py`). If any of these renames a public surface, the README needs an update.
- **Error propagation:** N/A — no code paths changed.
- **State lifecycle risks:** N/A.
- **API surface parity:** The README documents the public CLI surface (`python -m percell4.interfaces.cli.batch_export`). Any change to CLI flags or output layout must be mirrored here.
- **Integration coverage:** N/A.
- **Unchanged invariants:** Channel naming (`chNN` prefix), HDF5 paths (`/labels/<name>`, `/masks/<name>`, `/intensity/<name>`, `/metadata.channel_names`), workflow phase ordering, and the CLI's `--view-bin` / `--output-dir` semantics are documented as they currently exist and are not modified by this plan.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| README drifts from the actual launcher panel names after a future GUI refactor. | Cite the source file path in the plan (`src/percell4/interfaces/gui/main_window.py:206-216`) so the next reviewer can spot-check; add a `docs/solutions/` note if drift becomes recurrent. |
| Linux install instructions are untested on the user's actual Linux environment. | Mark Linux as "Ubuntu 22.04+ tested"; invite issue reports for other distros; do not claim distro coverage we have not verified. |
| Mermaid diagram fails to render on GitHub for some users (mobile clients, GitHub Enterprise). | Keep the diagram small and include a numbered text list immediately below it so the content is intact even if the diagram fails. |
| The logo file at `art/percell4_logo.png` clashes with future asset organization. | `art/` is a conventional repo-root location for README media; cost of moving later is one path update. |
| Channel-naming description in the README diverges from the importer's actual behavior after a future change. | Reference the canonical solution doc (`docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`) so the next change carries the README update. |

---

## Documentation / Operational Notes

- No operational impact (no migrations, no rollout, no monitoring).
- Per-module CLAUDE.md files are unaffected by this change — README is the user-facing front door, CLAUDE.md is the agent-facing guidance.
- If the README ends up substantially longer than 600 lines, consider splitting the Installation section into `docs/installation.md` in a follow-up. Not in scope now.

---

## Sources & References

- Origin: none (direct user request, no upstream brainstorm document).
- Reference: `/Users/leelab/percell/README.md` — structural model for header, TOC, per-OS install.
- End-to-end workflow phases: `docs/brainstorms/2026-05-20-end-to-end-single-cell-workflow-requirements.md`.
- Channel-naming contract: `docs/solutions/logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md`.
- Launcher panel inventory: `src/percell4/interfaces/gui/main_window.py:206-216`.
- Dependency versions: `pyproject.toml`.
- Logo: `/Users/leelab/Downloads/percell_logo.png` (834×834 PNG with alpha) → `art/percell4_logo.png`.
