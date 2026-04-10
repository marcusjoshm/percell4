---
title: "chore: Project folder cleanup and documentation hygiene pass"
type: chore
date: 2026-04-10
---

# chore: Project folder cleanup and documentation hygiene pass

## Overview

A mid-project housekeeping pass on the `percell4` repository. Over the last three
weeks of feature work, the working tree has accumulated test-output CSVs in the
repo root, stray architecture/reference docs at `docs/` root, a large backlog of
untracked brainstorms and plans, and ten fully-merged local feature branches.
`.gitignore` has also fallen behind the kinds of files the app now emits on
disk. This plan catalogues the cleanup and sequences it so nothing useful is
discarded and `main` ends the pass with a clean working tree and an index of
docs that matches the current state of the code.

No runtime code is being changed. This is repo hygiene + documentation pruning
only.

## Problem Statement / Motivation

A scan of `/Users/leelab/percell4` surfaces four classes of rot:

1. **Test-output artifacts at the repo root.** Running the measurement and
   particle export flows during feature testing dropped five CSVs next to
   `main.py`:
   - `measurements.csv` (169 KB)
   - `measurements_largeROI_As.csv` (120 KB)
   - `measurements_smallROI_As.csv` (133 KB)
   - `particles_largeROI_As.csv` (22 KB)
   - `particles_smallROI_As.csv` (18 KB)

   None are tracked, none are reference fixtures, and `.gitignore` does not
   currently exclude `*.csv` or `*.xlsx`, so any similar run pollutes
   `git status` again.

2. **Stray docs floating at the root of the repo or `docs/`.**
   - `microscopy_app_architecture.md` (585 lines, root) — the pre-rebuild
     architecture writeup. Predates `CLAUDE.md` and overlaps with it.
   - `hdf5_storage_guide.docx` (root, binary .docx) — early design reference.
   - `docs/refactor-atomic-state-signals.md` — describes an in-flight refactor
     that has since landed (commit `700a9c0` is on `main`). This is history,
     not current state, and violates the "CLAUDE.md files describe current
     state only — never plans, never history" rule in `CLAUDE.md:39`.
   - `docs/window-interactions.md` — a 17 KB reference guide that belongs
     either next to the code it documents (per-module CLAUDE.md) or in a
     proper `docs/reference/` subtree, not orphaned at `docs/` root.
   - `docs/plans/.Rhistory` — an R REPL history file accidentally saved into
     the plans directory.
   - `.DS_Store` files under root, `docs/`, and `docs/solutions/` (already
     matched by `.gitignore`, but physically present and noisy).

3. **Huge untracked doc backlog.** `git status` shows 17 untracked markdown
   files across `docs/brainstorms/`, `docs/plans/`, and
   `docs/solutions/logic-errors/` that were written during feature work and
   never committed. They are either (a) worth committing as institutional
   history or (b) safe to archive, but letting them sit untracked for weeks
   defeats the purpose of the compound-engineering workflow.

4. **Git environment drift.**
   - 10 local branches (`feat/*`, `fix/*`, `refactor/*`) are all fully merged
     into `main` per `git merge-base` verification — safe to delete.
   - Two stale remote-tracking refs exist
     (`origin/feat/selection-filtering-multi-roi`,
     `origin/refactor/atomic-state-signals`) that should be pruned if they no
     longer exist upstream.
   - `.gitignore` is missing entries for the CSV/XLSX test outputs and
     `.Rhistory` that have shown up during normal development.

5. **Context-poisoning risk in `CLAUDE.md` ecosystem.** The project's own
   feedback memory says: *"lean docs, aggressive archiving, no
   contradictions."* Today two docs (`microscopy_app_architecture.md` and
   `docs/refactor-atomic-state-signals.md`) describe state that is either
   stale or already realised in the code, which is exactly the kind of
   contradiction that poisons future Claude Code sessions. There are also
   no per-module `CLAUDE.md` files under `src/percell4/`, so Claude has to
   rediscover the layout of each subsystem (`gui/`, `io/`, `measure/`,
   `segment/`, `flim/`, `store.py`, `model.py`) from scratch every
   invocation.

6. **README / top-level docs out of sync.** `README.md` uses curly quotes
   (`”`, `’`) in several places — likely the result of an earlier editing
   session — and does not mention the Apr 5–7 features (`ExportImagesDialog`,
   `CompressDialog`, grouped segmentation, per-dataset file list, theme
   refactor) even at a one-line level. A light touch-up is warranted; a full
   rewrite is not.

## Proposed Solution

Six sequential phases, each independently verifiable and reversible. Each
phase ends with a `git status` snapshot and, where relevant, a commit.

### Phase 1 — Delete test-output artifacts

Target files (all repo root, all untracked):

```
measurements.csv
measurements_largeROI_As.csv
measurements_smallROI_As.csv
particles_largeROI_As.csv
particles_smallROI_As.csv
```

Action: `rm` each file. No commit (they are untracked).

Verification: `git status` no longer lists any `*.csv` in the root.

### Phase 2 — Relocate or archive stray docs

| File                                          | Current location   | Disposition |
|-----------------------------------------------|--------------------|-------------|
| `microscopy_app_architecture.md`              | repo root          | Move to `docs/archive/2026-03-25-microscopy-app-architecture.md` and add a one-line "SUPERSEDED — see `CLAUDE.md` and per-module `CLAUDE.md` files" banner at the top. |
| `hdf5_storage_guide.docx`                     | repo root          | Move to `docs/reference/hdf5_storage_guide.docx`. Keep as a reference artifact. |
| `docs/refactor-atomic-state-signals.md`       | `docs/`            | Archive to `docs/archive/2026-03-28-refactor-atomic-state-signals-original.md` (the matching `docs/plans/2026-03-28-refactor-atomic-state-signals-plan.md` is the canonical doc; the refactor shipped in commit `700a9c0`). |
| `docs/window-interactions.md`                 | `docs/`            | Review for accuracy against current `model.py` / `launcher.py`. If still accurate, move to `docs/reference/window-interactions.md`. If stale, archive under `docs/archive/` with an explicit "frozen on YYYY-MM-DD" banner. |
| `docs/plans/.Rhistory`                        | `docs/plans/`      | Delete. |
| `.DS_Store` (root, `docs/`, `docs/solutions/`) | various            | Delete on disk. Already in `.gitignore`. |

Creates two new directories: `docs/archive/` and `docs/reference/`. Each gets a
3–5 line `README.md` explaining its purpose (archive = historical, read-only;
reference = current, non-planning docs).

Commit: `chore(docs): archive superseded architecture docs and create reference/archive trees`.

### Phase 3 — Tame the untracked doc backlog

17 files are currently untracked. Triage by category:

**`docs/brainstorms/` (7 files)** — all follow the convention
`YYYY-MM-DD-<topic>-brainstorm.md`, all correspond to features that have since
shipped or are being actively built. Action: `git add` and commit them as a
batch. They are institutional history and match the workflow the repo uses.
Frontmatter status values vary ("Draft", "decided") — do **not** rewrite
frontmatter, just commit as-is.

**`docs/plans/` (10 files + `.Rhistory`)** — same story. Each plan has a
matching brainstorm and a shipped implementation on `main`. Action: `git add`
and commit.

**`docs/solutions/logic-errors/add-layer-flat-discovery-duplicate-import.md`** —
institutional learning from commit `4ab53a5`. `git add` and commit.

Commit: `docs: commit brainstorm, plan, and solution backlog from recent feature work`.

Verification: `git status` shows a clean working tree (modulo anything created
by later phases).

### Phase 4 — Update `.gitignore`

Add the following entries at the appropriate sections:

```gitignore
# Data files (large, not version controlled)
*.h5
*.sdt
*.ptu
*.bin
*.lif
*.czi

# Test / scratch outputs from running the app
*.csv
*.xlsx
*.xls

# R
.Rhistory
.Rdata
.Rproj.user/
```

Rationale for each new block:

- `*.csv`, `*.xlsx`, `*.xls` — every measurement/particle export the user runs
  from the GUI lands next to the repo root unless they explicitly change the
  directory. Ignoring them globally prevents future recurrence. If the repo
  ever needs a tracked CSV fixture, add an explicit
  `!tests/fixtures/**/*.csv` exception at that time.
- `.Rhistory`, `.Rdata`, `.Rproj.user/` — matches the `docs/plans/.Rhistory`
  already observed.

Verification: run `git check-ignore -v measurements.csv` and similar to prove
each pattern matches.

Commit: `chore(gitignore): ignore scratch CSV/XLSX outputs and R session files`.

### Phase 5 — Clean up git environment

**5a. Delete merged local branches.** All ten local branches below pass
`git merge-base <branch> main == <branch-tip>` — i.e. fully merged:

```
feat/batch-compress-tiff-datasets
feat/grouped-segmentation
feat/image-export
feat/measurement-particle-export-config
feat/selection-filtering-multi-roi
feat/windows-compat
fix/mask-layer-classification
fix/ui-popup-positioning-and-table-colors
refactor/atomic-state-signals
refactor/ui-theme-consistency
```

Action: `git branch -d <branch>` for each (use `-d`, not `-D`, to preserve the
merged-check safety net). **Confirm with the user before running.**

**5b. Prune stale remote-tracking refs.** Two remote refs remain locally
(`origin/feat/selection-filtering-multi-roi`,
`origin/refactor/atomic-state-signals`). Action:
`git fetch --prune origin` — this only deletes refs that no longer exist
upstream, so it is safe.

**5c. Verify.** `git branch -a` should show only `main`, `remotes/origin/main`,
and any remote refs that still legitimately exist.

No commit needed — branch deletions don't touch the tree.

### Phase 6 — Documentation freshness pass

**6a. Update `README.md`.**
- Replace curly quotes (`”` `’`) with straight ASCII quotes. These were
  introduced by an earlier editor/formatter and break copy-paste of code
  blocks on Windows. Affected lines (approximate): `README.md:29`, `:45`,
  `:59`, `:75`.
- Add a one-paragraph "Features" blurb referencing: Cellpose segmentation,
  phasor / FLIM workflows, HDF5-backed projects, batch TIFF compression,
  grouped thresholding, image export, and the multi-window shared-model
  architecture.
- No other changes. Do not rewrite install/troubleshooting sections.

**6b. Audit `CLAUDE.md`.** Confirm it still matches reality:
- Tech stack line is still correct.
- Architecture paragraph still matches `model.py` + `launcher.py`.
- "Previous Versions" paths still resolve.

No edits expected — this is a read-only verification step unless drift is
found.

**6c. Add per-module `CLAUDE.md` stubs under `src/percell4/`.** These are the
antidote to the "rediscovery tax" the project feedback memory warns about.
Create short (≤ 40 lines each), current-state-only files at:

```
src/percell4/CLAUDE.md          — package overview + entry points
src/percell4/gui/CLAUDE.md      — launcher, viewer, dialogs, theme
src/percell4/io/CLAUDE.md       — dataset discovery, HDF5, batch compress
src/percell4/measure/CLAUDE.md  — per-cell metrics, multi-ROI measurement
src/percell4/segment/CLAUDE.md  — Cellpose wrapper + grouped thresholding
src/percell4/flim/CLAUDE.md     — phasor computation
```

Each file must follow the project rule in `CLAUDE.md:37-40`:
*"describe current state only — never plans, never history."* Content is
derived by reading the module, not by copying plans. If a subsystem is
trivial (e.g. `cli/`, `plugins/`), skip rather than invent content.

**6d. Assess `docs/window-interactions.md` accuracy** (carried over from
Phase 2 if not yet done). Cross-reference its signal descriptions with the
current `CellDataModel` in `src/percell4/model.py`. If it describes
pre–`state_changed`-signal architecture, either update the relevant sections
to match the post-refactor state or archive it.

Commit: `docs: refresh README, add per-module CLAUDE.md stubs`.

## Technical Considerations

- **Archive vs. delete.** Default to archive (with a dated filename and a
  "SUPERSEDED" banner) rather than delete for any doc containing non-trivial
  design discussion. Deletion is reserved for mechanically-reproducible files
  (CSV exports, `.DS_Store`, `.Rhistory`, `.Rdata`).
- **Do not auto-commit binary `.docx`.** `hdf5_storage_guide.docx` is already
  tracked — leave its tracked status alone. Only `git mv` it.
- **Per-module `CLAUDE.md` discipline.** It is easy to violate the "current
  state only" rule by pasting from plans. If unsure about a section, leave it
  out — the file can be grown later. Shorter is better than speculative.
- **Branch deletion safety.** Use `git branch -d` (lower-case) so git refuses
  to delete anything not fully merged. Never `-D` in this pass.
- **`git fetch --prune` is non-destructive locally** but will show extra output
  for each pruned ref. Record the list in the final report.
- **Contradictions are the real enemy.** The cleanup is only successful if, at
  the end, there is exactly one source of truth for: (a) the runtime
  architecture (`CLAUDE.md` + per-module `CLAUDE.md`), (b) current feature
  plans (`docs/plans/`), and (c) historical decisions (`docs/archive/` +
  `docs/solutions/`). Anything that duplicates those trees should be moved
  into them, not left at `docs/` root.

## Acceptance Criteria

- [ ] No `*.csv` or `*.xlsx` files in the repo root
- [ ] `hdf5_storage_guide.docx` relocated under `docs/reference/`
- [ ] `microscopy_app_architecture.md` archived under `docs/archive/` with a
      SUPERSEDED banner
- [ ] `docs/refactor-atomic-state-signals.md` archived under `docs/archive/`
- [ ] `docs/window-interactions.md` either updated-and-moved to
      `docs/reference/` or archived
- [ ] `docs/plans/.Rhistory` removed
- [ ] `docs/archive/README.md` and `docs/reference/README.md` exist and explain
      each directory in 3–5 lines
- [ ] All 17 currently-untracked docs are either committed or explicitly
      archived; `git status` shows a clean working tree
- [ ] `.gitignore` contains new entries for `*.csv`, `*.xlsx`, `*.xls`,
      `.Rhistory`, `.Rdata`, `.Rproj.user/`
- [ ] `git check-ignore -v measurements.csv` confirms the new rule matches
- [ ] All 10 merged local branches deleted; `git branch` shows only `main`
- [ ] `git fetch --prune origin` run; stale remote refs gone
- [ ] `README.md` has straight ASCII quotes throughout
- [ ] `README.md` includes a one-paragraph Features blurb
- [ ] Per-module `CLAUDE.md` stubs exist for `gui/`, `io/`, `measure/`,
      `segment/`, `flim/` (and the top-level `src/percell4/` package), each
      ≤ 40 lines and current-state-only
- [ ] Top-level `CLAUDE.md` reviewed and confirmed still accurate (or updated
      if not)
- [ ] Three commits landed on `main` (Phase 2, Phase 3, Phase 4, Phase 6) —
      four total, one per logical cleanup
- [ ] No runtime behaviour changed; `python main.py` still launches the app

## Success Metrics

- `git status` on a clean checkout returns zero untracked/modified files.
- `git branch` shows a single local branch.
- `grep -rIl "TODO\|FIXME" docs/` returns zero matches (none were found at
  plan time — maintain the clean state).
- Any future Claude Code session started in this directory has `CLAUDE.md`
  + per-module `CLAUDE.md` as its only source of truth for architecture, with
  zero contradictions loaded into context.

## Dependencies & Risks

- **Risk: deleting something the user wanted.** The CSVs look like
  test output based on their filenames (`*_largeROI_As.csv`, etc.) but
  should be confirmed with the user before `rm`. Archive-first is always
  cheaper than regret.
- **Risk: archiving a still-canonical doc.** If `docs/window-interactions.md`
  is still the *only* place that explains the signal topology, archiving it
  loses information. Mitigation: Phase 6d's accuracy check must happen
  *before* the archive move, not after. If the doc is still correct, it
  moves to `docs/reference/` rather than `docs/archive/`.
- **Risk: per-module `CLAUDE.md` drift.** The whole point of these files is
  to stay current. If they are written sloppily (copy-paste from plans), they
  become the next context-poisoning source. Mitigation: keep each file tiny,
  derived from the code, and treat the "current state only" rule as load-bearing.
- **Risk: committing the untracked plan backlog changes the project's git
  history cosmetics.** Not a real risk — the plans are text files, not code —
  but worth noting that the commit in Phase 3 will be large.
- **Dependency: user confirmation** before Phase 1 (rm CSVs) and Phase 5a
  (branch deletion). Both are recoverable (git reflog for branches, local
  filesystem snapshots for CSVs) but deserve explicit sign-off.

## References & Research

### Current repo state (plan-time snapshot)

- Root directory listing: 5 CSV files, 1 DOCX, 1 stray MD — see Problem
  Statement §1–§2
- `git status --short` at plan time: 26 untracked entries across
  `docs/brainstorms/`, `docs/plans/`, `docs/solutions/logic-errors/`, and
  repo root
- Branch audit: all 10 feature/fix/refactor branches return empty from
  `git branch --no-merged main`, and each has
  `git merge-base <b> main == git rev-parse <b>`
- `.gitignore` current contents: `/Users/leelab/percell4/.gitignore:1-44`

### Internal conventions

- Project rule: `CLAUDE.md:37-40` — "Per-module CLAUDE.md files describe
  current state only — never plans, never history. Archive brainstorms and
  planning docs immediately after implementation. Active docs contain ONLY
  what IS, not what WAS or MIGHT BE."
- User feedback memory (persisted):
  `feedback_context_poisoning.md` — "lean docs, aggressive archiving, no
  contradictions"
- Filename convention for plans: `YYYY-MM-DD-<type>-<descriptive-name>-plan.md`
  (this plan follows it)
- Filename convention for brainstorms: `YYYY-MM-DD-<topic>-brainstorm.md`

### Related recent commits

- `bc7e0ac feat(gui): show dataset filename in napari viewer title bar`
- `546a517 fix(gui): raise threshold QC popups above viewer`
- `58de361 docs: compound lessons from UI theme refactor and session fixes`
- `700a9c0 refactor(model): add StateChange dataclass and state_changed signal`
  — proves `refactor/atomic-state-signals` landed, justifying archiving the
  stray `docs/refactor-atomic-state-signals.md`

### External references

- `git merge-base` semantics:
  https://git-scm.com/docs/git-merge-base
- `git fetch --prune` semantics:
  https://git-scm.com/docs/git-fetch#Documentation/git-fetch.txt---prune
