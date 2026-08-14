---
status: pending
priority: p2
issue_id: "008"
tags: [code-review, correctness, cellpose]
dependencies: []
---

# `build_cellpose_model` default `model_type="cpsam"` crashes on Cellpose 3.x

## Problem Statement

`src/percell4/segment/cellpose.py:25-41` — the new `build_cellpose_model` helper has a default:

```python
def build_cellpose_model(model_type: str = "cpsam", gpu: bool = False):
    ...
    if version >= 4:
        return models.CellposeModel(gpu=gpu)
    model_cls = getattr(models, "Cellpose", models.CellposeModel)
    return model_cls(model_type=model_type, gpu=gpu)
```

On Cellpose 4.x, `"cpsam"` is the one and only model and the parameter is ignored. On Cellpose 3.x, `"cpsam"` is **not** a valid model name — v3 expects `"cyto3"`, `"cyto2"`, `"cyto"`, or `"nuclei"`. A caller on a v3 environment calling `build_cellpose_model()` with no arguments will instantiate `models.Cellpose(model_type="cpsam")` and crash.

The existing `SegmentationPanel` code path passes `model_type` explicitly based on a combo box, so this has not bitten anyone. But the batch workflow runner (Phase 2) will call `build_cellpose_model(**cellpose_settings_as_dict)` — and if the `model` field defaults to `"cpsam"`, a v3 machine will crash at Phase 1 of every run.

## Findings

- **kieran-python-reviewer** (S8): the default is misleading for v3 users and guarantees a crash for the first batch workflow caller on a v3 machine.

## Proposed Solutions

### Option A — Branch on version in the default (Recommended)

```python
def build_cellpose_model(
    model_type: str | None = None,
    gpu: bool = False,
):
    from cellpose import models

    version = _get_cellpose_version()
    if version >= 4:
        return models.CellposeModel(gpu=gpu)

    if model_type is None:
        model_type = "cyto3"  # v3 default
    model_cls = getattr(models, "Cellpose", models.CellposeModel)
    return model_cls(model_type=model_type, gpu=gpu)
```

Also update `run_cellpose`'s signature:

```python
def run_cellpose(
    image: NDArray,
    model_type: str | None = None,
    ...
)
```

And inside `run_cellpose`, default to the v4-compatible `cpsam` (or v3-compatible `cyto3`) via the same branching:

```python
if version >= 4:
    if model is None:
        model = build_cellpose_model(gpu=gpu)
    ...
else:
    if model is None:
        model = build_cellpose_model(model_type=model_type or "cyto3", gpu=gpu)
    ...
```

- **Pros**: Fixes the v3 crash. Callers on v4 keep their current behavior. Callers on v3 that passed `model_type` explicitly are unaffected.
- **Cons**: Slight behavior change in `run_cellpose` when `model_type` is unspecified on v4 (now passes `None` → defaults to `cpsam` inside the branch, same as before). Verify the `SegmentationPanel` still works.
- **Effort**: Small.
- **Risk**: Low.

### Option B — Keep the default but validate and raise

```python
if version < 4 and model_type == "cpsam":
    raise ValueError(
        "model_type='cpsam' is only valid on Cellpose 4.x. "
        "On Cellpose 3.x, use 'cyto3' / 'cyto2' / 'cyto' / 'nuclei'."
    )
```

- **Pros**: Explicit error message rather than a cryptic Cellpose crash.
- **Cons**: Forces every v3 caller to override the default.
- **Effort**: Trivial.

### Option C — Document the limitation and leave behavior as is

- **Pros**: Zero diff.
- **Cons**: Phase 2 runner will crash on the first v3 user.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/segment/cellpose.py:25-112`

**Testing:** hard to test both branches in one environment. Add a unit test that mocks `_get_cellpose_version()` and asserts `build_cellpose_model(model_type=None, gpu=False)` uses the correct default for each version.

## Acceptance Criteria

- [ ] `build_cellpose_model()` with no arguments works on both v3 and v4
- [ ] `run_cellpose(image)` with no arguments works on both v3 and v4
- [ ] A unit test mocks the version and verifies the default is `cyto3` on v3 and `cpsam` on v4
- [ ] Existing `SegmentationPanel` flow still works

## Work Log

- 2026-04-10 — Flagged by kieran-python-reviewer.

## Resources

- `src/percell4/segment/cellpose.py`
- Review source: kieran-python-reviewer Should #8
