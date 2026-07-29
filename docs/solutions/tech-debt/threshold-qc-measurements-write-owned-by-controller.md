---
title: "ThresholdQCController.write_measurements_to_store is a compat shim"
category: tech-debt
date: 2026-04-10
last_refreshed: 2026-06-15
references:
  - src/percell4/gui/threshold_qc.py
  - src/percell4/gui/grouped_seg_panel.py
  - src/percell4/gui/workflows/single_cell/threshold_qc_queue.py
  - docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md
---

# ThresholdQCController.write_measurements_to_store — temporary compat shim

## What the flag does today

`ThresholdQCController.__init__` accepts `write_measurements_to_store: bool = True`. When the flag is `True` (the default, preserving existing behaviour), `_finalize` writes the updated per-cell measurements DataFrame to `/measurements` inside the dataset's HDF5 file. When `False`, the `/measurements` write is skipped but everything else — the `/masks/<name>` union mask, the `/groups/<name>` group-assignment DataFrame, and the `CellDataModel.set_measurements(df)` call — still happens.

The flag exists so the batch workflow runner (`src/percell4/workflows/` + `src/percell4/gui/workflows/`) can:

1. Reuse `ThresholdQCController` verbatim for per-dataset threshold QC
2. Own cross-dataset measurement persistence itself (Parquet + CSVs in the run folder), without having each dataset's h5 accumulate a stale `/measurements` group that would contradict the workflow's provenance invariant ("each `.h5` contains only image data + metadata + labels + masks; the measurement DataFrame lives only in the run folder")

**Status (2026-06-15):** the runner now exists. The single-cell workflow's `threshold_qc_queue.py` (`ThresholdQCQueueEntry` / `TimelapseThresholdQCQueueEntry`) constructs `ThresholdQCController(write_measurements_to_store=False)`, so the provenance invariant still holds and is now actively relied upon. `GroupedSegPanel` and the dilute-phase controller remain the callers relying on the default `True`. The shim is still in place — the flag has **not** been removed — so this note is still live.

## Why this is a shim, not a permanent API

A boolean parameter named "write this specific thing to this specific storage layer" is a code smell: the controller is doing persistence that the caller didn't ask for. The correct long-term shape is for `ThresholdQCController` to *return* the computed measurements DataFrame to its caller and let the caller decide what to do with it:

```python
# Today:
on_complete: Callable[[bool, str], None]
# finalize writes /measurements to the store unless the flag says not to

# Long-term:
on_complete: Callable[[bool, str, pd.DataFrame], None]
# finalize returns the DataFrame via the callback; never touches the store's
# /measurements group. The caller (GroupedSegPanel OR the workflow runner)
# decides whether and where to persist it.
```

With the callback-based design, the flag disappears, the controller has a single well-defined output contract, and the workflow runner no longer has to "know" about an internal persistence toggle.

## Migration plan

1. **Phase 1 (done)**: the additive flag ships. Default `True` preserves `GroupedSegPanel` behaviour.
2. **Phase 2 runner (done, 2026-06-15)**: the runner's `threshold_qc_queue.py` constructs `ThresholdQCController(write_measurements_to_store=False)` per dataset and owns measurement persistence in its run-folder Parquet. (Note: `ThresholdQCController` since gained a 3-arg `on_complete(success, msg, mask)` variant — but it forwards the **mask**, not the measurements DataFrame the long-term shape below envisions, and the `write_measurements_to_store` flag still coexists. The refactor in step 3 is therefore still pending.)
3. **Follow-up refactor** (next time `GroupedSegPanel` or `ThresholdQCController` gets meaningful work):
   - Add a 3-arg `on_complete(success, msg, measurements_df)` callback variant to `ThresholdQCController.__init__`.
   - Migrate `GroupedSegPanel` to the 3-arg variant. Have the panel (not the controller) call `self._current_store.write_dataframe("/measurements", df)` after a successful completion.
   - Migrate the workflow runner (by then in production) to the 3-arg variant as well — it was already passing `write_measurements_to_store=False`, so it just stops caring about the flag.
   - Delete the `write_measurements_to_store` parameter and the `/measurements` branch inside `_finalize`. `ThresholdQCController._finalize` now writes only mask + groups DF to the h5 and returns the measurements DataFrame through the callback.
4. **Cleanup**: delete this tech-debt note.

## Code references

- `src/percell4/gui/threshold_qc.py` — `ThresholdQCController.__init__` accepts the flag; `_finalize` gates the `/measurements` write on it
- `src/percell4/gui/grouped_seg_panel.py` — the one current non-workflow caller; constructs the controller without specifying the flag (default `True`)
- `docs/plans/2026-04-10-feat-single-cell-thresholding-workflow-plan.md` — Phase 1 plan that introduced the flag and promised this note

## Acceptance criteria for removal

- `ThresholdQCController.__init__` no longer accepts `write_measurements_to_store`
- `ThresholdQCController._finalize` never writes `/measurements` to any store
- The 3-arg `on_complete(success, msg, measurements_df)` variant is the only callback shape
- `GroupedSegPanel` uses the 3-arg variant and calls `store.write_dataframe("/measurements", df)` itself
- The batch workflow runner uses the 3-arg variant and writes to its run-folder Parquet
- This file is deleted
