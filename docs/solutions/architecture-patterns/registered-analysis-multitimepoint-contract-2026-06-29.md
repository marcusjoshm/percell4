---
title: "Registered analyses are multi-timepoint by contract (per-frame loop + aggregate)"
date: 2026-06-29
last_updated: 2026-06-29
category: architecture-patterns
module: percell4.application.use_cases.run_analysis
problem_type: architecture_pattern
component: tooling
severity: medium
canonical_source: src/percell4/application/use_cases/run_analysis.py
applies_to:
  - "src/percell4/application/use_cases/run_analysis*.py"
  - "src/percell4/application/analysis/modules/**/*.py"
  - "src/percell4/domain/analysis/_impl/**/*.py"
status: canonical_clean
tags:
  - analysis
  - registered-analysis
  - multi-timepoint
  - time-lapse
  - run-analysis
  - aggregate-timepoints
  - table-output
  - image-output
applies_when:
  - adding or modifying a registered analysis or its pure core
  - reasoning about time-lapse "(T, ...)" output shape
  - touching run_analysis or _aggregate_timepoints
---

# Registered analyses are multi-timepoint by contract

A registered analysis's pure core never knows about time. It receives 2D
arrays and returns a flat output dict. The **framework** — `run_analysis` — is
what makes every registered analysis run correctly over a time-lapse dataset:
it loops the timepoints, calls the same 2D core once per frame, and aggregates.
A new analysis whose core is 2D and rank-agnostic is therefore multi-timepoint
"for free," with no per-analysis code.

## Context

`run_analysis(analysis_name, h5_path, layer_map, ...)` is the single Python-API
entry point. After role/param validation it reads the dataset metadata once and
branches on a **single discriminator**:

```python
_meta = DatasetStore(h5_path).metadata
n_timepoints = int(_meta.get("n_timepoints", 1) or 1)
```

`n_timepoints` is the only thing that decides the path — never `array.ndim`. The
`or 1` guard makes a missing/`None`/`0` value collapse to a single frame.

## Guidance

The two branches:

- **`n_timepoints <= 1` (single frame):** ONE call —
  `load_layers(h5_path, layer_map, roles_dict)` yields 2D arrays, the pure core
  runs on them, and the returned dict is used as-is. The output is
  **byte-identical** to a non-time-lapse run: no `timepoint` column is added and
  no leading axis is stacked. The numeric-parity characterization fixtures
  depend on this identity.

- **`n_timepoints > 1` (time-lapse):** the framework loops —

  ```python
  per_t = []
  for t in range(n_timepoints):
      arrays_t = load_layers(h5_path, layer_map, roles_dict, timepoint=t)
      per_t.append((t, run_callable(arrays_t, resolved, **run_kwargs)))
  outputs = _aggregate_timepoints(per_t, cls)
  ```

  The same 2D core runs once per frame; only the loaded slice changes.

`_aggregate_timepoints(per_t, cls)` collapses the per-frame dicts into one, by
declared output kind:

- **`TableOutput`** — each frame's DataFrame is given a `timepoint` column and
  the frames are `pd.concat`'d (`ignore_index=True`). Output names are unioned
  across frames, so a `produced_when` table absent on some frames is tolerated.
- **`ImageOutput`** — frames are `np.stack(..., axis=0)` into exactly `(T, ...)`.
  This is an **exact-T contract**: a frame that omitted a key contributes a
  `np.zeros_like(template)` plane (template = the first frame that did emit it)
  so the stack is never short. A short stack would later raise
  `LayerSizeMismatchError` at write time. Note the deliberate asymmetry — tables
  tolerate absent frames; images must stay an exact-T stack.

## Why This Matters

The contract keeps the math layer pure and rank-agnostic while concentrating
all time handling in one place. Pure cores under `domain/analysis/_impl/` and
the declarative `run()` in `application/analysis/modules/` stay 2D; they cannot
silently break on a 4D dataset because they never see one. Conversely, the
`(T, ...)` ImageOutput shape and the `timepoint`-columned table are a stable
contract the batch runner and tests rely on — breaking the exact-T stacking
surfaces as a write-time `LayerSizeMismatchError`, not as silently dropped
frames.

## When to Apply

- Adding a new registered analysis: write the pure core for **one** 2D frame.
  Do not add a timepoint loop, a `timepoint` column, or a leading T axis inside
  the analysis — the framework owns all three.
- Modifying `run_analysis` or `_aggregate_timepoints`: preserve the single-frame
  byte-identity (no `timepoint` column, no stacking when `n_timepoints <= 1`)
  and the exact-T ImageOutput stacking (zero-pad absent frames; never emit a
  short stack).
- Reasoning about `(T, ...)` output: read the discriminator and the aggregator,
  not the array rank.

## Examples

A core that returns (illustratively) `{"some_table": <DataFrame>, "some_mask":
<2D ndarray>}` per frame becomes, on a 5-timepoint dataset: a `some_table` with a
`timepoint` column spanning all frames, and a `some_mask` ImageOutput of shape
`(5, H, W)`. On a single-timepoint dataset the exact same core returns the bare
DataFrame (no `timepoint` column) and a `(H, W)` mask — identical to running the
original standalone script.

## Related

- `docs/solutions/architecture-patterns/registered-analysis-framework.md` — the
  parent pattern (how to *add* an analysis: pure core + module + CLI + dialog).
  Its `date:` is 2026-05-28 — it predates this contract — but it has since been
  refreshed (`last_updated: 2026-06-29`) to note that registered analyses are
  multi-timepoint by contract and to point here; this entry is the "how it runs
  over T" companion.
- `docs/solutions/architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md`
  — the strongest conceptual sibling in the `domain/measure/` layer: same
  recipe (keep the core rank-agnostic, loop per-frame in the caller, emit
  exactly-T planes). `_aggregate_timepoints`'s exact-T ImageOutput stacking is
  the analysis-layer mirror of that doc's store `(T, H, W)` exact-T emission.
- `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md` — the
  post-validation `_add_area_um2_columns` step that runs on every `TableOutput`
  after aggregation, including the concatenated time-lapse tables.
- `docs/solutions/architecture-patterns/decay-write-path.md` — weak link: only
  the leading-T `(T, ...)` store layout.
