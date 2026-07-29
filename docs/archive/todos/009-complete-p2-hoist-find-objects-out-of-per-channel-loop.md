---
status: pending
priority: p2
issue_id: "009"
tags: [code-review, performance, measure]
dependencies: []
---

# `measure_multichannel_with_masks` runs `find_objects` + `regionprops` per channel (should be per dataset)

## Problem Statement

`src/percell4/measure/measurer.py:459-484`:

```python
def measure_multichannel_with_masks(images, labels, metrics=None, masks=None):
    ...
    per_channel = {
        ch_name: measure_cells_with_masks(
            image, labels, metrics=metrics, masks=masks
        )
        for ch_name, image in images.items()
    }
    return _merge_multichannel(per_channel)
```

Each call to `measure_cells_with_masks` runs its own `_iter_cell_crops`, which calls `find_objects(labels)` and `regionprops(labels)`. For **4 channels × 10k cells per dataset × 50 datasets**, that is **200 regionprops(labels) calls** instead of **50**.

`regionprops` on a 4k² label array with 10k cells is tens to hundreds of milliseconds per call. The avoidable cost is ~30–60 s wall time per run.

Additionally, the per-round mask boolean operations (`mask[sl] > 0`, `cell_mask & mask_bool`, `cell_mask & ~mask_bool`) are recomputed for every channel — 120k redundant boolean crop operations per dataset that could be 30k.

The commit claimed "single-pass multi-mask measurement" — the single-pass is **per channel**, not **per dataset**, which misses the bigger optimization.

## Findings

- **performance-oracle** (Finding 1a, High): factor out label-side work to run once per dataset.
- **performance-oracle** (Finding 1b, Medium): `_compute_metrics` recomputes `image[mask]` per metric; adds ~15–30% additional overhead.

## Proposed Solutions

### Option A — Precompute crops once, loop channels inside (Recommended)

```python
def measure_multichannel_with_masks(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    metrics: list[str] | None = None,
    masks: dict[str, NDArray[np.uint8]] | None = None,
) -> pd.DataFrame:
    if not images:
        raise ValueError("No channel images to measure")

    metric_names = _validate_metrics(metrics)
    mask_items = list(masks.items()) if masks else []

    if labels.max() == 0:
        channels = list(images.keys())
        return _empty_multichannel_df(channels, metric_names, mask_items)

    # Precompute crops once per dataset, not once per channel.
    crop_meta = []
    for prop in regionprops(labels):
        sl = find_objects(labels)[prop.label - 1]
        if sl is None:
            continue
        cell_mask = labels[sl] == prop.label
        if not cell_mask.any():
            continue
        # Precompute per-round inside/outside once, reuse for every channel.
        per_round = [
            (round_name, cell_mask & (mask[sl] > 0), cell_mask & ~(mask[sl] > 0))
            for round_name, mask in mask_items
        ]
        crop_meta.append((prop, sl, cell_mask, per_round))

    # Now iterate channels, reusing crop_meta.
    rows_per_channel = {ch: [] for ch in images}
    for prop, sl, cell_mask, per_round in crop_meta:
        core = _core_row_from_prop(prop)  # label, centroid, bbox, area
        for ch_name, image in images.items():
            img_crop = image[sl]
            row = dict(core)
            # whole-cell metrics
            for m in metric_names:
                row[m] = (
                    core["area"] if m == "area"
                    else BUILTIN_METRICS[m](img_crop, cell_mask)
                )
            # per-round inside/outside metrics
            for round_name, inside, outside in per_round:
                inside_area = float(np.sum(inside))
                outside_area = float(np.sum(outside))
                for m in metric_names:
                    if m == "area":
                        row[f"{m}_in_{round_name}"] = inside_area
                        row[f"{m}_out_{round_name}"] = outside_area
                    else:
                        row[f"{m}_in_{round_name}"] = BUILTIN_METRICS[m](img_crop, inside)
                        row[f"{m}_out_{round_name}"] = BUILTIN_METRICS[m](img_crop, outside)
            rows_per_channel[ch_name].append(row)

    per_channel_dfs = {ch: pd.DataFrame(rows) for ch, rows in rows_per_channel.items()}
    return _merge_multichannel(per_channel_dfs)
```

Also cache `find_objects(labels)` at the top of the function (one call, not per-prop lookup inside the loop).

- **Pros**: ~3–4× faster on a typical 4-channel × 3-round run. Kills the redundant boolean crop ops. Keeps the public API.
- **Cons**: More bespoke code. `measure_cells_with_masks` (the single-channel helper) remains parallel to this new implementation — but that's intentional; callers that don't need multichannel can keep the simpler path.
- **Effort**: Medium.
- **Risk**: Low-medium. Add a regression test that asserts numerical equivalence with the current (slower) sequential path.

### Option B — Keep the current per-channel loop but cache `find_objects`/`regionprops` externally

A thinner change: compute `slices = find_objects(labels)` and `props = regionprops(labels)` once, thread them through as optional arguments to `measure_cells_with_masks`.

- **Pros**: Smaller diff. Preserves `measure_cells_with_masks` as the building block.
- **Cons**: Doesn't eliminate the redundant per-round boolean ops. Only addresses ~50% of the waste.
- **Effort**: Small.

### Option C — Defer

- **Pros**: No risk now.
- **Cons**: Phase 7 (measure) will be 3–5× slower than it needs to be. Noticeable on 20+ dataset runs.

## Recommended Action

Option A, and add the numerical-equivalence regression test at the same time (see todo #012).

## Technical Details

**Affected files:**
- `src/percell4/measure/measurer.py:459-484`
- `tests/test_workflows/test_measurer_with_masks.py` — add a test that compares single-pass vs sequential outputs on a non-trivial synthetic dataset with overlapping + disjoint masks

**Also consider:** Performance finding 1c — `_merge_multichannel` uses `merge(how="outer")` which is slower and less deterministic than `join` on a label index. Worth a separate todo or a piggyback in the same commit.

## Acceptance Criteria

- [ ] `measure_multichannel_with_masks` calls `find_objects` and `regionprops` **once** per invocation (verify with a mock or counter)
- [ ] Output matches sequential-call baseline numerically for a synthetic test with 2 channels, 2 rounds, overlapping + disjoint masks, cells outside both masks
- [ ] Existing `test_measurer_with_masks.py` tests still pass
- [ ] Benchmark shows measurable speedup on a fixture with 4 channels × 3 rounds × 1000 cells

## Work Log

- 2026-04-10 — Identified by performance-oracle.

## Resources

- `src/percell4/measure/measurer.py` — current implementation
- Review source: performance-oracle Finding 1a (High impact)
