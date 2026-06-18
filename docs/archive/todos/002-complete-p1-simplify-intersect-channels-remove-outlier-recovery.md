---
status: pending
priority: p1
issue_id: "002"
tags: [code-review, simplicity, correctness, workflows]
dependencies: []
---

# Simplify `intersect_channels` — remove outlier-recovery tier (which has a latent bug)

## Problem Statement

`src/percell4/workflows/channels.py:67-100` implements a "recovery tier": if the full channel intersection is empty, the algorithm tries to identify individual datasets that have **zero overlap with any other dataset**, drops them, and re-computes the intersection over the remaining datasets. The idea is to let the config dialog offer a "proceed without these N datasets" prompt.

Two problems with this:

1. **The algorithm has a correctness bug** (flagged by kieran-python-reviewer as Critical). Consider:

   ```python
   sources = [
       ("DS1", ["GFP", "RFP"]),
       ("DS2", ["GFP", "Cy5"]),
       ("DS3", ["RFP", "Cy5"]),
       ("DS4", ["XYZ"]),           # disjoint from everyone
   ]
   ```

   - `full_intersection == set()`
   - Outlier loop: DS1 overlaps DS2 (GFP) and DS3 (RFP) → not outlier. Same for DS2, DS3. DS4 has zero overlap → outlier.
   - `remaining_sets = [DS1, DS2, DS3]`, `set.intersection == set()` → returns `([], ["DS4"])`.

   **This is wrong.** After removing DS4, DS1/DS2/DS3 still have no common channel. The caller is told there's one outlier, drops it, then gets an empty-intersection error on the retry. The docstring at `channels.py:51` explicitly promises the opposite behavior.

2. **The whole recovery tier is speculative** (flagged by code-simplicity-reviewer as Cut #5). The config dialog that would surface the outliers is Phase 2. Nothing in Phase 1 consumes the recovery output except two tests. The simpler rule — "intersection empty → abort" — is enough for Phase 1 and can be augmented in Phase 2 if the actual UX calls for it.

**Resolution:** Rather than fix the buggy recovery (C1), remove it entirely. The bug becomes moot and ~35 LOC + 2 tests evaporate.

## Findings

- **kieran-python-reviewer** (C1, Critical): recovery algorithm misclassifies datasets when chained partial overlaps prevent a full intersection.
- **code-simplicity-reviewer** (Cut #5): the entire recovery tier is speculative — Phase 1 has no consumer, Phase 2 can add whatever outlier-detection shape the dialog actually needs.
- **architecture-strategist** (S7): the "all datasets are outliers" fallback produces a confusing signal for the caller; a proper status enum would be cleaner — but cutting the tier sidesteps the whole concern.

## Proposed Solutions

### Option A — Cut the recovery tier (Recommended)

Replace the current implementation with the minimal rule. Keep the `_ordered` helper; drop the outlier detection loop.

```python
def intersect_channels(
    sources: list[ChannelSource],
) -> tuple[list[str], list[str]]:
    """Compute channel intersection across datasets.

    Returns
    -------
    intersection
        Channels present in every source, ordered as they appear in the
        first source. Empty list if no source has any channel or if the
        intersection is empty.
    outliers
        Datasets that explain why the intersection is empty. Currently
        always returned as ``[name for name, _ in sources]`` when the
        intersection is empty — i.e. "everyone is suspect; the user needs
        to fix the selection." The shape is preserved so Phase 2 can add
        smarter detection later without breaking callers.
    """
    if not sources:
        return [], []

    sets = [set(channels) for _, channels in sources]
    full = set.intersection(*sets) if sets else set()
    if full:
        return _ordered(sources[0][1], full), []
    return [], [name for name, _ in sources]
```

Delete the following tests that pin recovery-tier behavior:
- `test_single_source_dedupes` (the de-dup is only in the single-source fast path)
- `test_one_outlier_dataset` (asserts smart recovery works on a specific shape)
- `test_no_common_channels_at_all` (asserts the "all outliers" fallback for chained overlaps)

Keep: `test_empty_sources`, `test_single_source`, `test_all_datasets_identical`, `test_partial_overlap_preserves_first_order`, `test_intersection_preserves_first_source_order`.

- **Pros**: Removes ~35 LOC of buggy logic, removes 3 tests, sidesteps the chained-overlap bug entirely, matches the simpler rule that Phase 2 needs.
- **Cons**: When the config dialog eventually needs outlier suggestions, we'll re-add something similar. But the re-added version can be shaped by real UX requirements rather than the guess we made in Phase 1.
- **Effort**: Small.
- **Risk**: Low — the consumer (config dialog) does not exist yet.

### Option B — Fix the recovery algorithm

Rewrite the recovery logic to iteratively drop outliers until either an intersection is found or all sources are flagged.

- **Pros**: Preserves the "smart recovery" behavior promised in the docstring.
- **Cons**: Keeps speculative code alive; the iterative loop is still making policy calls the config dialog hasn't earned yet; more code than the problem deserves.
- **Effort**: Medium.
- **Risk**: Medium — easy to introduce a new edge case.

### Option C — Keep as-is and document the bug

- **Pros**: Zero diff.
- **Cons**: Ships a known-wrong algorithm.
- **Effort**: Trivial.
- **Risk**: High — will bite the first real user.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/channels.py:41-101` (the whole function body + helper)
- `tests/workflows/test_channels.py:19,22,46,58` (remove 3 tests)

**Follow-up:** When Phase 2 config dialog is written, if the UX requires outlier suggestions, re-introduce a targeted helper — but shape it against real user interaction, not guessed policy.

## Acceptance Criteria

- [ ] `intersect_channels` is ~20 lines, no recovery loop
- [ ] 5 tests remain in `test_channels.py`, all passing
- [ ] The cut 3 tests are deleted, not commented out
- [ ] No caller of `intersect_channels` exists yet in Phase 1, so no other callsites need updating

## Work Log

- 2026-04-10 — Identified by kieran-python-reviewer (bug) + code-simplicity-reviewer (speculative). Simplicity wins; cut the tier entirely.

## Resources

- Review source: kieran-python-reviewer C1, code-simplicity-reviewer Cut #5, architecture-strategist S7
- File: `src/percell4/workflows/channels.py`
