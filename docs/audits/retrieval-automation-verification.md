---
title: "R16 verification — retrieval automation rollout"
date: 2026-04-30
status: open
related:
  - docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md
  - docs/plans/2026-04-30-feat-learnings-retrieval-automation-plan.md
  - docs/audits/canonical-sources-matrix.yaml
---

# R16 verification — retrieval automation rollout

This doc tracks whether the v1 retrieval mechanism shipped on `feat/learnings-retrieval-automation` actually changes agent behavior in real implementation tasks.

## Rubric

The mechanism is judged **successful** when the user does NOT have to issue previously-canonical instructions during the next 2–3 implementation tasks that touch T1 modules with applicable canonical sources. "Previously-canonical" means anything documented in `docs/solutions/` with `canonical_source != n/a`.

The mechanism is judged **insufficient** when the user has to re-instruct a canonical behavior in 2 of the 3 tracked tasks. Insufficiency triggers iteration — likely escalation from warn-only to blocking mode (PreToolUse hook returns exit code 2 on uncovered drift), tightening the `CLAUDE.md` instruction, or both.

## Tracker

| # | Date | Task slug | T1 files touched | Applicable entries (per `scripts/learnings_applicability.py`) | Researcher invoked? | User had to re-instruct any canonical behavior? | Decision |
|---|---|---|---|---|---|---|---|
| 1 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| 2 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| 3 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

How to fill a row:
1. After a relevant task lands, set `Date` and `Task slug` to the merged PR or branch name.
2. List the T1 files the task modified (`git diff --name-only <merge-base>..HEAD`, filtered to T1 prefixes).
3. For each file, run `python3 scripts/learnings_applicability.py <path>` and record the matching slugs.
4. Inspect the session transcript: did `ce-learnings-researcher` (or its successor) actually run? Y/N.
5. Did the user have to issue an instruction that maps to one of the applicable entries (e.g., "remember to use `wrap_in_scroll`")? If yes, list the instructions and which entries they should have surfaced.
6. Record the per-task decision: `keep` (mechanism worked), `iterate-soft` (small adjustment), `iterate-blocking` (escalate hook to exit 2).

## Iteration triggers

The v1 mechanism gets revised when any of the following occurs across the three tracked tasks:

- **Hard signal:** ≥ 2 tasks in which the user re-instructed a canonical behavior whose entry was applicable. Action: escalate the PreToolUse hook from warn (exit 0) to block (exit 2) and require an in-session researcher invocation before tool calls proceed.
- **Soft signal:** 1 task with re-instruction, but the agent demonstrated awareness of the warning (e.g., consulted the entry and chose to deviate intentionally). Action: keep warn-only; widen `CLAUDE.md` guidance.
- **False-positive signal:** ≥ 1 task where the warning fired but applicable entries were genuinely irrelevant (e.g., the file was modified for an unrelated reason — a typo fix, a docstring tweak). Action: refine the `applies_to` globs in the noisy entries; do not change the mechanism.
- **No-fire signal:** 1 task where a canonical behavior was re-implemented but the helper found no applicable entry. Action: the missing entry is the bug; author it (or fix its `applies_to`) per the audit's R5b ("first audit pass MUST author entries for every gap").

## Closing condition

The doc transitions to `status: closed` after either:

- **3 successful tasks** in a row with no re-instruction events. The brainstorm's R16 row is then linkable as "verified" and the v1 mechanism is the steady state.
- **A v2 mechanism merging** in response to insufficiency. The closing comment links the v2 plan and freezes this tracker as a historical artifact.

When closed, update the brainstorm's R15/R16 status to reference this doc as the verification record.

## Operational notes

- The mechanism does not retroactively cover tasks completed before `feat/learnings-retrieval-automation` merged. Verification starts on the next task that begins after merge.
- The hook is project-local (`.claude/settings.json`); contributors who clone the repo pick it up automatically. If a contributor reports the hook isn't firing, check `python3 -m json.tool .claude/settings.json` and `git log -- .claude/settings.json`.
- The applicability helper is callable independently for ad-hoc queries — `python3 scripts/learnings_applicability.py <path>` works any time and is the canonical way to ask "what do I need to know before editing X?".
