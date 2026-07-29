---
title: "feat: automate ce-learnings-researcher retrieval at task start (R15/R16)"
type: feat
status: active
date: 2026-04-30
origin: docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md
---

# feat: automate ce-learnings-researcher retrieval at task start (R15/R16)

> Closes R15/R16 of the codebase audit brainstorm — the *retrieval automation* that makes the audit's content investment actually pay off. Without this, enriched `docs/solutions/` entries stay unread when new code is written, and the user keeps re-instructing canonical behaviors.

## Overview

The codebase audit completed on 2026-04-30 produced enriched `docs/solutions/` entries with `canonical_source`, `applies_to`, and `duplicates_at` frontmatter, plus matrix YAMLs that catalog drift. The retrieval pipeline (`compound-engineering:ce-learnings-researcher`) reads those entries by frontmatter and is already wired into `/ce-plan` (Phase 1.1). The gap is the bare-prompt and `/ce-work` paths: when the agent edits a T1 file outside a planning workflow, the researcher does not fire. That's the failure mode the user named — "compounding writes findings but they never get applied" — and the dialog-scroll-helper drift over the project's lifetime is the receipt.

This plan layers two complementary mechanisms:

1. A **Claude Code PreToolUse hook** that fires on `Edit`/`Write`/`MultiEdit` against T1 paths. The hook script reads each entry's `applies_to` globs, finds matches for the target path, and emits a structured "consult these learnings before editing" warning to the agent (non-blocking in v1, iterate to blocking if v1 proves insufficient per R16).
2. A **`CLAUDE.md` instruction** at the repo root that documents the convention: invoke `ce-learnings-researcher` before non-trivial T1 edits. This catches the session-start case where Claude reads `CLAUDE.md` and the always-on context channel.

The shared piece is `scripts/learnings_applicability.py` — a pure-Python helper that maps a file path → applicable `docs/solutions/` entries via `applies_to` glob matching. Hook script consumes it; future tooling (CI checks, manual queries) can reuse it.

R16 testability: the helper is unit-testable in isolation, and the verification protocol (track 2–3 subsequent implementation tasks; if the user still has to issue previously-canonical instructions, iterate) is documented as the validation rubric.

---

## Problem Frame

The brainstorm's R15/R16 (origin lines 199–211 and the "retrieval gap" framing in Problem Frame) say:

- R15. The audit MUST produce a process change that ensures `ce-learnings-researcher` is invoked at the start of any non-trivial implementation in a T1 module. Mechanism is settled in planning (Claude Code hook, `AGENTS.md`/`CLAUDE.md` instruction, or skill-prompt modification).
- R16. The retrieval mechanism MUST be testable: starting a new implementation task in a T1 module with a known canonical-source applicability MUST surface the matching `docs/solutions/` entry before code is written. Verification protocol: track 2–3 subsequent implementation tasks; if the user still has to issue previously-canonical instructions, the mechanism is wrong and is iterated as a follow-up.

T1 modules from the revised R2: I/O slice (`src/percell4/domain/io/`, `src/percell4/adapters/importer.py`, `src/percell4/adapters/readers.py`, `src/percell4/store.py`, `src/percell4/application/use_cases/{export_images,compute_phasor,add_decay_to_dataset}.py`, `src/percell4/interfaces/gui/main_window.py`) plus all `src/percell4/gui/*Dialog.py` (`src/percell4/gui/workflows/single_cell/config_dialog.py` included).

The mechanism choice (R15) is settled in this plan. The verification protocol (R16) is documented in U6.

---

## Requirements Trace

- R1. (origin R15) `ce-learnings-researcher` retrieval is automated for T1 modules — the agent does not need to remember to invoke it manually for `Edit`/`Write` tool calls.
- R2. (origin R15) Mechanism is a layered combination of `PreToolUse` hook + `CLAUDE.md` instruction. Skill-prompt modification is *not* in scope for this plan (already partially solved — `/ce-plan` Phase 1.1 invokes the researcher).
- R3. (origin R16) The mechanism is testable via a deterministic, reusable applicability check (`scripts/learnings_applicability.py`).
- R4. (origin R16) Verification protocol is documented: track 2–3 subsequent implementation tasks; iterate the mechanism if the user still has to issue previously-canonical instructions.
- R5. The hook script must be **fast** (sub-100ms typical). It runs on every `Edit`/`Write`/`MultiEdit` call and must not introduce perceptible latency.
- R6. The hook script must be **safe under failure** — a bug or missing dependency in the script must NOT block legitimate edits. It exits 0 with a benign warning when its own logic fails.
- R7. The mechanism uses **stdlib-only Python** in the hook script. Hooks run as subprocesses without `.venv` activation; using stdlib keeps the script portable and avoids "venv not active" failures.

**Origin actors:** A1 (User/Lee Lab researcher), A6 (Agent workflow / project config).

---

## Scope Boundaries

- **Not in scope: blocking edits.** The v1 hook warns; it does not block. R16's verification protocol guides whether v2 should escalate to blocking.
- **Not in scope: modifying skill prompts.** `/ce-plan` already integrates the researcher (Phase 1.1). `/ce-work`'s skill prompt is shipped via the compound-engineering plugin and not editable per-project; layered with the hook + `CLAUDE.md`, the workflow path is covered.
- **Not in scope: extending the retrieval to non-T1 modules.** The brainstorm scopes T1 explicitly; broadening is a follow-up if v1 succeeds.
- **Not in scope: a new researcher agent.** `compound-engineering:ce-learnings-researcher` already exists; this plan automates *invocation* of the existing agent.
- **Not in scope: matching `docs/solutions/` entries by content (full-text).** Matching is by frontmatter `applies_to` glob only — that's the contract the audit's enrichment established.
- **Not in scope: caching the index.** v1 reads `docs/solutions/` from disk on every hook fire; if profiling shows latency above 100ms, add an mtime-keyed cache as a follow-up.

### Deferred to Follow-Up Work

- **Escalation to blocking mode** — only if R16 verification surfaces that warning is insufficient.
- **CI/pre-commit check** that asserts every PR touching a T1 file with applicable canonical sources cites those entries in commit messages — useful, but separate from agent-loop retrieval.
- **`AGENTS.md` parity** — when/if the project adopts `AGENTS.md` as the canonical agent-instruction file, mirror the `CLAUDE.md` content there.
- **Index caching** if profiling shows the hook is slow.

---

## Context & Research

### Relevant Code and Patterns

- **`compound-engineering:ce-learnings-researcher`** — existing agent at `~/.claude/plugins/cache/every-marketplace/compound-engineering/3.0.3/agents/ce-learnings-researcher.agent.md`. Reads `docs/solutions/` frontmatter; described "Integration Points" already include `/ce-plan` and standalone invocation. We invoke it; we don't modify it.
- **`docs/solutions/` entries with new frontmatter shape** — the audit's enrichment added `canonical_source`, `applies_to`, `duplicates_at`, `status`. Reference: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` (clean exemplar after Thread 2 closed it).
- **`docs/audits/canonical-sources-matrix.yaml`** — the matrix that lists 10 canonical-source columns with their `applies_to` shapes; useful as a sanity check when validating the helper's output.
- **`.claude/settings.local.json`** — currently in repo, contains only `permissions`. The new `.claude/settings.json` (committed) is a sibling file for project-level config that all contributors share; settings.local.json stays user-local.
- **`CLAUDE.md`** — top-level project instruction file at repo root. Already documents the architecture and points at `docs/solutions/`; we add a short retrieval-automation section.
- **Existing hook patterns reference:** `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/claude-code-setup/skills/claude-automation-recommender/references/hooks-patterns.md` describes the `.claude/settings.json` shape, `PreToolUse` matchers, and command invocation.

### Institutional Learnings

- The audit brainstorm itself (`docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md`) names "compounding writes findings but they never get applied" as the meta-bug this plan exists to fix.
- `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` is a 5-vector compound that *would have been retrieved* by an applies-to match against `application/use_cases/compute_phasor.py` if the retrieval mechanism had existed when the staleness fixes were being designed. It's the load-bearing case for why this plan matters.
- Five new entries authored by the audit pass (dialog-scroll-when-tall, decay-write-path, channel-deletion-permanence, atomic-write-contract, plus the staleness compound) are now waiting on retrieval to fire.

### External References

- Claude Code hook docs (PreToolUse / PostToolUse / Notification matchers; `command` type; tool-input JSON on stdin). The hook config schema is well-established and the project-scoped `.claude/settings.json` is its canonical home.

---

## Key Technical Decisions

- **Layered mechanism: hook + `CLAUDE.md` instruction; skill-prompt modification deferred.** The hook is deterministic but only fires on tool calls — it can't help when the agent is *thinking* about whether to look something up. The `CLAUDE.md` instruction primes the agent during session-start context loading. Together they cover both proactive (instruction-led) and reactive (tool-call-triggered) cases. Skill-prompt modification is partially solved already (`/ce-plan` invokes the researcher) and would require editing per-user skills outside the repo, which doesn't help other contributors.
- **`PreToolUse` not `PostToolUse`.** We want the warning *before* the edit lands so the agent can pause and consult learnings; firing after the edit is too late.
- **Non-blocking warning in v1.** Blocking would force the agent to retrieve every time, which is expensive and may cause friction on legitimate non-applicable edits (e.g., touching a docstring in a T1 file). v1 emits a structured warning the agent must read; if R16 verification shows warnings are ignored, escalate to blocking.
- **Matcher fires on `Edit|Write|MultiEdit` only.** Read-only tools (`Read`, `Grep`, `Glob`) don't need the warning — the agent is gathering context, which is the right behavior. `NotebookEdit` is excluded since the project has no `.ipynb` files in T1 paths today.
- **Match by `applies_to` glob, not full-text.** The audit's enrichment contract is that each entry declares its applicability via frontmatter globs. The helper trusts that contract; if an entry's `applies_to` is wrong or missing, the fix is to update the entry, not the helper.
- **Stdlib-only hook script.** Hooks run as subprocesses without `.venv` activation. Using `pathlib`, `fnmatch`, `json`, `sys` only avoids brittleness. Frontmatter parsing is hand-rolled (YAML-as-frontmatter has a simple shape we can read line-by-line).
- **Helper lives in `scripts/`, not `src/percell4/`.** It's tooling, not application code. Putting it in `src/percell4/` would imply it's part of the package; it's not — it's invoked by hooks and CLI commands.
- **Session de-duplication is NOT v1.** The naive design fires the warning every time the agent edits a T1 file, even if it already retrieved the entry earlier in the session. Session de-dup requires hook scripts to share state across invocations (a session-scoped state file), which is implementable but complicates v1. v1 accepts repeat warnings and lets R16 verification tell us whether they cause friction.
- **Failure mode: warn, don't crash.** The hook script wraps its logic in a top-level `try/except` and exits 0 with a generic "could not check learnings (see logs)" message on any failure. Critical: a bug in the hook must never block legitimate edits.

---

## Open Questions

### Resolved During Planning

- *Mechanism — hook vs CLAUDE.md vs skill-prompt?* — Layered hook + `CLAUDE.md`. See Key Technical Decisions.
- *Block or warn in v1?* — Warn. R16 verification informs whether to escalate.
- *Where does the helper live?* — `scripts/learnings_applicability.py`, stdlib-only.
- *Match by what?* — Frontmatter `applies_to` globs, per the audit's enrichment contract.
- *Session de-dup?* — Not in v1.

### Deferred to Implementation

- **[Affects U2][Technical]** Exact JSON shape of `PreToolUse` stdin input. Documented externally; the implementer reads a sample firing during U2 to confirm field names (`tool_name`, `tool_input.file_path`, etc.) before finalizing the parser.
- **[Affects U1][Technical]** Whether to use `fnmatch` or `pathlib.PurePath.match` for glob matching. `fnmatch` is more permissive (handles `**` extension informally); `PurePath.match` is more precise but doesn't support `**` recursion at all. Pick during U1 based on the actual `applies_to` patterns in the four authored entries (most use `*` not `**`).
- **[Affects U3][Operational]** Initial set of T1 path globs the hook fires on — derived from the brainstorm's R2 list, but the implementer may simplify to a single `src/percell4/**` matcher and let the helper's per-entry filter do the rest.
- **[Affects U6][User decision]** Cadence of the verification check — once per implementation task for 2–3 tasks vs every retrieval over 1–2 weeks. Settle when the first verification target task is named.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```
┌──────────────────────────────────────────────────────┐
│  Agent attempts: Edit src/percell4/gui/foo_dialog.py │
└──────────────────────────────────────────────────────┘
                       │
                       ▼  (PreToolUse fires)
┌──────────────────────────────────────────────────────┐
│  .claude/settings.json hooks → command pipeline       │
│  python3 scripts/claude_code_hooks/                  │
│         check_learnings_retrieval.py                 │
└──────────────────────────────────────────────────────┘
                       │
                       ▼ (script reads JSON from stdin)
┌──────────────────────────────────────────────────────┐
│  scripts/learnings_applicability.py                  │
│  applicable_entries(file_path) → [                   │
│      docs/solutions/ui-bugs/dialog-scroll-when-tall.md,│
│      ...                                             │
│  ]                                                   │
└──────────────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Script emits to stderr (visible to agent):          │
│  ⚠ Learnings apply to gui/foo_dialog.py:             │
│    • dialog-scroll-when-tall (canonical_source:      │
│      src/percell4/gui/_dialog_utils.py)              │
│    Run ce-learnings-researcher with this path        │
│    before substantive edits.                         │
└──────────────────────────────────────────────────────┘
                       │
                       ▼ (exit 0; edit proceeds)
                  Edit happens.
```

CLAUDE.md instruction parallel path: every session start, the agent reads `CLAUDE.md` which now contains "Before non-trivial edits in T1 modules, invoke `ce-learnings-researcher` with the file paths in scope."

---

## Implementation Units

- U1. **Add the applicability helper.**

  **Goal:** A pure, stdlib-only function that maps a file path → list of `docs/solutions/` entries whose `applies_to` matches.

  **Requirements:** R3, R5, R7.

  **Dependencies:** None.

  **Files:**
  - Create: `scripts/__init__.py` (empty, to make `scripts/` importable for tests)
  - Create: `scripts/learnings_applicability.py`
  - Test: `tests/test_scripts/__init__.py` (empty)
  - Test: `tests/test_scripts/test_learnings_applicability.py`

  **Approach:**
  - Public function: `applicable_entries(file_path: str, solutions_root: Path = REPO_ROOT/"docs/solutions") -> list[Entry]`. Returns a list of small dataclass-like records (`{slug, path, canonical_source, status, applies_to_match}`) for entries whose `applies_to` glob matched.
  - Read every `.md` under `solutions_root` recursively; parse frontmatter (between `---` markers) line-by-line — the format is simple key:value with list values on subsequent lines using `- ` prefix.
  - For glob matching: use `fnmatch.fnmatch` for v1 simplicity. The four authored entries use patterns like `src/percell4/gui/*Dialog.py` and `src/percell4/application/use_cases/*.py` — `fnmatch` handles `*` correctly. Document this constraint at the helper's top.
  - Skip entries with `status: superseded` or no `applies_to` field.
  - Helper exposes a `main()` so `python3 scripts/learnings_applicability.py path/to/file.py` prints the matching entries (debug aid + foundation for the hook).

  **Patterns to follow:**
  - Simple, fast, stdlib-only — mirror the spirit of `tests/conftest.py` (light fixtures, no heavy dependencies).
  - Frontmatter parsing follows the shape used by `ce-learnings-researcher.agent.md`'s Step 4.

  **Test scenarios:**
  - Happy path: a path matching `dialog-scroll-when-tall.md`'s `applies_to: ["src/percell4/gui/*Dialog.py"]` returns that entry. (Use the post-U7 canonical_clean version of the entry as input.)
  - Happy path: a path matching multiple entries returns all of them. Use a test fixture entry under `tests/test_scripts/fixtures/solutions/` with overlapping `applies_to`.
  - Edge case: a path that matches no entry returns `[]`.
  - Edge case: an entry with `applies_to: []` returns no match.
  - Edge case: an entry with `status: superseded` is filtered out.
  - Edge case: a malformed frontmatter (missing closing `---`, invalid YAML-ish) is skipped silently — the helper does not raise.
  - Edge case: a file with no frontmatter at all is skipped silently.
  - Edge case: relative vs absolute paths — both should match equivalently. Accept either form; normalize internally.
  - Performance: with the current ~25 entries, `applicable_entries(...)` returns in < 50ms (assert via `time.perf_counter()`).

  **Verification:**
  - `pytest tests/test_scripts/test_learnings_applicability.py` passes.
  - `python3 scripts/learnings_applicability.py src/percell4/gui/import_dialog.py` prints `dialog-scroll-when-tall` (and any other matches).
  - `python3 scripts/learnings_applicability.py src/percell4/domain/measure/metrics.py` prints nothing (T3 path; not applicable).

- U2. **Add the PreToolUse hook script.**

  **Goal:** A small subprocess script that reads tool-call JSON from stdin, calls U1's helper, and emits a structured warning to stderr when applicable entries exist.

  **Requirements:** R1, R5, R6, R7.

  **Dependencies:** U1.

  **Files:**
  - Create: `scripts/claude_code_hooks/__init__.py` (empty)
  - Create: `scripts/claude_code_hooks/check_learnings_retrieval.py`
  - Test: `tests/test_scripts/test_check_learnings_retrieval.py`

  **Approach:**
  - Read JSON from stdin via `json.load(sys.stdin)`.
  - Extract the affected file path from `tool_input.file_path` (for `Edit`/`Write`) or `tool_input.notebook_path` (skipped — out of scope) — verify exact field name during implementation against a sample firing.
  - Short-circuit: if the path is outside `src/percell4/` and outside `tests/`, exit 0 with no output. Conservative T1 filter at the hook level so the helper doesn't load for every doc edit.
  - Call `applicable_entries(path)`. If the list is empty, exit 0 with no output.
  - Otherwise, emit a structured warning to stderr (visible to the agent) with: file path, list of matching entry slugs and their `canonical_source` values, and the exact prompt to invoke `ce-learnings-researcher`.
  - Wrap everything in a top-level `try/except Exception`. On failure, write `# learnings-retrieval check skipped: <reason>` to stderr and exit 0.
  - Keep the script small (< 80 lines).

  **Patterns to follow:**
  - The error-swallow pattern from `cap_to_screen` in `src/percell4/gui/_dialog_utils.py` — best-effort, never crash the caller.

  **Test scenarios:**
  - Happy path: feeding stdin `{"tool_name": "Edit", "tool_input": {"file_path": "src/percell4/gui/foo_dialog.py"}}` produces stderr output containing "dialog-scroll-when-tall" and exit code 0. Assert the message includes the canonical_source path.
  - Happy path: a path with no applicable entries produces no stderr output and exit code 0.
  - Edge case: tool_name=`Read` (read-only tool) produces no output and exit code 0. (The hook matcher should not even fire for Read in production, but the script defends in depth.)
  - Edge case: malformed stdin JSON exits 0 with a generic skip message; does not crash.
  - Edge case: missing `tool_input.file_path` exits 0 with a generic skip message.
  - Error path: helper raises (simulated by monkeypatching) — script exits 0 and writes a skip message to stderr.
  - Performance: full hook invocation completes in < 100ms wall-clock for a path with 1–2 matches. Assert via `time.perf_counter()` around `subprocess.run`.

  **Verification:**
  - Manually invoking the script via `echo '{"tool_name":"Edit","tool_input":{"file_path":"src/percell4/gui/import_dialog.py"}}' | python3 scripts/claude_code_hooks/check_learnings_retrieval.py` prints the dialog-scroll-when-tall match to stderr and exits 0.
  - The same invocation against a non-T1 path (e.g., `docs/foo.md`) produces no output.

- U3. **Wire the hook into `.claude/settings.json`.**

  **Goal:** Register the script as a `PreToolUse` hook on `Edit|Write|MultiEdit` so it fires automatically.

  **Requirements:** R1, R2.

  **Dependencies:** U2.

  **Files:**
  - Create: `.claude/settings.json` (NEW project-scoped config; `.claude/settings.local.json` stays user-local).

  **Approach:**
  - Schema follows the standard Claude Code hook format: `{"hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": "python3 scripts/claude_code_hooks/check_learnings_retrieval.py"}]}]}}`.
  - Use `python3` not `.venv/bin/python` — the hook script is stdlib-only and must work whether or not the venv is activated.
  - Document at the top of the settings file (or in the commit message) that this hook is the implementation of R15 and points at the brainstorm.

  **Patterns to follow:**
  - Hook config shape from `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/claude-code-setup/skills/claude-automation-recommender/references/hooks-patterns.md`.

  **Test scenarios:**
  - Test expectation: none — pure config. Verification is behavioral: in a fresh Claude Code session, attempting to `Edit src/percell4/gui/import_dialog.py` causes the warning to surface in the conversation. Documented in U6's verification protocol.

  **Verification:**
  - `cat .claude/settings.json | python3 -m json.tool` parses without error.
  - In a fresh Claude Code session loaded against this repo, an `Edit` against a T1 file with applicable entries surfaces the warning before the edit lands.

- U4. **Add `CLAUDE.md` retrieval-automation section.**

  **Goal:** Document the convention so the agent picks it up at session start (always-on context) even when the hook can't help (e.g., during planning, when reasoning about what to do).

  **Requirements:** R1, R2.

  **Dependencies:** U1 (the helper is referenced by name).

  **Files:**
  - Modify: `CLAUDE.md`

  **Approach:**
  - Add a new section "## Audit-driven retrieval automation" near the existing "Documentation Rules" section.
  - Body (~10 lines): names the convention ("Before non-trivial edits in T1 modules, invoke `ce-learnings-researcher` with the file paths in scope"), points at the matrix YAML and the helper script, links the brainstorm origin.
  - Mention the hook briefly so future readers know the warning isn't manual but automated.

  **Patterns to follow:**
  - Existing CLAUDE.md sections are short, prose-only, point at canonical artifacts.
  - Use repo-relative paths.

  **Test scenarios:**
  - Test expectation: none — pure documentation. Validation is in U6 (does the agent actually consult learnings in the next 2–3 implementation tasks?).

  **Verification:**
  - `CLAUDE.md` renders cleanly in markdown viewers.
  - The new section names: (a) the convention, (b) the helper at `scripts/learnings_applicability.py`, (c) the matrix at `docs/audits/canonical-sources-matrix.yaml`, (d) the brainstorm origin.

- U5. **Wire test-runner discovery for the new tests.**

  **Goal:** Ensure the new `tests/test_scripts/` package is picked up by `pytest`.

  **Requirements:** R3.

  **Dependencies:** U1, U2.

  **Files:**
  - Modify: `pyproject.toml` only if `pytest` config explicitly enumerates test paths (verify during implementation; if pytest auto-discovers, skip this unit).

  **Approach:**
  - Run `pytest tests/test_scripts/ -v` after U1 and U2 land. If pytest finds and runs the tests, this unit is a no-op and can be deleted.
  - If pyproject.toml's `[tool.pytest.ini_options]` enumerates `testpaths`, add `tests/test_scripts/` to the list.

  **Patterns to follow:**
  - Existing `tests/test_io/` and `tests/test_gui/` packages — both are auto-discovered today; the new package follows the same structure.

  **Test scenarios:**
  - Test expectation: none — pure config (or no-op).

  **Verification:**
  - `pytest tests/test_scripts/` runs and reports the 7+ tests from U1 and U2.

- U6. **Document the verification protocol.**

  **Goal:** A short doc that records the R16 verification rubric and tracks the next 2–3 implementation tasks against it.

  **Requirements:** R4.

  **Dependencies:** U1, U2, U3, U4.

  **Files:**
  - Create: `docs/audits/retrieval-automation-verification.md`

  **Approach:**
  - Sections:
    1. **Rubric** (one paragraph): the mechanism succeeds when the user does NOT have to issue previously-canonical instructions during the next 2–3 implementation tasks in T1 modules.
    2. **Tracker** (table): columns for `task slug`, `T1 files touched`, `applicable entries (per helper)`, `was researcher invoked? (Y/N)`, `did user re-instruct any canonical behavior? (Y/N — list)`, `decision: keep mechanism / iterate / escalate to blocking`.
    3. **Iteration triggers** (bullet list): explicit conditions under which the v1 mechanism is judged insufficient and v2 is planned (e.g., "user re-instructed a canonical behavior in 2 of 3 tasks").
    4. **Closing condition** (one paragraph): when the doc is "done" — after 3 successful tasks with no re-instruction events; mark and link the verification result in the brainstorm's R16 row.

  **Patterns to follow:**
  - Same lightweight YAML-frontmatter shape as other `docs/audits/` files.
  - Cross-link to the matrix YAMLs and the brainstorm origin.

  **Test scenarios:**
  - Test expectation: none — pure documentation. The "test" is the verification process itself.

  **Verification:**
  - Doc exists at `docs/audits/retrieval-automation-verification.md` with the four sections.
  - Cross-links resolve to `docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md` and `docs/audits/canonical-sources-matrix.yaml`.

---

## System-Wide Impact

- **Interaction graph:** The hook fires on every `Edit`/`Write`/`MultiEdit` tool call. It runs as a subprocess; it does not modify the tool input or block the call (v1 is warn-only). Read-only tools are not affected.
- **Error propagation:** Hook script swallows all exceptions, exits 0 with a benign message — never blocks the underlying tool call (R6).
- **State lifecycle risks:** None in v1. The script is stateless; there is no session-scoped state, cache, or persistence.
- **API surface parity:** The applicability helper at `scripts/learnings_applicability.py` is reusable. Future tooling (CI checks, manual queries, IDE integrations) can import it without dependency on the hook.
- **Integration coverage:** U6's verification protocol is the integration-coverage check — does the mechanism actually change agent behavior in real implementation tasks? Unit tests prove the helper and hook script work in isolation; verification proves the mechanism works in production.
- **Unchanged invariants:** `compound-engineering:ce-learnings-researcher` is unmodified. `docs/solutions/` content is unmodified. `pyproject.toml`, `requirements.txt`, the venv layout — all unchanged. The hook script is stdlib-only and runs with system `python3`.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Hook script slow → every edit feels laggy. | R5 mandates < 100ms; U1 and U2 test scenarios assert this; if profiling shows otherwise, add mtime-keyed index caching as a follow-up. |
| Hook script bugs → blocks legitimate edits. | R6 mandates exit 0 on any failure with a benign skip message. Defensive `try/except` at script top level; test scenarios cover malformed stdin, missing fields, helper crashes. |
| Warning is too noisy → agent ignores it. | U6's verification protocol explicitly tests for this. If the agent ignores the warning, escalate to blocking mode in v2. |
| Warning fires repeatedly within a session for the same entry. | v1 accepts this; session de-duplication is deferred. If verification shows it causes friction, add session-state file. |
| `applies_to` globs in entries are wrong → mechanism misses or over-matches. | Glob correctness is part of the audit's enrichment contract. If the helper surfaces a false positive or false negative during verification, the fix is to update the `docs/solutions/` entry's frontmatter, not the helper. |
| Future entries authored without `applies_to` field don't get retrieved. | The audit's R7 mandates the field. Add a new compliance test (follow-up) that asserts every `docs/solutions/` entry has either `applies_to` or `canonical_source: n/a`. |
| `python3` isn't on PATH for some contributor's shell config. | Ship-blocker only if it actually happens. Mitigation if it does: change the hook command to use an absolute path or detect via `command -v python3` and skip with a benign message. |
| Hook fires during the helper's own tests. | The hook checks paths under `src/percell4/`; tests live under `tests/`. Tests editing `src/percell4/` files would trigger the hook, but that's the desired behavior. |

---

## Documentation / Operational Notes

- **Commit citation convention** for this thread's PRs: `Closes R15: retrieval automation v1` and `Closes R16: verification protocol documented`. Each commit should also reference the brainstorm: `(see origin: docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md)`.
- **Onboarding note:** new contributors who clone the repo automatically pick up `.claude/settings.json` (committed). The hook fires from the first session.
- **Disabling the hook for debugging:** rename `.claude/settings.json` to `.claude/settings.json.bak` for the session; restore after.

---

## Sources & References

- **Origin document:** `docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md` (R15, R16, F5)
- **Existing retrieval agent:** `compound-engineering:ce-learnings-researcher` (read-only reference)
- **Audit deliverables to retrieve from:** `docs/audits/canonical-sources-matrix.yaml`, `docs/solutions/` (post-audit enriched entries)
- **Hook config reference:** `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/claude-code-setup/skills/claude-automation-recommender/references/hooks-patterns.md`
- **Load-bearing case the mechanism would have caught:** `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md`
