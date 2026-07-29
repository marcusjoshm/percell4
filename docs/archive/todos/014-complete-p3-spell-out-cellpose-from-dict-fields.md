---
status: pending
priority: p3
issue_id: "014"
tags: [code-review, quality, workflows]
dependencies: ["003"]
---

# `_cellpose_from_dict` uses `**d` — asymmetric with other helpers, breaks on schema drift

## Problem Statement

`src/percell4/workflows/artifacts.py:122-123`:

```python
def _cellpose_from_dict(d: dict[str, Any]) -> CellposeSettings:
    return CellposeSettings(**d)
```

Every **other** `_X_from_dict` helper spells out its fields explicitly:
- `_round_from_dict` (artifacts.py:139-149)
- `_entry_from_dict` (artifacts.py:163-170)
- `_failure_from_dict` (artifacts.py:201-208)

`_cellpose_from_dict` is the odd one out — it uses `**d`, which is the exact footgun the commit message claims to have avoided. If a future `CellposeSettings` drops a field (e.g., after todo #003 removes `batch_size`) or adds a required one, `**d` silently breaks: unknown keys raise `TypeError` at the point of use, known-missing fields fall back to defaults without warning.

Because `CellposeSettings` is `frozen=True, slots=True, kw_only=True`, a `**d` with an unknown key raises `TypeError: got unexpected keyword argument 'batch_size'` — cryptic for a user loading an older `run_config.json`.

## Findings

- **kieran-python-reviewer** (N6): spells out the asymmetry and the schema-drift brittleness.

## Proposed Solutions

### Option A — Spell out fields explicitly (Recommended)

```python
def _cellpose_from_dict(d: dict[str, Any]) -> CellposeSettings:
    return CellposeSettings(
        model=d.get("model", "cpsam"),
        diameter=d.get("diameter", 30.0),
        gpu=d.get("gpu", True),
        flow_threshold=d.get("flow_threshold", 0.4),
        cellprob_threshold=d.get("cellprob_threshold", 0.0),
        min_size=d.get("min_size", 15),
    )
```

(After todo #003 removes `batch_size` and `channel_idx`; if they stay, include them.)

- **Pros**: Matches the pattern of the other helpers. Robust to schema drift — unknown keys in `d` are silently ignored, missing keys fall back to dataclass defaults.
- **Cons**: The dataclass defaults are now duplicated in the loader. Minor — flagged in N7 of the review as a follow-up.
- **Effort**: Trivial.

### Option B — Filter `d` to only known field names before `**d`

```python
def _cellpose_from_dict(d: dict[str, Any]) -> CellposeSettings:
    known = {f.name for f in fields(CellposeSettings)}
    return CellposeSettings(**{k: v for k, v in d.items() if k in known})
```

- **Pros**: Preserves the splat style, tolerates unknown keys, no duplication.
- **Cons**: More clever. The `fields()` import pulls in `dataclasses.fields` at every call (trivial overhead).

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/workflows/artifacts.py:122-123`

**Depends on #003** — if `batch_size`/`channel_idx` are removed first, the explicit list becomes shorter.

## Acceptance Criteria

- [ ] `_cellpose_from_dict` spells out each field with `d.get(..., default)`
- [ ] Loading a `run_config.json` from a prior version (missing a field) works via defaults
- [ ] Loading a `run_config.json` with an unknown key does not raise TypeError
- [ ] Existing round-trip tests pass

## Work Log

- 2026-04-10 — Flagged by kieran-python-reviewer.

## Resources

- Review source: kieran-python-reviewer N6
- `src/percell4/workflows/artifacts.py:122-123`
