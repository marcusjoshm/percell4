---
date: 2026-04-29
topic: io-principles-audit-and-remediation
---

# I/O Principles Audit & Remediation Initiative

## Problem Frame

Seven I/O principles were just settled in `docs/ideation/2026-04-29-io-principles-ideation.md`. None are enforced anywhere yet. Without an explicit operationalization plan, three failure modes are likely:

1. **Tribal application** — principles get partially applied per PR; the codebase drifts in seven different ways with no view of total compliance.
2. **Recurring violations** — the "discovery scopes, processing consumes" rule has already been violated twice (compress, add-layer); other principles will follow the same pattern unless caught structurally.
3. **Real-user pain stays unaddressed** — concrete workflows like *"add TCSPC `.bin` to an existing dataset and bind to the right intensity channel despite mismatched token conventions"* aren't possible today, and the principles don't auto-fix them.

This initiative defines a **process** — not any single fix — to (a) audit current I/O code against the seven principles, (b) organize remediation as user-pain threads that close violations across multiple principles per thread, and (c) leave behind a living matrix that future I/O work continues to satisfy.

The working tree currently has uncommitted modifications to six I/O files (`importer.py`, `discovery.py`, `models.py`, `scanner.py`, `compress_dialog.py`, `store.py`), which confirms the seam is hot and the audit is timely.

---

## Actors

- A1. **User (Lee Lab researcher)**: Provides known violations as audit seeds; reviews matrix findings; picks next thread; reviews thread plans before implementation.
- A2. **AI auditor**: Runs the rubric pass on tiered scope; populates the matrix with evidence-backed findings; surfaces threads the user did not name.
- A3. **AI planner (`/ce-plan`)**: Takes one thread + relevant matrix cells as input; produces an implementation plan that retires those cells.
- A4. **PerCell4 codebase**: The subject of the audit; receives PRs that close matrix cells and reference the matrix in their commit messages.

---

## Key Flows

- F1. **Initial audit pass**
  - **Trigger:** User runs the audit (likely via `/ce-work` or a one-shot agent dispatch).
  - **Actors:** A1, A2.
  - **Steps:**
    1. User provides their seed list of known violations (starting with the two TCSPC + cross-format examples already captured in this doc).
    2. AI reads the in-scope (T1) files with the per-principle rubric, finds additional violations.
    3. AI optionally extends to T2 files when threads or evidence point there.
    4. AI writes the matrix to `docs/audits/io-principles-matrix.md` with one row per file and seven principle columns; each cell is `OK` / `VIOLATION` / `N/A` with one-line evidence and an optional thread tag.
    5. AI proposes thread groupings (one thread can retire cells across multiple principles) at the bottom of the matrix doc.
  - **Outcome:** Matrix doc exists; threads are named with rough scope tags; user can see the total surface and pick where to start.
  - **Covered by:** R1, R2, R3, R4, R5, R6.

- F2. **Thread selection and planning**
  - **Trigger:** User picks the next thread to work on (first thread is pre-selected: TCSPC append + cross-format token matching).
  - **Actors:** A1, A3.
  - **Steps:**
    1. User invokes `/ce-plan` with the thread name and the matrix cells it claims to retire.
    2. Planner reads the matrix, the thread's cited principles, and the affected files.
    3. Planner produces `docs/plans/YYYY-MM-DD-thread-<slug>-plan.md` whose acceptance criteria include "matrix cells X, Y, Z transition from VIOLATION to OK."
  - **Outcome:** Thread plan exists, ready for implementation.
  - **Covered by:** R7, R8.

- F3. **Thread implementation and matrix update**
  - **Trigger:** PRs from a thread plan land on `main`.
  - **Actors:** A1, A4.
  - **Steps:**
    1. Each PR explicitly cites which matrix cells it retires.
    2. When the last PR of a thread merges, the matrix doc is updated to mark those cells `OK`.
    3. If new violations are surfaced during implementation (e.g., a touched file revealed an issue not in the original matrix), the matrix is updated with new rows/cells in the same PR.
  - **Outcome:** Matrix reflects current reality; the thread's cited cells are green.
  - **Covered by:** R9, R10.

---

## Requirements

**Audit pass**

- R1. The audit MUST be **user-seeded + AI-completes**: the user provides known violations as anchors, the AI does an independent rubric pass to find additional violations.
- R2. Audit scope is **tiered**:
  - **T1 (must audit, drives the matrix):** `src/percell4/domain/io/`, `src/percell4/adapters/importer.py`, `src/percell4/store.py`, the four I/O dialogs (`gui/import_dialog.py`, `gui/add_layer_dialog.py`, `gui/compress_dialog.py`, `gui/export_images_dialog.py`), and `src/percell4/application/use_cases/{import_dataset,export_images,compute_phasor,batch_compress}.py` (verify the actual file names during the audit).
  - **T2 (audit-as-needed):** FLIM-specific I/O (`src/percell4/flim/` readers for `.sdt` / `.bin`), `assembler.py`, `src/percell4/project.py` (project index).
  - **T3 (out of scope):** pure-domain compute that does not open files (`src/percell4/domain/measure/`, `src/percell4/domain/segmentation/`, `src/percell4/domain/flim/phasor.py`).
- R3. The audit MUST cover **all seven principles** for each in-scope file. A principle column may be `N/A` for a file (e.g., principle #2 round-trip on a write-only path), but the cell MUST exist and the `N/A` MUST carry a one-line reason.
- R4. Each `VIOLATION` cell MUST cite **specific evidence**: a function name, line range, or quoted code snippet showing the violation. Heuristic-only "this file probably has issues" is not accepted.

**Audit matrix doc**

- R5. The matrix is a **structured-data file** at `docs/audits/io-principles-matrix.yaml` — keyed by `<file_path>.<principle_number>` with cell records carrying `status`, `evidence`, `thread`, and `notes`. A small render script `scripts/render_io_matrix.py` produces `docs/audits/io-principles-matrix.md` for human reading. PRs MUST update the YAML; the rendered Markdown is regenerable and may be regenerated by the same PR or by a follow-up commit. Reasoning: a Markdown table of ~100 cells is painful to diff, multiplies merge-conflict surface during active churn, and forecloses a future v2 CI check that would need a parseable artifact. Structuring the data now for v2 enforcement costs almost nothing; retrofitting later is expensive.
- R5b. **Audit baseline.** The audit runs against `HEAD` of the branch the audit PR is opened on — NOT the working tree. Uncommitted modifications are out of scope for the matrix. If the user wants uncommitted work audited, they commit it first (or rebase the audit PR onto a branch that includes it).
- R6. The matrix YAML MUST include a `threads:` section, where each thread has: a slug, a one-line description, a list of cited matrix cell keys, the principles it retires, and a status (`proposed` / `planned` / `in_progress` / `closed`). Cell status enum is `ok` / `violation` / `partial` / `n/a` — `partial` requires a `residual_evidence` note pointing at what still needs to close.

**Remediation grouping**

- R7. Remediation MUST be organized by **user-pain threads**, not by principle. One thread MAY close violations across multiple principles. The matrix is the source of truth; threads are how work gets done.
- R8. The **first thread** is pre-selected: *TCSPC append + cross-format token matching*. It targets principles 3 (Identify Before Open / Metadata Before Filename), 4 (Two-Layer Postel + Single Write Boundary), and 5 (One Pipeline: Batch ≡ Incremental), with corollary effects on 1 (Capability Matrix) and 6 (Provenance). The thread MUST enable: (a) adding `.bin` TCSPC data to an existing `.h5` dataset, (b) reconciling mismatched token conventions (e.g., `.tif _s00_ch00` ↔ `.bin s1_ch1`) so the right `.bin` channel binds to the right intensity image channel, (c) tile stitching of TCSPC volumes consistent with the existing intensity tiles.

**PR conventions**

- R9. Every PR that lands a fix MUST cite the matrix cells it retires in the PR description (e.g., "Closes matrix cells: scanner.py × #4, discovery.py × #5").
- R10. The matrix doc MUST be updated **in the same PR** that retires its cells — never a follow-up PR. If a fix surfaces new violations, those are added to the matrix in the same PR.

**Threads beyond the first**

- R11. Subsequent threads MUST be proposed by the AI auditor in the matrix YAML's `threads:` section, each with a one-paragraph rationale citing user-pain signal, cited cell count, and an estimated implementation complexity bucket (`small` / `medium` / `large`). The auditor proposes a recommended ordering with reasoning; the user picks the next thread to work on. No formula is mandated — the user's judgment, informed by the rationale, settles the order.
- R12. **Thread scope cap.** A thread plan that grows beyond ~10 cited matrix cells during planning MUST be split. Threads that span more files become hard to land atomically and accumulate review surface that obscures principle progress. Split threads keep their original user-pain framing and inherit the proposing rationale; matrix entries cite the new thread slug.
- R13. **Audit-tier for tests.** Tier T1 of R2 includes I/O test files at `tests/test_io/`, `tests/test_use_cases/test_*import*`, `tests/test_use_cases/test_*export*`, `tests/test_gui_workflows/test_*dialog*`. Test files can themselves encode principle violations (hardcoded fixture paths, format-token assumptions, batch/incremental divergence in test scaffolding). Tests are not exempt from the matrix.

---

## Acceptance Examples

- AE1. **Covers R1, R3, R4.** Given the user has provided two seed violations (TCSPC append missing; mixed token conventions), when the AI runs the audit, the matrix MUST include those two violations as concrete cells AND additional violations the user did not name (e.g., direct `h5py.File(...).create_dataset` calls outside `_write_layer` if any exist; functions in `compress_dialog.py` that re-derive files from `source_dir`). Each violation cell cites a function name or line range.

- AE2. **Covers R5, R6, R10.** Given the matrix YAML exists and Thread 1 (TCSPC append) is in progress, when its third PR merges, the matrix YAML in that same PR transitions the relevant cells (e.g., `importer.py.4`, `store.py.5`, `add_layer_dialog.py.5`) from `violation` to `ok`, and the `threads:` section moves Thread 1 from `in_progress` to `closed`. The rendered Markdown view (`io-principles-matrix.md`) is regenerated either in the same PR or in a follow-up commit; the YAML is authoritative.

- AE3. **Covers R7, R8.** Given Thread 1 spans principles 3, 4, and 5, when its plan is written, the plan's acceptance criteria explicitly call out matrix cells across all three principles. A plan that addresses some-but-not-all of a thread's cited cells MAY defer cells with a stated reason — deferred cells return to the matrix as `violation` (with a `deferred_from_thread: <slug>` note) and the thread closes when its non-deferred cells go `ok`. A plan that silently ignores cited cells (no decision recorded) MUST be rejected as incomplete.

- AE4. **Covers R11.** Given the audit completes and proposes Threads 2-N, when the user has not picked Thread 2, no plan exists for it; the matrix doc is sufficient. The planner is invoked only when the user picks.

---

## Success Criteria

**Human outcome**

- The user can answer "is the codebase compliant with principle X?" by reading one row of one document, not by grepping.
- Every concrete I/O pain the user names becomes a thread; thread completion makes the pain go away (e.g., after Thread 1 closes, the user CAN add TCSPC `.bin` to an existing `.h5` and the convention mismatch is reconciled automatically).
- New code added to in-scope files in the future has a written rubric to be audited against — the matrix is reusable.

**Downstream-agent handoff quality**

- The audit pass is **structurally** repeatable, not byte-repeatable: a fresh agent invocation given this doc + the YAML matrix can re-run the audit on a future commit and produce a comparably-shaped matrix that can be diffed against the prior one cell-by-cell. Exact wording of evidence and exact set of surfaced violations will vary because (a) the audit is user-seeded — different seeds yield different first-pass coverage — and (b) the AI rubric pass is non-deterministic. The hybrid trades byte-repeatability for completeness; this is the right call for v1, but should not be confused with deterministic auditing.
- The audit always runs against `HEAD` of the branch the audit PR is opened on, never the working tree (per R5b). This makes "re-run the audit" a well-defined operation: it's `git checkout <commit>; <run-audit>`.
- `/ce-plan` invocations against a thread can produce a plan **without inventing scope** — the thread slug + cited matrix cells uniquely identify what to do.
- Matrix-cell citations in PR descriptions allow `git log --grep "matrix cells"` to enumerate which violations retired in which commits. The structured YAML allows the same lookup over the matrix history via `git log -p docs/audits/io-principles-matrix.yaml`.

---

## Scope Boundaries

- **Not in scope: brainstorming any single principle.** That belongs in separate ce-brainstorm sessions if needed when a thread requires deeper definition.
- **Not in scope: changing the seven principles.** They were settled in the prior ideation. If a thread surfaces evidence a principle is wrong or incomplete, that's an out-of-band update to the ideation doc — not a deliverable here.
- **Not in scope: porting principles to non-I/O code.** This initiative covers I/O only. Cross-cutting refactors (e.g., capability matrix patterns applied to plugins) are deferred.
- **Not in scope: T3 modules.** Pure-domain compute is excluded from the matrix.
- **Not in scope: a formal "done" date.** The initiative is rolling: the matrix lives, threads close as priority allows.
- **Not in scope: enforcement automation in v1.** Lint rules, CI checks, runtime assertions for the principles are *future* work; v1 relies on the matrix as a living document and PR-description citations.

---

## Key Decisions

- **Audit-first over foundation-first / lint-driven / codify-and-review.** The matrix as first artifact gives a complete view before remediation decisions are made; foundation-first risks committing to one anchor principle without seeing total surface. Lint-driven is deferred to a future phase. Codify-and-review alone is too slow for the active churn in the working tree.
- **Per-file × per-principle matrix as source of truth.** Granularity matches how violations actually distribute (one file can violate multiple principles; one principle violates multiple files). Coarser granularity (e.g., per-module) loses too much signal.
- **User-seeded + AI-completes.** Pure heuristic AI pass misses domain-specific violations the user can name immediately (TCSPC append). Pure user audit misses what AI catches at scale. The hybrid uses each side for what it's best at.
- **Thread-grouped remediation, matrix cells as currency.** A real fix often closes violations on multiple principles at once. Forcing per-principle work-streams would split TCSPC append into three artificially-separated PRs. Threads are the natural unit; cells are the receipts.
- **Three-doc artifact set.** Requirements doc (this) + living matrix doc + one plan per thread. Matrix and plans share the cell-citation contract so they don't drift.
- **First thread pre-selected.** TCSPC append + cross-format token matching is the user's named pain and lights up principles 3+4+5 — *plausibly* the densest cluster, though final density can only be confirmed once the matrix exists. The pick is justified by user-pain alone; matrix density is corroborating evidence at best until the audit pass runs.

---

## Dependencies / Assumptions

- The seven principles in `docs/ideation/2026-04-29-io-principles-ideation.md` are stable enough to audit against. If a principle proves under-specified during the audit (e.g., "what counts as a 'sidecar' under principle 3?"), the audit pauses, the ideation doc is sharpened, and the audit resumes — not a re-derivation here.
- `docs/audits/` does not yet exist; the audit pass creates it.
- The four I/O dialogs and the use-case file names listed in R2 are the names the user knows them by; the audit verifies the actual file paths against the codebase before populating the matrix (file names may have shifted slightly during the active churn).
- Running `/ce-plan` for the first thread requires the matrix to exist OR the requirements doc to enumerate the thread's matrix cells from the seed examples. The TCSPC thread can be planned from this doc alone if the user wants to start the plan in parallel with the audit.
- No new dependencies introduced by this initiative itself. Individual threads may add dependencies (e.g., the TCSPC thread might need a tile-stitching helper); those are scoped in their own plans.

---

## Outstanding Questions

### Resolve Before Planning

- *(none — the initiative shape is settled enough to invoke `/ce-plan` for the first thread)*

### Deferred to Planning

- **[Affects R2][Technical]** Verify the actual file paths in `application/use_cases/` against the user's stated names (`import_dataset`, `export_images`, `compute_phasor`, `batch_compress`) — naming may have shifted during active churn.
- **[Affects R8][Technical]** What's the canonical schema for binding `.bin` TCSPC volumes to intensity channels in the `.h5`? The thread plan answers this; this requirements doc only states the binding must exist.
- **[Affects R8][Needs research]** Token-convention reconciliation rule: hardcoded "zero-padded → unpadded with +1 offset" or a configurable cross-format token map? The user wants it "discoverable and configurable" — the planning step decides whether config lives in `TokenConfig`, in a sidecar JSON, or somewhere else.
- **[Affects R5][Technical]** Schema for `docs/audits/io-principles-matrix.yaml` — exact key shape (`<file>.<principle>` vs nested mapping), `evidence` field structure, and the render script's output style — settled by the first audit PR.
- **[Affects R10][Technical]** PR-description grep convention: exact format of "Closes matrix cells: ..." line for `git log --grep` to work cleanly. The first thread's first PR fixes the format; subsequent PRs follow.
- **[Affects R12][User decision]** Exact threshold for the thread-scope cap (~10 cells is a starting heuristic; tune after the first 2-3 threads land).

---

## Next Steps

→ **`/ce-plan`** for the first thread (*TCSPC append + cross-format token matching*). The audit + matrix work can run as a separate `/ce-work` task in parallel — they don't block each other. When the audit completes, additional threads queue up at the bottom of the matrix and the user picks #2.
