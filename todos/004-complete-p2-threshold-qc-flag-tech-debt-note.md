---
status: pending
priority: p2
issue_id: "004"
tags: [code-review, tech-debt, docs, workflows]
dependencies: []
---

# Create the tech-debt note promised by the plan for `write_measurements_to_store` flag

## Problem Statement

The plan (`docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md:720`) explicitly says:

> Record in `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` (to be created during Phase 1).

This file was not created. The `write_measurements_to_store` flag on `ThresholdQCController.__init__` is a compatibility shim — by the plan's own admission — that exists only so the batch workflow runner can skip the `/measurements` h5 write while `GroupedSegPanel` continues to work unchanged.

Without the note, the next engineer to touch `ThresholdQCController` has no documented justification for removing the flag, and the "Phase 1" framing of the commit will make future-you assume the flag is permanent API.

## Findings

- **architecture-strategist** (S1): tech-debt note promised by plan line 720 is missing.
- The `docs/solutions/tech-debt/` directory does not exist in the repo yet.
- The proper long-term fix is a 3-arg `on_complete(success, msg, measurements_df)` callback variant so the caller owns measurement persistence — the flag becomes unnecessary.

## Proposed Solutions

### Option A — Create the tech-debt note only (Recommended)

Write `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` describing:

1. What the flag is today (`write_measurements_to_store: bool = True` gate on `/measurements` h5 write in `_finalize`)
2. Why it exists (batch workflow needs to skip the write; additive-only Phase 1 constraint)
3. What the correct long-term shape is (3-arg `on_complete(success, msg, measurements_df)` callback)
4. The migration plan: when `GroupedSegPanel` is next refactored, add the 3-arg variant, migrate both the workflow runner and `GroupedSegPanel` to use it, then delete the flag
5. Code references: `gui/threshold_qc.py:77-105` (ctor), `gui/threshold_qc.py:720-725` (gated write)

- **Pros**: Honors the plan's own commitment. Low effort. Prevents the shim from becoming permanent by accident.
- **Cons**: None meaningful.
- **Effort**: Small.

### Option B — Ship the 3-arg `on_complete` variant now

Add an overload to `ThresholdQCController.__init__` that accepts a 3-arg callback, and have the workflow runner use it when it lands in Phase 2. The flag stays for v1 backward compat.

- **Pros**: Unblocks the Phase 2 runner (which needs the DF back from the controller anyway).
- **Cons**: Adds complexity mid-stream. Better done as a deliberate refactor with its own commit.
- **Effort**: Medium.

### Option C — Both

Do Option A now (5 minutes) and queue Option B as its own todo for when Phase 2 runner lands.

## Recommended Action

Option C — write the tech-debt note now; Phase 2 will add the 3-arg variant when it needs it.

## Technical Details

**New file:** `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md`

Template sections:
- Problem (what the flag does today)
- Why it exists (compat shim, additive-only Phase 1)
- Correct long-term shape (3-arg on_complete)
- Migration plan
- Code references with file:line
- Date recorded: 2026-04-10

## Acceptance Criteria

- [ ] `docs/solutions/tech-debt/` directory exists
- [ ] `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` exists
- [ ] Note describes what the flag is, why it's a shim, and what replaces it
- [ ] Plan line 720 reference matches the actual filename

## Work Log

- 2026-04-10 — Identified by architecture-strategist as a commitment the plan made and the commit did not deliver.

## Resources

- Plan line 720 (the promise)
- `src/percell4/gui/threshold_qc.py:77-105` (the constructor with the flag)
- `src/percell4/gui/threshold_qc.py:720-725` (the gated write)
