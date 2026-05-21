---
title: tiff_pending channel-name fallback dropped the `ch` prefix, breaking threshold_compute
date: 2026-05-21
category: logic-errors
module: gui/workflows/single_cell
problem_type: logic_error
component: tooling
symptoms:
  - "`KeyError: channel '02' not in dataset; available: ['ch00', 'ch01', 'ch02']` at `threshold_compute:round_1`"
  - "All `tiff_pending` datasets in a run fail at the same phase after segmentation already succeeded (5–10+ min/dataset wasted)"
  - "Export phase reports `no staging parquets to concatenate`; run finishes failed"
  - "Reproduces only when the user accepted compress-dialog defaults (no channel renames) — `cfg.layer_assignments == {}`"
root_cause: logic_error
resolution_type: code_fix
severity: high
tags:
  - channel-names
  - tiff-pending
  - workflow-config
  - importer
  - threshold-compute
  - hdf5-metadata
  - naming-contract
related_components:
  - src/percell4/adapters/importer.py
  - src/percell4/workflows/phases.py
  - tests/test_gui_workflows/test_channel_name_derivation.py
---

# tiff_pending channel-name fallback dropped the `ch` prefix, breaking threshold_compute

## Problem

The workflow config dialog produced bare token channel names (`"02"`) on `WorkflowDatasetEntry.channel_names` for `tiff_pending` datasets when the user did not rename any channels in the compress dialog. The importer wrote the HDF5 `/metadata.channel_names` with the `ch` prefix (`"ch02"`). The thresholding-round dropdown picked up the bare token, and the runtime channel lookup against the HDF5 raised `KeyError` — but only after a long segmentation pass had already completed for every dataset.

## Symptoms

- `KeyError: channel '02' not in dataset; available: ['ch00', 'ch01', 'ch02']` raised by `_channel_index` at `src/percell4/workflows/phases.py:436-450`.
- Failure surfaces at `threshold_compute:round_1`, **after** Cellpose segmentation has already succeeded (200+ cells across the four-dataset run that motivated this fix).
- All `tiff_pending` datasets in the run fail at the same phase; `export` finds no staging parquets; run finishes failed.
- Only reproduces on the `tiff_pending` + empty `cfg.layer_assignments` path. The `h5_existing` path reads `channel_names` directly from already-`ch`-prefixed HDF5 metadata at `src/percell4/gui/workflows/single_cell/config_dialog.py:842`, and the user-renamed path uses each `LayerAssignment.name`.

## What Didn't Work

The first fix attempt extracted the channel-name derivation into a helper (`_derive_tiff_pending_channel_names`) but **removed the local `layer_assignments = cfg.layer_assignments or {}` binding** from `_add_tiff_via_compress_dialog`. The downstream `layer_assignments_payload` dict-comp still referenced that local name, producing a `NameError` at runtime as soon as the user accepted the compress dialog. The unit suite passed because nothing in `tests/` exercises `_add_tiff_via_compress_dialog` end-to-end — it opens a nested `QDialog` that is awkward to drive from pytest. The regression was caught only by a manual `python main.py` smoke run. Resolved in follow-up commit `82a2523` by keeping the local binding alongside the helper call.

Takeaway: when extracting a helper, scan downstream code in the same function for references to any local you drop. Unit-testing the extracted helper does not prove its caller still compiles.

## Solution

Single source of truth for the channel-name fallback, mirroring `src/percell4/adapters/importer.py:211`.

**Before** (inlined inside `_add_tiff_via_compress_dialog`):

```python
selected_token_ids = sorted(cfg.selected_channels)
layer_assignments = cfg.layer_assignments or {}
channel_names = [
    (layer_assignments[ch_id].name
     if ch_id in layer_assignments and layer_assignments[ch_id].name
     else ch_id)        # ← bare token: silent mismatch with importer
    for ch_id in selected_token_ids
]
```

**After** (`src/percell4/gui/workflows/single_cell/config_dialog.py`):

```python
def _derive_tiff_pending_channel_names(
    selected_token_ids: list[str],
    layer_assignments: dict[str, Any],
) -> list[str]:
    """Fall back to f"ch{ch_id}" so the dialog matches importer.py."""
    out: list[str] = []
    for ch_id in selected_token_ids:
        override = layer_assignments.get(ch_id)
        name = getattr(override, "name", "") if override is not None else ""
        out.append(name or f"ch{ch_id}")
    return out
```

Call site:

```python
selected_token_ids = sorted(cfg.selected_channels)
layer_assignments = cfg.layer_assignments or {}   # KEEP — payload dict-comp below depends on it
channel_names = _derive_tiff_pending_channel_names(
    selected_token_ids, layer_assignments,
)
```

Regression coverage at `tests/test_gui_workflows/test_channel_name_derivation.py` pins:

- empty `layer_assignments` → `ch`-prefixed names (the original bug);
- override `name` wins when present;
- partial overrides mix correctly with `ch`-prefix defaults;
- empty `.name` falls back to `ch`-default;
- `None` value in `layer_assignments` falls back to `ch`-default;
- empty token list returns `[]`.

Fix branch `fix/channel-name-prefix-tiff-pending`; commits `dd4a30e` (extraction + prefix fix) and `82a2523` (restore local binding); merged in `9da8697`.

## Why This Works

The producer side (`src/percell4/adapters/importer.py:211`) writes `default_name = f"ch{ch_key}" if ch_key else "ch0"` into `/metadata.channel_names` whenever no `LayerAssignment` override is provided. The consumer side (`_channel_index`, called from `threshold_compute`) does a strict-equality lookup against that stored list. By making the dialog's fallback emit identical strings, the name the user picks in the rounds table is guaranteed to exist in `store.metadata["channel_names"]` at threshold time. The lookup itself is unchanged — the producer was wrong, not the consumer.

## Prevention

- **Treat `f"ch{ch_id}"` as a single API contract spanning two files.** Both `src/percell4/adapters/importer.py:211` and `_derive_tiff_pending_channel_names` now carry a comment pointing at the other. Any future change to one must update both. Code review on either file should look for the partner.
- **Pin the importer convention with a unit test.** Add coverage that imports a TIFF dataset with no `layer_assignments` and asserts `store.metadata["channel_names"] == ["ch00", "ch01", ...]`. If a future change drops the `ch` prefix on the importer side, the test fails immediately with a pointer back to the dialog helper.
- **Add `pytest-qt` coverage for `_add_tiff_via_compress_dialog`.** A test that monkeypatches `CompressDialog.exec_` to return `Accepted` with a stub `compress_config` would exercise the full payload-build path. This is the gap that let the `NameError` regression land green on unit tests.
- **Cross-phase invariant check at workflow start.** Validate in Phase 0 (compress) that every `WorkflowDatasetEntry.channel_names` value referenced by any `ThresholdingRound.channel` will resolve against what `import_dataset` is about to write. Failing loudly at run start beats failing after segmentation.
- **Nearest-name suggestion in `_channel_index`.** The error already shows `available:` (which did help diagnosis), but adding `did you mean 'ch02'?` for misses by a common prefix/suffix would cut diagnosis to seconds. Cheap to add at `src/percell4/workflows/phases.py:447-449`.
- **Audit-matrix entry.** Add a row to `docs/audits/canonical-sources-matrix.yaml` keyed on the channel-name `ch`-prefix contract, with `applies_to` covering both `src/percell4/adapters/importer.py` and `src/percell4/gui/workflows/single_cell/config_dialog.py` — so the `PreToolUse` learnings hook surfaces this doc on edits to either file.

## Related Issues

- `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` — documents `f"ch{i}"` as the orphan-slice fallback in the deletion path. This new doc elevates the same string as the *default for fresh import* of unnamed channels. Cross-reference would close the loop.
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — Bugs #3/#4 are the prior compress-dialog → downstream handoff regressions; this is a sibling in that family (identity leaking incorrectly across the boundary).
- `docs/solutions/architecture-patterns/channel-deletion-permanence.md` — owns the writer side of the channel-name lifecycle; its `applies_to` could widen to cover any module that **constructs** channel names, not just deletes them.
- `docs/solutions/architecture-patterns/consolidate-canonical-state-over-per-module-overrides-2026-05-14.md` — generalises the "re-derive a canonical value in a second module" anti-pattern this bug instantiated.
- `docs/audits/canonical-sources-matrix.yaml` — no current entry covers the channel-naming contract; the new helper plus `importer.py:211` is a clean T1 canonical pair.
