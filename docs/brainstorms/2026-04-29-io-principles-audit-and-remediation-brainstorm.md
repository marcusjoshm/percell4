---
date: 2026-04-29
revised: 2026-04-30
topic: codebase-audit-canonical-sources-and-conventions
supersedes_title: "I/O Principles Audit & Remediation Initiative"
---

# Codebase Audit: Canonical Sources, Convention Drift & I/O Principles

> **Revision note (2026-04-30).** Originally framed as an I/O-only audit against seven principles. After Thread 1 (TCSPC append + cross-format token matching) merged at `97ae037`, lived experience reframed the audit. The seven I/O principles are preserved as a *secondary* rubric scoped to the I/O slice; the *primary* axis is now codebase-wide canonical-source reuse and convention drift. The original framing is preserved structurally and called out where it survives.

## Problem Frame

Two compounding bugs are the real target:

1. **Content gap in `docs/solutions/`.** Past learnings get written but lack the frontmatter predicates that would make them retrievable against future task contexts (`canonical_source`, `applies_to`, `duplicates_at`). Some behaviors the user has had to instruct repeatedly — e.g., "use `QScrollArea` when a dialog can grow taller than the screen" — aren't compounded at all.
2. **Retrieval gap in the agent loop.** Even well-tagged entries don't fire because `ce-learnings-researcher` isn't reliably invoked at the start of new implementations. Compounding closes only at write time, not at read time.

The downstream symptom: identical features get re-implemented from scratch in new locations, miss prior fixes, ignore conventions, and create inconsistencies that produce real bugs. Concrete cases:

- Scrollbar pattern. The user has had to issue an explicit "add a `QScrollArea` when this gets too tall" instruction throughout the project's lifetime. Never compounded.
- Channel deletion permanence. Thread 1 surfaced that the Data tab deleted channels in-memory only; the persisted-write path that *did* exist for compress wasn't reused — it was re-derived (commits `06221ec`, `1e5b30f`, `60b20bb`).
- Decay-write path divergence. Thread 1's add-layer use case had to be aligned with compress's existing decay-write semantics in five separate commits (`e25831a`, `37dd603`, `392f66d`, `f31a970`, `c25950e`). The canonical write path existed; the audit's lens would have caught the duplication before it shipped.
- Five-vector in-session HDF5 staleness compound (`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`) — five independent caching layers all needed write-time invalidation. Adjacent prior learnings existed and didn't fire.

This initiative defines a **process** to: (a) audit the codebase for canonical-source drift and convention inconsistency, (b) inside the I/O slice, also run the seven-principles compliance pass, (c) enrich `docs/solutions/` with the frontmatter that makes retrieval work, (d) author missing entries for repeatedly-instructed behaviors, and (e) ensure `ce-learnings-researcher` actually fires at the start of new implementation work in audited modules.

The seven I/O principles from `docs/ideation/2026-04-29-io-principles-ideation.md` are stable and remain the rubric inside the I/O slice. They are not the primary axis anymore.

---

## Actors

- A1. **User (Lee Lab researcher)**: Provides seed instructions and seed canonical sources from lived pain (scrollbar, decay-write, channel deletion, staleness invalidation, etc.); reviews matrix findings; picks next thread; reviews thread plans before implementation.
- A2. **AI auditor (codebase-wide reuse pass)**: Mines `docs/solutions/` for candidates; greps T1 modules for duplicates and drift against each canonical source; flags missing solution entries for behaviors the user has named; populates the reuse matrix with evidence-backed findings; surfaces threads.
- A3. **AI auditor (I/O sub-rubric pass)**: Inside the I/O slice only, runs the seven-principles rubric per file × per principle; populates the I/O sub-matrix; surfaces I/O-specific threads.
- A4. **AI planner (`/ce-plan`)**: Takes one thread + relevant matrix cells (reuse or I/O sub-rubric) as input; produces an implementation plan that retires those cells.
- A5. **PerCell4 codebase**: The subject of the audit; receives PRs that close matrix cells and reference enriched solution slugs in their commit messages.
- A6. **Agent workflow / project config**: The retrieval mechanism. Receives the change that ensures `ce-learnings-researcher` fires at task start in T1 modules.

---

## Key Flows

- F1. **Codebase-wide reuse audit pass**
  - **Trigger:** User runs the audit (likely via `/ce-work` or a one-shot agent dispatch).
  - **Actors:** A1, A2.
  - **Steps:**
    1. User provides their seed list of canonical sources and behaviors they've had to instruct repeatedly.
    2. AI mines `docs/solutions/` for additional canonical-source candidates.
    3. AI runs the reuse-axis pass on T1 modules: for each candidate canonical source, grep T1 files for callsites, classify each as `consumes_canonical` / `re_implements` / `drifts_from_canonical` / `n/a`.
    4. AI flags **gaps**: behaviors the user named that have no `docs/solutions/` entry yet (those become "must-author" entries, not matrix cells).
    5. AI writes the reuse matrix to `docs/audits/canonical-sources-matrix.yaml` (one row per T1 file, one column per canonical source); a render script `scripts/render_canonical_sources_matrix.py` produces a human-readable Markdown view.
    6. AI proposes thread groupings — one thread per "consolidate behavior X across N drift sites."
  - **Outcome:** Reuse matrix exists; gaps are listed; threads are named with rough scope tags.
  - **Covered by:** R1, R2, R3, R4, R5, R6, R7.

- F2. **I/O principles sub-rubric pass** (scoped to I/O slice only)
  - **Trigger:** Same audit invocation as F1; this pass runs in parallel.
  - **Actors:** A1, A3.
  - **Steps:**
    1. Same seven-principle rubric the original brainstorm defined, scoped only to I/O T1 files.
    2. AI populates the I/O sub-matrix at `docs/audits/io-principles-matrix.yaml` (one row per I/O T1 file × seven principle columns; cells are `ok` / `violation` / `partial` / `n/a` with evidence).
    3. Threads from F2 may overlap with threads from F1 — when they do, one thread closes both matrices.
  - **Outcome:** I/O sub-matrix exists; principle compliance per I/O file is visible.
  - **Covered by:** R8, R9.

- F3. **Thread selection and planning**
  - **Trigger:** User picks the next thread from the matrix output.
  - **Actors:** A1, A4.
  - **Steps:** Per the original brainstorm — invoke `/ce-plan` with the thread name and the matrix cells (reuse or I/O sub-rubric, or both) it claims to retire.
  - **Outcome:** Thread plan exists.
  - **Covered by:** R10, R11.

- F4. **Thread implementation and matrix update**
  - **Trigger:** PRs from a thread plan land on `main`.
  - **Actors:** A1, A5.
  - **Steps:**
    1. Each PR explicitly cites which matrix cells (reuse or I/O sub-rubric) it retires, by `docs/solutions/` slug + duplicate id, and by I/O matrix cell key when relevant.
    2. When the last PR of a thread merges, both matrices are updated to mark those cells `ok`.
    3. If new drift / new violations are surfaced during implementation, both matrices update in the same PR.
    4. **In the same PR**, the affected `docs/solutions/` entry's `duplicates_at` field is updated; `canonical_source` is finalized if it was previously `TBD`.
  - **Outcome:** Matrices and `docs/solutions/` reflect current reality.
  - **Covered by:** R12, R13, R14.

- F5. **Retrieval automation rollout**
  - **Trigger:** F1 / F2 expose enriched `docs/solutions/` entries; the agent workflow needs to consume them at task start.
  - **Actors:** A1, A6.
  - **Steps:**
    1. Pick a retrieval mechanism (Claude Code hook, project-level instruction in `AGENTS.md`/`CLAUDE.md`, or skill-prompt modification — settled in planning, not here).
    2. Land the configuration change.
    3. Verify the mechanism fires by starting a new implementation task in a T1 module and checking `ce-learnings-researcher` runs (or whatever equivalent is settled on).
    4. Track over the next 2–3 implementation tasks whether the user still has to issue previously-canonical instructions. If yes, the mechanism is wrong; iterate.
  - **Outcome:** Retrieval reliably fires in T1 modules; the user stops re-instructing canonical behaviors.
  - **Covered by:** R15, R16.

---

## Requirements

**Audit pass (codebase-wide reuse)**

- R1. The audit MUST be **user-seeded + AI-completes**: user provides seed canonical sources from lived pain; AI mines `docs/solutions/` for additional candidates; AI does an independent grep pass to find duplicates and drift.
- R2. **Tiered scope**:
  - **T1 (must audit, drives both matrices):**
    - I/O slice — `src/percell4/domain/io/` (incl. `cross_format.py`), `src/percell4/adapters/importer.py` (canonical home of `import_dataset()` — there is no use-case wrapper), `src/percell4/adapters/readers.py` (where `.sdt` / `.bin` readers actually live), `src/percell4/store.py`, `src/percell4/application/use_cases/{export_images,compute_phasor,add_decay_to_dataset}.py`, `src/percell4/interfaces/gui/main_window.py::_run_batch_compress` (canonical home of batch-compress orchestration — there is no `batch_compress.py` use case).
    - All `src/percell4/gui/*Dialog.py` (`import_dialog.py`, `add_layer_dialog.py`, `compress_dialog.py`, `export_images_dialog.py`, `gui/workflows/single_cell/config_dialog.py`).
  - **T2 (audit-as-needed):** session/state (`src/percell4/application/session.py`, `src/percell4/model.py`), peer views (`src/percell4/interfaces/gui/peer_views/`), task panels (`src/percell4/interfaces/gui/task_panels/`), threading conventions (QThread workers across the codebase), `src/percell4/project.py`, `src/percell4/domain/io/assembler.py`.
  - **T3 (out of scope):** pure-domain compute that does not open files or render UI (`src/percell4/domain/measure/`, `src/percell4/domain/segmentation/`, `src/percell4/domain/flim/phasor.py`).
- R3. The codebase-wide pass MUST cover **every canonical-source candidate** for each in-scope file. A column may be `n/a` for a file (the canonical source doesn't apply there), but the cell MUST exist with a one-line reason.
- R4. Each `re_implements` / `drifts_from_canonical` cell MUST cite **specific evidence**: a function name, line range, or quoted code snippet. Heuristic-only flags are not accepted.
- R5. The audit MUST surface **gaps**: behaviors the user named that have no `docs/solutions/` entry yet. Gaps are not matrix cells; they are listed in `gaps:` in the matrix YAML and produce "must-author" tasks. The first audit pass MUST author entries for every gap before declaring complete.

**Audit deliverables (the registry IS `docs/solutions/`)**

- R6. The reuse-axis matrix lives at `docs/audits/canonical-sources-matrix.yaml`, keyed by `<file_path>.<canonical_source_slug>`, with cells carrying `status` (`consumes_canonical` / `re_implements` / `drifts_from_canonical` / `n/a`), `evidence`, `thread`, `notes`. A render script `scripts/render_canonical_sources_matrix.py` produces a human-readable Markdown view.
- R6b. The matrix YAML MUST include a `threads:` section (slug, one-line description, cited cell keys, status).
- R7. **`docs/solutions/` frontmatter MUST be enriched** for every entry that names a canonical source. New required fields:
  - `canonical_source`: file path of the single canonical implementation, OR `TBD` if pre-canonical, OR `n/a` if the entry is not about reusable code.
  - `applies_to`: list of file globs / module names / task triggers that should retrieve this entry. Used by `ce-learnings-researcher` for matching.
  - `duplicates_at`: list of currently-drifted callsites (`{path: ..., note: ...}`). Updated as drift retires.
  - `status`: `canonical_clean` / `has_drift` / `pre_canonical` / `superseded`.
  Existing fields (`title`, `date`, `category`, `module`, `tags`, etc.) are preserved.
- R7b. Adding the new fields MUST be backward-compatible — existing tooling that reads `docs/solutions/` frontmatter must keep working. The audit verifies this on first pass.

**I/O sub-rubric (preserves the original brainstorm's seven-principles work)**

- R8. Inside the I/O slice only, the audit MUST also run the seven-principles rubric per file × per principle (the original R3 of this doc). Principles and ideation source: `docs/ideation/2026-04-29-io-principles-ideation.md`.
- R9. The I/O principles sub-matrix lives at `docs/audits/io-principles-matrix.yaml`, with the schema and conventions from the original brainstorm: `<file_path>.<principle_number>` keys, `status` enum (`ok` / `violation` / `partial` / `n/a`), `evidence`, `thread`, `notes`. A render script produces the Markdown view.
- R9b. **Audit baseline.** Both audits run against `HEAD` of the branch the audit PR is opened on — NOT the working tree. (Preserves original R5b.)

**Remediation grouping**

- R10. Remediation MUST be organized by **user-pain threads**, not by canonical source or principle. One thread MAY close cells in both matrices.
- R11. Subsequent threads MUST be proposed by the AI auditor in the matrix YAMLs' `threads:` sections, each with a one-paragraph rationale citing user-pain signal, cited cell count across both matrices, and complexity bucket. The auditor proposes a recommended ordering with reasoning; the user picks.
- R11b. **Thread scope cap.** Original heuristic was ~10 cited matrix cells. Thread 1 retro showed this was too tight — Thread 1 grew to ~30+ commits when the staleness vectors are counted. Revised cap: **~15 cells across both matrices**, with the user's explicit override allowed when a coherent user-pain thread justifies more. Re-evaluate after the next 2–3 threads land.

**PR conventions**

- R12. Every PR that lands a fix MUST cite which matrix cells it retires. Format:
  - For reuse-matrix cells: `Closes drift: <solution-slug>#<duplicate-id>` (e.g., `Closes drift: in-session-hdf5-staleness-multi-vector#peer-views-on-dataset-switch`).
  - For I/O sub-matrix cells: `Closes I/O matrix: <file>.<principle>` (e.g., `Closes I/O matrix: store.py.4`).
  - When both apply, both lines are present.
- R13. The matrix YAMLs MUST be updated **in the same PR** that retires their cells — never a follow-up PR.
- R14. The affected `docs/solutions/` entry's `duplicates_at` field MUST be updated in the same PR; if a `canonical_source: TBD` is being finalized, that update lands in the same PR too.

**Retrieval automation**

- R15. The audit MUST produce a process change that ensures `ce-learnings-researcher` is invoked at the start of any non-trivial implementation in a T1 module. Mechanism is settled in planning (Claude Code hook, `AGENTS.md`/`CLAUDE.md` instruction, or skill-prompt modification — see Outstanding Questions).
- R16. The retrieval mechanism MUST be testable: starting a new implementation task in a T1 module with a known canonical-source applicability MUST surface the matching `docs/solutions/` entry before code is written. Verification protocol: track 2–3 subsequent implementation tasks; if the user still has to issue previously-canonical instructions, the mechanism is wrong and is iterated on as a follow-up.

**Tests**

- R17. **Audit-tier for tests.** T1 includes I/O test files: `tests/test_io/` (incl. `test_importer.py`, `test_scanner.py`, `test_assembler.py`, `test_readers.py`, `test_cross_format.py`, `test_store_append.py`), `tests/test_store.py`, `tests/test_add_decay_to_dataset.py`, `tests/test_use_cases.py`, and `tests/test_gui_workflows/test_*dialog*`. Test files can themselves encode duplicated logic and convention violations (hardcoded fixture paths, dialog test scaffolding that re-derives sizing instead of using a canonical helper). Tests are not exempt from either matrix. (Preserves original R13.)

---

## Acceptance Examples

- AE1. **Covers R1, R3, R4, R5.** Given the user has provided seed canonical sources (scrollbar pattern, decay-write path, channel-deletion-permanence, dataset-switch staleness invalidation), when the AI runs F1, the reuse matrix MUST include those seeds as columns AND additional canonical-source candidates the AI mined from `docs/solutions/`. Each `re_implements` / `drifts_from_canonical` cell cites a function name or line range. Behaviors the user named that lack a `docs/solutions/` entry (e.g., scrollbar) appear in `gaps:` and the first audit pass authors entries for them.

- AE2. **Covers R6, R7, R13, R14.** Given the matrix YAMLs exist and a thread that consolidates "scrollbar use across dialogs" is in progress, when its last PR merges, the same PR (a) transitions reuse-matrix cells from `re_implements` to `consumes_canonical`, (b) updates `dialog-scroll-when-tall.md`'s `duplicates_at` to remove the migrated callsites, (c) regenerates the rendered Markdown view (or commits a follow-up that does so).

- AE3. **Covers R8, R9, R10.** Given the I/O sub-rubric pass runs alongside F1, when a thread spans both matrices (e.g., "consolidate decay-write across compress + add-layer + export" closes both reuse-matrix cells *and* I/O sub-matrix cells for principle #4 + #5), the thread plan's acceptance criteria explicitly call out cells in both matrices.

- AE4. **Covers R15, R16.** Given the retrieval mechanism is in place, when the user starts a new task that touches `gui/some_dialog.py`, `ce-learnings-researcher` (or its successor) fires automatically and surfaces `dialog-scroll-when-tall.md` (and any other applicable entries) before code is written. The user does NOT have to issue the scrollbar instruction.

- AE5. **Covers R11.** Given F1 + F2 complete and propose Threads 2–N, when the user has not picked Thread 2, no plan exists for it; the matrix docs are sufficient.

---

## Success Criteria

**Human outcome**
- The user can answer "is the codebase consistent with canonical implementation X?" by reading one row of one matrix.
- The user STOPS having to issue explicit instructions for behaviors that have a canonical source (the named pain — scrollbar, dialog conventions, decay-write reuse, etc.).
- New implementations in T1 modules consume the canonical source by default; deviations are deliberate, not accidental.
- Every concrete pain the user names becomes a thread; thread completion makes the pain go away.
- The seven I/O principles remain auditable inside the I/O slice — the original goal is preserved.

**Downstream-agent handoff quality**
- The audit pass is **structurally** repeatable, not byte-repeatable: a fresh agent invocation given this doc + the matrices + `docs/solutions/` can re-run the audit on a future commit and produce comparably-shaped matrices that diff cell-by-cell.
- Both audits run against `HEAD` of the branch the audit PR is opened on (per R9b).
- `/ce-plan` invocations against a thread can produce a plan **without inventing scope** — the thread slug + cited cells in both matrices uniquely identify what to do.
- `git log --grep "Closes drift:"` enumerates which canonical-source drifts retired in which commits; `git log --grep "Closes I/O matrix:"` does the same for the I/O sub-matrix.
- `ce-learnings-researcher` reliably fires at task start in T1 modules — measurable by absence of repeat-instruction events from the user.

---

## Scope Boundaries

- **Not in scope: brainstorming any single principle.** That belongs in separate ce-brainstorm sessions if needed when a thread requires deeper definition.
- **Not in scope: changing the seven I/O principles.** They were settled in the prior ideation. If a thread surfaces evidence a principle is wrong, that's an out-of-band update to the ideation doc.
- **Not in scope: T3 modules.** Pure-domain compute is excluded from both matrices.
- **Not in scope: a formal "done" date.** The initiative is rolling; the matrices live; threads close as priority allows.
- **Not in scope: lint/CI enforcement automation in v1.** v1 relies on the matrices as living documents, PR-description citations, and the retrieval mechanism. Lint rules are future work.
- **Not in scope: refactoring code to consolidate duplicates *during* the audit pass.** The audit flags drift; threads consolidate.
- **Not in scope: choosing the exact retrieval mechanism.** R15 mandates the requirement; planning chooses Claude Code hook vs. `AGENTS.md`/`CLAUDE.md` instruction vs. skill modification.
- **Not in scope (removed in revision):** ~~"Not in scope: porting principles to non-I/O code."~~ The audit IS now codebase-wide. The original constraint was lifted on 2026-04-30.

---

## Key Decisions

- **Codebase-wide reuse axis as primary; seven I/O principles as secondary sub-rubric.** The user's lived experience implementing Thread 1 showed that the deepest pain is logic re-implementation and convention drift, not principle compliance. Principles still apply inside I/O; they are no longer the top-level frame.
- **The registry IS `docs/solutions/`.** No new artifact. Enrich frontmatter (`canonical_source`, `applies_to`, `duplicates_at`); fill gaps with new entries. Reuses the existing `ce-learnings-researcher` retrieval pipeline so the audit's content investment pays off at retrieval time.
- **Audit fixes both content AND retrieval.** R5/R7 close the content gap; R15/R16 close the retrieval gap. Closing only one leaves the other failure mode intact.
- **User-seeded + AI-completes.** Same hybrid as the original brainstorm; broadened so seeds come from user pain plus `docs/solutions/` mining.
- **Per-file × per-canonical-source matrix as source of truth for reuse.** Granularity matches how drift actually distributes (one file can drift from multiple canonicals; one canonical drifts at multiple files).
- **Per-file × per-principle I/O sub-matrix preserved.** Original schema intact for I/O slice; merges into the same `threads:` mechanism so nothing splits artificially.
- **Thread-grouped remediation, matrix cells as currency.** A real fix often closes drift on multiple canonicals at once, sometimes also touching I/O principle cells. Threads are the natural unit; cells are the receipts.
- **First thread NOT pre-selected.** Original brainstorm pre-selected TCSPC append; that thread is closed at `97ae037`. The next first thread is picked from the audit output by the user, not pre-committed here.
- **Three-doc artifact set.** Brainstorm doc (this) + reuse matrix YAML + I/O sub-matrix YAML + per-thread plans. `docs/solutions/` entries are the canonical-source backing store.
- **Citation convention shifts.** Original brainstorm used `Closes matrix cells: ...`. Thread 1's PRs in fact used `U1/U2/U3/U4`. The revised convention is `Closes drift: <solution-slug>#<duplicate-id>` for reuse cells and `Closes I/O matrix: <file>.<principle>` for principle cells. PR descriptions can carry both lines.

---

## Dependencies / Assumptions

- The seven I/O principles in `docs/ideation/2026-04-29-io-principles-ideation.md` are stable.
- `ce-learnings-researcher` exists and works (verified — listed in current available skills as `compound-engineering:ce-learnings-researcher`). Its retrieval pipeline reads `docs/solutions/` frontmatter.
- `docs/solutions/` frontmatter schema can absorb new fields (`canonical_source`, `applies_to`, `duplicates_at`, `status`) without breaking existing tooling. Verify in the first audit PR; if a downstream tool breaks, reshape the schema.
- `docs/audits/` does not yet exist; the audit pass creates it.
- Some seed file paths in the current codebase have shifted since the original brainstorm: `src/percell4/flim/` is empty (`.sdt` / `.bin` readers actually live in `src/percell4/adapters/readers.py`); new files exist (`domain/io/cross_format.py`, `application/use_cases/add_decay_to_dataset.py`); there is no `application/use_cases/import_dataset.py` (the canonical entry point is `adapters/importer.py::import_dataset`); there is no `application/use_cases/batch_compress.py` (orchestration lives at `interfaces/gui/main_window.py::_run_batch_compress`). R2 reflects current paths.
- No new dependencies introduced by this initiative itself. Individual threads may add some.

---

## Outstanding Questions

### Resolved Before Planning (some via Thread 1 retrospective)

- ~~Token-convention reconciliation: hardcoded vs configurable?~~ **Settled** by Thread 1 — per-channel `.bin`-token override dropdown.
- ~~Pre-selected first thread (TCSPC append)?~~ **Settled** — Thread 1 merged at `97ae037`.
- Reuse axis vs principle axis as primary frame? **Settled in this revision.**
- New artifact vs `docs/solutions/`-as-registry? **Settled in this revision** — `docs/solutions/` IS the registry.
- Audit fixes content gap, retrieval gap, or both? **Settled in this revision** — both.

### Resolve Before Planning

- *(none — initiative shape is settled enough to invoke `/ce-plan` for the first new thread once the audit's matrix output exists)*

### Deferred to Planning

- **[Affects R6][Technical]** Schema for `docs/audits/canonical-sources-matrix.yaml` — exact key shape, `evidence` field structure, and the render script's output style — settled by the first audit PR.
- **[Affects R9][Technical]** Schema for `docs/audits/io-principles-matrix.yaml` — same shape question for the I/O sub-matrix; the original brainstorm flagged this and it remains open.
- **[Affects R7][Technical]** Exact frontmatter shape for `applies_to` — file globs, module dotted paths, task-trigger keywords, or all three. The first solution-doc enrichment PR settles the shape.
- **[Affects R12][Technical]** PR-description grep convention — exact format of the `Closes drift:` and `Closes I/O matrix:` lines so `git log --grep` works cleanly. First thread's first PR fixes the format.
- **[Affects R15][User decision + technical]** The retrieval mechanism — Claude Code hook (auto-fires `ce-learnings-researcher` on tool-call patterns), project-level `AGENTS.md`/`CLAUDE.md` instruction (less reliable, no enforcement), or skill-prompt modification (e.g., bake retrieval into `/ce-work` and `/ce-plan` openings). Trade-off space settled in planning.
- **[Affects R11b][User decision]** Thread-scope cap exact threshold — revised heuristic ~15 cells; tune after next 2–3 threads.

---

## Thread 1 Retrospective (2026-04-30)

What Thread 1 (TCSPC append + cross-format token matching, `97ae037`) revealed that this revision incorporates:

- **Reuse failures dominate principle violations as the source of pain.** Five of Thread 1's commits were aligning add-layer's decay-write path with compress's existing path (`e25831a`, `37dd603`, `392f66d`, `f31a970`, `c25950e`). The canonical implementation existed; it just wasn't reused. This is exactly the bug class the reuse axis is built to catch.
- **Convention drift slips in alongside feature work.** Scrollbar / dialog sizing (`6adde5a`), per-channel token override (`3beb964`, `c8875f3`), screen-bounded dialog height — none had compounded learnings, all were instructed in-thread.
- **In-session staleness is a 5-vector compound** (`docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`). Adjacent prior learnings existed (snapshot patterns, mask cache invalidation) but did not fire when the staleness fixes were being designed. The retrieval gap directly caused the compounding-into-five-vectors shape of the bug.
- **Channel-deletion permanence** (`06221ec`, `1e5b30f`, `60b20bb`) was a re-discovery of already-canonical write semantics. Same bug class.
- **The original ~10-cell thread cap is too tight.** Thread 1's effective cell count (counting reuse drifts, principle violations, AND staleness vectors) exceeded 15. The revised heuristic in R11b is 15.
- **PR citation convention drifted.** Thread 1's PRs cited `U1/U2/U3/U4` (implementation-unit slugs), not the brainstorm's `Closes matrix cells: ...`. The revision in R12 settles a workable convention going forward.
- **File paths in the original R2 were partially wrong.** `src/percell4/flim/` is empty; `.sdt`/`.bin` readers live in `src/percell4/adapters/readers.py`. New files (`domain/io/cross_format.py`, `application/use_cases/add_decay_to_dataset.py`) didn't exist when the brainstorm was written. R2 is corrected.

---

## Next Steps

→ Run the audit (F1 codebase-wide reuse + F2 I/O sub-rubric, in parallel). Output: both matrix YAMLs + `gaps:` list + thread proposals.
→ User picks the next thread.
→ Invoke `/ce-plan` for that thread.
→ The retrieval-mechanism decision (R15) can run as its own one-shot task in parallel — it doesn't block thread implementation.
