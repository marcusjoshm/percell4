---
status: pending
priority: p3
issue_id: "018"
tags: [code-review, simplicity, workflows]
dependencies: ["014"]
---

# Collapse artifacts.py hand-written to_dict helpers using `dataclasses.asdict`

## Problem Statement

`src/percell4/workflows/artifacts.py:109-244` has 10 small serialization helpers:
`_cellpose_to_dict`, `_cellpose_from_dict`, `_round_to_dict`, `_round_from_dict`, `_entry_to_dict`, `_entry_from_dict`, `_failure_to_dict`, `_failure_from_dict`, `metadata_to_dict`, `metadata_from_dict`, plus `config_to_dict` / `config_from_dict`.

The **reason** the code is explicit (not `asdict`) is that `dataclasses.asdict` round-trips as nested dicts — the *from_dict* direction genuinely needs explicit reconstruction for `Path`, nested dataclasses, and `StrEnum` values. But the **to_dict** direction can use `asdict` + the `_json_default` encoder and get the same result for free.

This could collapse from 8 `_X_to_dict` functions to 1 `config_to_dict` that just returns `asdict(cfg)`. The `_X_from_dict` functions stay — that's the actually-hard part.

Estimated reduction: ~80 LOC. Surface that looks like boilerplate: gone.

## Findings

- **code-simplicity-reviewer** (Simplify #7): 11 helpers should be 4.

## Proposed Solutions

### Option A — Use `asdict` for the to_dict direction, keep explicit from_dict (Recommended)

```python
from dataclasses import asdict


def config_to_dict(cfg: WorkflowConfig) -> dict[str, Any]:
    """Serialize a WorkflowConfig to a JSON-safe dict.

    Uses dataclasses.asdict for the forward direction; _json_default at
    the json.dumps layer handles Path / datetime / Enum coercion.
    """
    return asdict(cfg)


def metadata_to_dict(meta: RunMetadata) -> dict[str, Any]:
    return asdict(meta)
```

Delete `_cellpose_to_dict`, `_round_to_dict`, `_entry_to_dict`, `_failure_to_dict`.

Keep `config_from_dict`, `metadata_from_dict`, and **all** the `_X_from_dict` helpers — they do the load-time reconstruction work that `asdict` can't reverse.

- **Pros**: ~80 LOC removed. `config_to_dict` becomes a one-liner. Adding a new field to `CellposeSettings` requires updating exactly one `_cellpose_from_dict` function, and the `asdict` direction updates automatically.
- **Cons**: `asdict` recursively converts dataclasses and enums to plain values, so the output is a dict-of-dicts-of-enums/paths/datetimes. The existing `_json_default` encoder handles all of those at `json.dumps` time, but verify the round-trip test still passes.
- **Effort**: Small.
- **Risk**: Low — the existing `test_config_roundtrip_dict` test will catch any regression.

### Option B — Keep the hand-written helpers

- **Pros**: Zero diff.
- **Cons**: 10 helpers doing boilerplate work that `asdict` already does.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/artifacts.py:109-244`

**Verify:**
- `asdict` on `WorkflowConfig` (frozen) works the same as current explicit serialization — StrEnums are preserved as Enum instances in the dict, and `_json_default` converts them at `json.dumps` time.
- `asdict` on `RunMetadata` with a non-empty `failures` list serializes `FailureRecord.failure` as an Enum instance and `ts` as a datetime.

**Depends on:** todo #014 (spelling out `_cellpose_from_dict` fields). Can be done in either order.

## Acceptance Criteria

- [ ] `_cellpose_to_dict`, `_round_to_dict`, `_entry_to_dict`, `_failure_to_dict` are deleted
- [ ] `config_to_dict` and `metadata_to_dict` use `asdict`
- [ ] All `test_artifacts.py` round-trip tests pass
- [ ] JSON serialization of the `asdict` result works via existing `_json_default`
- [ ] `_X_from_dict` helpers remain untouched

## Work Log

- 2026-04-10 — Flagged by code-simplicity-reviewer.

## Resources

- Review source: code-simplicity-reviewer Simplify #7
- `src/percell4/workflows/artifacts.py:109-244`
