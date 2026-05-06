---
title: Complete a branch before merging — no follow-up commits on main
date: 2026-05-06
category: workflow-issues
module: development-workflow
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - preparing to merge a feature or fix branch into main
  - a refactor changed a constant or signature that tests in the same module reference
  - about to commit a learning or solutions doc related to a fix that has not yet merged
  - reviewing a branch tip and noticing related artifacts (tests, docs, config) are missing
  - tempted to "merge now, polish on main" because the branch is otherwise green
related_components:
  - testing_framework
  - documentation
tags:
  - git-workflow
  - merge-hygiene
  - branch-discipline
  - pre-merge-checklist
  - commit-history
---

# Complete a branch before merging — no follow-up commits on main

## Context

In the past 24 hours on this repo, two merges into `main` shipped the
*engineering* correctly but left loose ends that landed on `main` as
separate commits afterward.

**Instance A — `refactor/viewer-presets-axis-parity`.** Branch tip
`30c91e6` (2026-05-05 18:29) tuned `LABELS_DEFAULT_OPACITY` from `None`
to `0.25` and adjusted `IMAGE` blending. Merge `4f0cbaa` landed at
18:48. One minute later, `3cae23b` (18:49) landed directly on `main`
fixing
`tests/test_gui_workflows/test_viewer_presets_propagation.py` — a
propagation test asserting against the very constants the branch had
just tuned. The follow-up's own commit message admits: *"broke once
the constant was tuned to 0.25 in the just-merged axis-parity
refactor."*

**Instance B — `fix/phasor-auto-load-on-dataset-switch`.** Branch tip
`0237a2a` (2026-05-05 18:34) shipped the fix and three propagation
tests. Merge `9957846` landed at 18:36. Roughly 16 hours later, in a
different session, `6e6f93b` (2026-05-06 10:58) added a 236-line
compound-learning doc *for the exact bug just fixed*, directly on
`main`.

"Properly committed before merging" means the branch tip is itself a
self-contained artifact: tests in touched modules pass, every doc the
change unlocks is in the branch, and there are no untracked files in
the branch's footprint waiting on a future session. The friction is
invisible at merge time and surfaces during bisect, blame, or "is
this really fixed?" investigations — including the session that
prompted this learning. (session history) The user noticed the gap
themselves mid-session ("it looks like some changes to the
viewer_presets.py were not committed or committed to a branch that
was not merged with main"), but only *after* the merge had already
shipped — the detection happened post-fact, which is exactly when
this rule is meant to fire pre-fact.

## Guidance

Before running `git merge <branch>` into `main`, verify the branch
tip stands alone:

1. **Run propagation tests, not just new tests.** Identify every test
   file that imports or asserts against constants/symbols the branch
   touches, and run them against the branch tip — not just the tests
   added on the branch.
2. **Commit the compound learning on the branch.** If the change
   merits a `docs/solutions/` entry, write and commit it on the fix
   branch *before* merging. A learning doc authored after the merge
   belongs to the merge, not to `main` two timestamps later.
3. **`git status` must be clean of untracked files in the branch's
   touched paths.** Untracked `.md`, `.svg`, or test files in the
   branch footprint are unfinished work pretending to be merged.
4. **No "I'll write the doc next session" debt.** If a doc is
   planned, it is staged or committed now, on this branch — or it is
   explicitly out of scope and tracked under `todos/`. Session
   interruption is a real failure mode (see Instance B below); the
   mitigation is to write the doc *first*, then merge.
5. **Re-grep for the constant/symbol you tuned.** When changing a
   constant value (e.g. `None` → `0.25`), `grep -r` the symbol across
   `tests/` and `src/` to surface dependents the branch didn't touch.

A pragmatic solo pre-merge sweep, run from the branch tip:

```bash
# from the branch tip, before `git checkout main && git merge`
git status                                       # must be clean
git diff --name-only main...HEAD                 # files this branch changes
pytest tests/ -x -q                              # at minimum the dirs touching changed modules
ls docs/solutions/**/*.md 2>/dev/null | xargs grep -l "$(date +%Y-%m-%d)"  # any learning doc dated today already committed?
```

## Why This Matters

The cost is historical, not functional. A logical change like "tune
LABELS opacity to 0.25" now lives across three commits at three
timestamps (`30c91e6` → `4f0cbaa` → `3cae23b`); a future `git bisect`
landing on `4f0cbaa` sees a passing-then-failing-then-passing window
depending on which test the bisect runs. `git blame` on the test
file points at `3cae23b` with no intrinsic link back to the refactor
that necessitated it — the relationship has to be reconstructed from
commit-message archaeology.

For Instance B the cost is sharper: a future session asking "was the
phasor auto-load fix actually merged?" has to cross-reference
`9957846` (the fix) with `6e6f93b` (a doc that was *untracked* when
the next session opened). The session that prompted this learning
*is* that cost — the user's question "track down the changes that
were not merged with main" exists because the merge boundary stopped
being trustworthy.

(session history) Instance B's 16-hour delay was not carelessness —
it was a session-discontinuity accident. The first session called
`/ce-compound`, hit an `AskUserQuestion` prompt at 23:51, and that
prompt went unanswered until 15:52 the next day. The compound doc
was then written in a fresh session and committed direct to `main`
because the original branch was already merged. The lesson is not
"don't get interrupted" — it's "don't merge until the doc is
committed," because session interruption *will* happen, and the only
way to keep the merge boundary clean is to make the doc a
prerequisite of the merge, not a follow-up to it.

Each loose end erodes the "merge = unit of done work" contract that
makes solo `main`-based development viable without a CI gate.

## When to Apply

- Before any local `git merge <feature-branch>` into `main`.
- When tuning a constant or signature on a branch — sweep `tests/`
  and `src/` for the symbol before merging.
- When `/ce-compound` is invoked for a fix that came from a recently
  merged branch: the doc belongs *in* that branch. If the branch is
  already merged, fold the doc into the next related commit on a new
  branch rather than dropping it solo on `main`.
- When a session ends with untracked files in the working tree on
  `main`, before starting unrelated work in the next session.
- When a branch's commit message references a doc, test, or
  follow-up that isn't in `git log <branch>`.

## Examples

**Anti-pattern (what happened):**

Instance A — viewer presets axis parity:

```
30c91e6  2026-05-05 18:29  (branch tip) tune LABELS_DEFAULT_OPACITY None -> 0.25
4f0cbaa  2026-05-05 18:48  Merge branch 'refactor/viewer-presets-axis-parity'
3cae23b  2026-05-05 18:49  fix(test): decouple labels-opacity propagation test from None default
```

The propagation test broke the moment `30c91e6` landed; the merge
happened anyway, and `main` carried a known-red test for 60 seconds.

Instance B — phasor auto-load on dataset switch:

```
0237a2a  2026-05-05 18:34  (branch tip) fix(phasor): auto-load cached phasor on dataset switch
9957846  2026-05-05 18:36  Merge branch 'fix/phasor-auto-load-on-dataset-switch'
6e6f93b  2026-05-06 10:58  docs/solutions/.../phasor-auto-load-skipped-on-dataset-switch-2026-05-06.md
```

The compound-learning doc for the bug fixed in `0237a2a` was
authored 16 hours later, in a different session, and committed
straight to `main`.

**Pattern (what should have happened):**

Instance A as a single coherent merge:

```
abc1111  2026-05-05 18:29  refactor: tune LABELS_DEFAULT_OPACITY None -> 0.25, IMAGE blending
abc2222  2026-05-05 18:35  test: update labels-opacity propagation test for new default
def3333  2026-05-05 18:48  Merge branch 'refactor/viewer-presets-axis-parity'
```

The propagation test fix is on the branch, where it belongs; bisect
and blame both terminate inside one logical unit.

Instance B as a single coherent merge:

```
abc1111  2026-05-05 18:34  fix(phasor): auto-load cached phasor on dataset switch
abc2222  2026-05-05 18:35  docs(solutions): phasor-auto-load-skipped-on-dataset-switch
def3333  2026-05-05 18:36  Merge branch 'fix/phasor-auto-load-on-dataset-switch'
```

The compound learning ships *with* the fix; six months later,
`git show 9957846` (the merge) tells the entire story without a
16-hour gap to a different session's commit.

## Related

- [`ui-bugs/phasor-auto-load-skipped-on-dataset-switch-2026-05-06.md`](../ui-bugs/phasor-auto-load-skipped-on-dataset-switch-2026-05-06.md)
  — Instance B's compound learning, the canonical example of "doc
  authored after main." This is the artifact that should have been
  written inside the fix branch before the merge.
- [`ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md`](../ui-bugs/phasor-apply-visible-as-mask-ignored-filters-2026-05-03.md)
  — companion phasor learning showing the in-branch pattern this
  rule wants to enforce.
- [`architecture-decisions/eliminating-shims-and-temp-fixes.md`](../architecture-decisions/eliminating-shims-and-temp-fixes.md)
  — nearest prior "same-commit / same-branch" rule (file-deletion
  scope); the same-spirit precedent generalized here to "land all
  related artifacts in the same branch."
- [`architecture-decisions/percell4-code-review-findings-phases-0-6.md`](../architecture-decisions/percell4-code-review-findings-phases-0-6.md)
  — echoes the "delete the old file in the same commit" prevention
  rule; supports generalizing to merge-boundary completeness.
- `CLAUDE.md` Documentation Rules — "Archive brainstorms and
  planning docs immediately after implementation." This learning
  operationalizes the same discipline at the merge boundary.
