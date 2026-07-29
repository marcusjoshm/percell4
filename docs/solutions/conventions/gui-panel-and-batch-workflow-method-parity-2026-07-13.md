---
title: "GUI creator panels and batch/workflow methods must map 1:1 (name and parameters)"
date: 2026-07-13
category: conventions
module: src/percell4/workflows
problem_type: convention
component: development_workflow
severity: high
related_components:
  - "src/percell4/gui/workflows/single_cell/config_dialog.py"
  - "src/percell4/gui/adaptive_clip_panel.py"
  - "src/percell4/domain/measure/auto_extraction.py"
  - "src/percell4/domain/measure/adaptive_clip.py"
root_cause: config_error
resolution_type: code_fix
applies_when:
  - "Exposing the same detection/thresholding feature through both a GUI creator panel and a batch/workflow method"
  - "Naming or relabeling detection methods in the single-cell workflow config dialog"
  - "Threading a GUI panel parameter (e.g. min particle size) into a workflow method for parity"
  - "A workflow method routes to a detector that has no GUI code path (orphaned detector)"
  - "A user reports batch results that do not reproduce the GUI Analysis panel"
symptoms:
  - "Batch workflow produced ~10x denser masks than the GUI Adaptive Local Clipping panel for identical settings and the same .h5 (133,740 vs ~13,882 positive px)"
  - "A similarly-named workflow method (Adaptive sigma clipping) silently routed to a different single-window detector than the panel's two-pass auto-extractor"
  - "GUI Analysis-panel masks could not be reproduced in batch"
tags: [adaptive-local-clipping, auto-extraction, gui-workflow-parity, naming-trap, thresholding, batch-workflow, reproducibility, min-particle-size]
---

# GUI creator panels and batch/workflow methods must map 1:1 (name and parameters)

## Context

The same conceptual feature often has two entry points: an interactive **GUI panel** (a Creator/Action window a researcher clicks through on one dataset) and a **batch/workflow configuration method** (a dialog that serializes a run config to apply the same operation across many files). In PerCell4 the single-cell thresholding step exists both ways — the "Adaptive Local Clipping" Analysis panel (`src/percell4/gui/adaptive_clip_panel.py`) and the batch workflow's per-round Method dropdown (`src/percell4/gui/workflows/single_cell/config_dialog.py`).

These two surfaces drifted apart. The GUI panel has exactly **one** detection mode: two-pass auto-extraction (`percell4.domain.measure.auto_extraction.auto_extract`) — its `_on_run` even comments "Auto extraction (two-pass) is the only detection mode." The batch dialog, meanwhile, offered a method labeled **"Adaptive sigma clipping"** that routed to a *different* detector entirely — the single-window `detect_adaptive_by_particle_size` — which had **zero** GUI callers (`grep` confirms no GUI counterpart). A user reproducing a GUI result in batch matched the closest-named method and silently ran a different algorithm.

The failure was quantitative and severe: for the same file and identical visible settings (Ø 2px, σ 1, Min size 3px², CNR 16), the batch path produced **133,740** positive pixels versus the GUI's **~13,882** — roughly 10x denser masks — impossible to reconcile until the naming trap was found.

The divergence was visible three days earlier and not acted on (session history): a 2026-07-10 session adding a `global_sigma` checkbox explicitly mapped both surfaces in writing and noted that the config dialog's "Adaptive sigma clipping" routes to `detect_adaptive_by_particle_size` while the Analysis panel routes to `auto_extract` — then treated them as two legitimately separate features and extended only the single-window engine, *further* diverging it from the panel. The framing was "which surface gets the new checkbox," not "why do these two engines disagree at all." That framing is exactly what this convention exists to correct.

## Guidance

When one conceptual feature is exposed through both a GUI panel and a batch/workflow method, the two surfaces must map **1:1** on two axes:

1. **Algorithm name.** The workflow method that reproduces a given GUI panel must be *named after that panel's algorithm*, and no two different detectors may share a similar name. A workflow method with no GUI counterpart must be *labeled as such*, not left to look like the GUI's option.
2. **Exposed parameters.** Every user-facing parameter the GUI passes to the underlying function must be threaded through the workflow path too — never left silently at a hidden default.

Concretely, the fix (commit `b886a081`):

- **Relabeled the method constants** so the name encodes the algorithm and its GUI relationship:
  ```python
  # config_dialog.py
  _METHOD_ADAPTIVE     = "Adaptive σ-clipping (single-window)"   # orphan: no GUI counterpart
  _METHOD_AUTO_EXTRACT = "Adaptive Local Clipping (two-pass)"    # the exact detector the GUI panel runs
  ```
  The Method tooltip now spells out that "Adaptive Local Clipping (two-pass)" is the SAME detector the GUI "Adaptive Local Clipping" panel runs, and that the single-window method is an older detector with no GUI equivalent. Labels are **display-only**; routing and serialization key off the sentinel dataclass (`AutoExtractSettings` vs `AdaptiveClipSettings`), so `run_config.json` is unchanged and the relabel is non-breaking.

- **Threaded the round's Min particle size into the detector's own size filter**, matching what the GUI panel does with its Min-particle-size widget:
  ```python
  # phases.py — resolve once, pass through; None keeps auto_extract's own default
  extra = {} if min_spot_px is None else {"min_spot_px": int(min_spot_px)}
  mask, report = auto_extract(..., **extra)
  # caller:
  err = _apply_auto_extract_cells(..., min_spot_px=(min_size_px if min_size_px > 0 else None))
  ```
  The generic post-mask size filter is skipped for the auto-extract path (the detector applies its own), so a batch auto-extract round is bit-identical to a GUI panel run. Backward-compatible: an unset Min size (0) passes `None` and leaves the detector default.

## Why This Matters

Batch reproducibility is the whole point of a workflow surface: a researcher validates settings interactively on one dataset, then expects to apply *the same operation* across a cohort. If the workflow secretly runs a different algorithm under a similar name, that guarantee is broken in the most insidious way — no error, no warning, just a result that can't be reconciled with the GUI. The user did everything right (matched every visible setting) and still got a 10x-wrong mask, because **matching visible settings is not enough when two surfaces expose different algorithms under similar names**. The parameter gap compounds it: even the *correct* detector gives different output if a GUI-exposed knob is left at a hidden default in the batch path.

It is also a slow trap: the 2026-07-10 session (session history) had the divergence in front of it and, lacking this convention, extended one engine without asking "should these two agree?" — widening the gap. A standing rule turns that into a checklist item ("does this method have a GUI twin? do their names and params match?") rather than a judgment call each time.

## When to Apply

Apply whenever you add or edit a feature that has (or is about to have) two entry points:

- Any batch/workflow/config dialog method that corresponds to a GUI Creator or Action panel.
- Adding a new detection/processing method to a workflow dropdown — check whether the GUI exposes the same or a similarly-named one.
- Removing or deprecating a GUI mode while a workflow still offers it (or vice versa) — that creates an orphan.
- Adding a parameter widget to a GUI panel — verify the workflow path threads the same parameter through.

Watch for the smell: a workflow method whose name resembles a GUI feature but routes to a different function, or a `grep` for a detector that finds workflow callers but no GUI callers (an orphan detector).

## Examples

**Before (the trap):**

| Surface | Label | Routes to |
|---|---|---|
| GUI panel | "Adaptive Local Clipping" | `auto_extract` (two-pass) |
| Batch dialog | "Adaptive sigma clipping" | `detect_adaptive_by_particle_size` (single-window, no GUI caller) |

Parameter gap: `_apply_auto_extract_cells` called `auto_extract(...)` without `min_spot_px`, using the hardcoded default (2), while the GUI passed its Min-particle-size widget.

**After (1:1 mapping):**

| Surface | Label | Routes to |
|---|---|---|
| GUI panel | "Adaptive Local Clipping" | `auto_extract` (two-pass) |
| Batch dialog | "Adaptive Local Clipping (two-pass)" — *pick this to reproduce a GUI run* | `auto_extract` (two-pass) |
| Batch dialog | "Adaptive σ-clipping (single-window)" — *no GUI counterpart* | `detect_adaptive_by_particle_size` |

Plus `min_spot_px` threaded from the round's Min size all the way to `auto_extract`.

**Prevention — naming-guard test** (`tests/test_gui_workflows/test_config_dialog.py`):

```python
def test_gui_matching_method_builds_auto_extract_round(dialog, h5_ds1):
    """The method a user maps from the GUI 'Adaptive Local Clipping' panel builds an
    AutoExtractSettings round — the same detector the GUI runs — not the single-window
    AdaptiveClipSettings. Guards the naming trap (batch != GUI thresholding)."""
    assert "Adaptive Local Clipping" in _METHOD_AUTO_EXTRACT
    assert "single-window" in _METHOD_ADAPTIVE  # the orphan is clearly marked

    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._rounds_table.cellWidget(0, _ROUND_COL_METHOD).setCurrentText(_METHOD_AUTO_EXTRACT)
    ...
    rounds = dialog._rounds_from_table(dialog._current_intersection())
    assert rounds[0].auto_extract is not None   # the GUI's two-pass detector
    assert rounds[0].adaptive_clip is None       # NOT the single-window detector
```

A companion parity spy test (`test_apply_auto_extract_threads_round_min_size_to_min_spot` in `tests/test_workflows/test_phases.py`) asserts the round's Min size reaches `auto_extract` as `min_spot_px`, plus a backward-compat test that an unset Min size leaves the detector default.

## Related

- [`architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`](../architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md) — Prior sibling learning; same GUI↔batch drift family (the shared-*default* trap). This doc adds the shared-*name* / orphan-routing + parameter-parity (`min_spot_px`) case. **Note:** that doc identifies `detect_adaptive_by_particle_size` as "the GUI's Adaptive Local Clipping algorithm" — commit `b886a081` shows the GUI panel actually runs `auto_extract` (two-pass) and the single-window detector has no GUI caller; that doc should be refreshed.
- [`architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md`](../architecture-patterns/extending-per-cell-detection-to-time-lapse-2026-06-25.md) — Canonical reference for the `auto_extract` two-pass path (`_apply_auto_extract_cells`, `NoParticlesFound`) the batch method now routes to.
- [`conventions/adaptive-clip-window-and-k-rules-2026-06-23.md`](./adaptive-clip-window-and-k-rules-2026-06-23.md) — The knob semantics (window / k / presmooth) both ALC detectors share; explains why the single-window and two-pass detectors are structurally different (not parameter variants of one engine).
- Root `CLAUDE.md` → "GUI state ownership" (Selector / Creator / Action): the GUI panel is a Creator, the workflow dialog is its batch mirror. The broader principle mirrors agent-native parity ("any action a user can take, an agent can also take") applied to GUI-vs-batch — any operation a user can run in the GUI must be reproducible identically in batch, under a name that says so.
