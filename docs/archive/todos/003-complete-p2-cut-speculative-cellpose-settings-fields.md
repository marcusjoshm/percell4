---
status: pending
priority: p2
issue_id: "003"
tags: [code-review, simplicity, workflows, yagni]
dependencies: []
---

# Cut speculative fields: `batch_size`, `channel_idx`, `schema_version`

## Problem Statement

Three fields added to `workflows/models.py` are written, validated, round-tripped through JSON, asserted in tests, and **never read by any consumer in this commit or the Phase 2 plan**:

1. **`CellposeSettings.batch_size`** (`models.py:68`) — `run_cellpose()` and `build_cellpose_model()` do not accept a batch_size parameter. Cross-image batching is explicitly listed in the plan as **P-OPT-9, "not in initial scope; add to backlog"** (plan.md:624). Pure dead field.

2. **`CellposeSettings.channel_idx`** (`models.py:70`) — the workflow plan resolves the segmentation channel via `DatasetStore.read_channel(path, idx)` at the *call site*, not from the recipe. Nothing in Phase 1 reads `c.channel_idx`. The Phase 2 shape is likely to be a channel *name* (resolved against the dataset's channel list at runtime), not the integer index stored here.

3. **`WorkflowConfig.schema_version`** (`models.py:152`) — version 1 has no version 0 to migrate from. `config_from_dict` (`artifacts.py:187`) uses `data.get("schema_version", 1)` so the field isn't even enforced. Documentation masquerading as code.

## Findings

- **code-simplicity-reviewer** (Cuts #1, #2, #3): all three flagged as YAGNI violations.
- Plan line 624 explicitly defers Cellpose batching to backlog.
- `test_artifacts.py:50` asserts `batch_size=16` round-trips — but that's the only consumer.
- `config_from_dict`'s silent default for `schema_version` makes the field load-bearing for nothing.

## Proposed Solutions

### Option A — Delete all three (Recommended)

Remove each field, its `__post_init__` validator, its dict entry in `artifacts.py`, and any test fixture that sets it.

- **Pros**: ~20 LOC cut. Prevents the "knob we might want later" anti-pattern. Fields re-added at the moment the first consumer lands, with the right type.
- **Cons**: If/when cross-image Cellpose batching lands, `batch_size` must be re-added. If schema evolves, `schema_version` must be re-added with a real migration. These are cheap to add *at the right time*.
- **Effort**: Small.
- **Risk**: Low — no consumers exist to break.

### Option B — Keep the fields with a `TODO(phase2)` comment

- **Pros**: Zero diff.
- **Cons**: Entrenches speculative API.
- **Effort**: Trivial.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/models.py` — remove three fields + validators
- `src/percell4/workflows/artifacts.py` — remove the fields from `_cellpose_to_dict`, `_cellpose_from_dict`, `config_from_dict`, `config_to_dict`
- `tests/workflows/test_models.py` — update `CellposeSettings` tests (drop batch_size/channel_idx cases)
- `tests/workflows/test_artifacts.py` — update `_sample_config` fixture (drop `batch_size=16`, drop `schema_version` assertions)

**Note:** Leave the `# TODO(phase2): promote to CompressPlan dataclass` comment on `WorkflowDatasetEntry.compress_plan` — that field IS consumed by a validator in Phase 1 (`models.py:141`), it's just schema-free. See todo #015.

## Acceptance Criteria

- [ ] `CellposeSettings` has no `batch_size` or `channel_idx` fields
- [ ] `WorkflowConfig` has no `schema_version` field
- [ ] `artifacts.py` serialization helpers do not read/write the removed fields
- [ ] All `test_models.py` and `test_artifacts.py` tests still pass
- [ ] Plan document updated to remove Phase 1 mentions of these fields (if any)

## Work Log

- 2026-04-10 — Identified by code-simplicity-reviewer. User explicitly asked for simpler scope.

## Resources

- Review source: code-simplicity-reviewer Cuts #1, #2, #3
- Plan line 624: "P-OPT-9 Cellpose batching (follow-up) — not in initial scope; add to backlog"
