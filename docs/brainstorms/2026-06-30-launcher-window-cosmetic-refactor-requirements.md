---
date: 2026-06-30
topic: launcher-window-cosmetic-refactor
---

# Launcher Window Cosmetic / UX Refactor

## Problem Frame

The PerCell4 launcher window (`src/percell4/interfaces/gui/main_window.py`, `LauncherWindow`) works correctly, but several tabs are organized around the code's technical structure rather than the user's mental model. Button names assume insider knowledge, retired analysis methods still clutter panels, a redundant Save button persists, and two near-identical tabs (Scripts, Workflows) are split. This refactor re-labels and re-groups launcher controls so the window reads the way the user thinks, and removes dead/unused controls. All features stay functional — this is presentation and pruning, not new capability.

Scope is the **launcher window only**. The napari viewer window, peer windows (data plot, cell table, phasor plot), and the underlying detection/measurement/workflow engines are out of scope except where a control physically moves between launcher tabs.

Reconciliation note: the launcher actually has **8 tabs** (I/O, Viewer, Segment, Analysis, FLIM, Scripts, Workflows, Data), not the 5 first described. The "Open Viewer" button lives in its own **Viewer** tab. **FLIM** and **Data** tabs are untouched by this work.

---

## Requirements

**I/O tab**
- R1. Replace today's 5 import buttons + 3 export buttons with **5 buttons** matching the user's verbs: `New Dataset…`, `Open Dataset…`, `Add Data ▾`, `Close Dataset`, `Export ▾`. (Labels are placeholders, final-able during implementation.) Mapping: `New Dataset` = current "Compress TIFF Dataset…" (CompressDialog); `Open Dataset` = current "Load Dataset…"; `Close Dataset` = current "Close Dataset".
- R2. `Add Data ▾` is a **menu button**: clicking it drops a small menu whose entries open the existing add flows — `Layer…` (AddLayerDialog) and `Batch TCSPC…` (BatchTCSPCDialog). No add capability is removed.
- R3. `Export ▾` is a **menu button** exposing all existing export flows — `Measurements (CSV)…`, `Images (TIFF)…`, `Phasor (.npz)…` — each opening its current flow. No export capability is removed.

**Viewer tab**
- R4. Add a `Hide Viewer` button alongside `Open Viewer`. Hide calls `window.hide()`; the viewer singleton (`self._windows["viewer"]`) and its layers persist. `Open Viewer` continues to re-show/raise the same instance. The viewer is never destroyed by these buttons.
- R5. Move the **Cell Filter** controls (`Clear Selection`, `Filter to Selection`, `Clear Filter`, count label) from the Analysis tab into the Viewer tab, turning that bare tab into a small "viewer controls" panel. Behavior is unchanged — it remains a Selector writing `session.selection` / `session.filter_ids`; only its location moves.

**Segment tab**
- R6. Merge the `Manual Editing` and `Label Cleanup` sections into a **single label-editing module** containing all their controls: Create Empty Labels Layer, Delete Selected Label, Add New Label (next ID), Clean Up Labels (relabel sequential), Edge margin (px), Min cell area (px), Preview Removal, Apply Removal.
- R7. Remove the `Save Labels to HDF5` button and its Save section. Label mutations already auto-persist (button edits write synchronously; brush/erase strokes are debounced via `_autosave_timer` — see `segmentation_panel.py:293-300`), so the button is redundant. Add a subtle "edits auto-saved" reassurance line in the merged module so its absence does not feel risky.

**Analysis tab**
- R8. Remove the `Iterative Otsu Thresholding` module entirely (never used).
- R9. Reorder the Analysis modules top-to-bottom to lead with the most-used: **Adaptive Local Clipping → Particle Analysis → Measurements → Grouped Thresholding → Whole-Field Thresholding.**
- R10. Strip `Adaptive Local Clipping` to **auto-extract (two-pass) only**. Remove the "Auto adaptive window size" checkbox and the "Auto window method" dropdown — no method choice remains. Retained controls: `Auto-detect smallest (LoG)` checkbox (the auto-detection toggle), `Smallest particle Ø` (+ px/µm unit, the manual override shown when auto-detect is off), `Gaussian σ`, `Min particle size` (+ px²/µm² unit), and the read-only `Detected Ø (µm)` back-fill readout.
- R11. Remove the now-unused Adaptive Local Clipping fields: `Size percentile (%)`, `Size cutoff Ø (px)`, `Auto start window`, `Iterations`, `Noise (σ) estimate` combo, `k (σ multiplier)`, and the raw `Window` field. Rationale: the two-pass engine (`run_adaptive_auto_extract`) pins k=1 and **derives the window from the smallest-particle Ø**, so the Smallest-particle Ø override is the physically meaningful window control; k and Window have no effect in two-pass mode.
- R12. The CNR tools (`Classify Mask by CNR`, `Segment by CNR (interactive)`) attached to the Adaptive Local Clipping panel remain unchanged.

**Analyses & Workflows tab**
- R13. Merge the `Scripts` and `Workflows` tabs into one tab titled `Analyses & Workflows`, with two light section headers — `Analyses` (the 3 registry scripts: Per Particle Donut, Per Particle Multichannel, Whole Field Intensity) and `Workflows` (the 5 batch workflows). The underlying registration mechanisms (dynamic `@register_analysis` registry vs hard-coded workflow buttons) are unchanged; only the presentation merges.

**Cross-cutting**
- R14. All changes are confined to the launcher window tree. The FLIM and Data tabs, the napari viewer window internals, and all detection/measurement/workflow engine behavior are untouched (beyond deleting dead UI code paths).

---

## Visual Aid — launcher before / after

```
BEFORE (8 tabs)                          AFTER (7 tabs)
─────────────────────────────────        ─────────────────────────────────
I/O                                       I/O
  Compress TIFF Dataset…                    New Dataset…
  Load Dataset…                             Open Dataset…
  Add Layer to Dataset…                     Add Data        ▾ (Layer / Batch TCSPC)
  Batch TCSPC Append…                       Close Dataset
  Close Dataset                             Export          ▾ (CSV / Images / Phasor)
  Export Measurements to CSV…
  Export Images…                          Viewer
  Export Phasor (.npz)…                     Open Viewer
                                            Hide Viewer            ← new
Viewer                                      Cell Filter           ← moved from Analysis
  Open Viewer
                                          Segment
Segment                                     Cellpose · Tracking
  Cellpose · Tracking                       Edit Labels           ← Manual Editing + Label Cleanup merged
  Manual Editing                            (Save button removed; edits auto-saved)
  Label Cleanup
  Save Labels to HDF5                      Analysis
                                            Adaptive Local Clipping   (stripped to two-pass)
Analysis                                    Particle Analysis
  Cell Filter                               Measurements
  Whole Field Thresholding                  Grouped Thresholding
  Grouped Thresholding                      Whole-Field Thresholding
  Adaptive Local Clipping
  Iterative Otsu Thresholding             FLIM            (untouched)
  Measurements                            Data            (untouched)
  Particle Analysis
                                          Analyses & Workflows      ← Scripts + Workflows merged
FLIM · Scripts · Workflows · Data           Analyses (3) · Workflows (5)
```

---

## Acceptance Examples

- AE1. **Covers R4.** Given the viewer is open with layers loaded, when the user clicks `Hide Viewer` then `Open Viewer`, the same viewer instance reappears with its layers intact (no reload, no new window).
- AE2. **Covers R7.** Given the user paints/erases labels and never clicks any Save control, when the dataset is closed and reopened, the edits are present (auto-save persisted them).
- AE3. **Covers R2, R3.** Given the user wants to append TCSPC data, when they click `Add Data ▾`, a menu appears with `Layer…` and `Batch TCSPC…`, and choosing `Batch TCSPC…` opens today's BatchTCSPCDialog unchanged.
- AE4. **Covers R10, R11.** Given the Adaptive Local Clipping module after the refactor, the only visible inputs are the Auto-detect-smallest checkbox, Smallest particle Ø (when auto-detect is off), Gaussian σ, Min particle size, and the Detected Ø readout — no method dropdown, k, Window, percentile, cutoff, auto-start-window, iterations, or noise combo.

---

## Success Criteria

- The touched tabs read in the user's terms: I/O verbs match create / open / add / close / export; Analysis leads with the user's most-used tool; no retired methods or redundant controls remain.
- A user with no knowledge of the internal naming can locate each action by intent.
- No capability regresses: every import, export, label edit, and detection run remains reachable, and label edits still auto-persist.
- Downstream handoff is clean: `ce-plan` can implement without inventing layout, labels, or which fields/modules to keep — every keep/cut and every move is enumerated above.

---

## Scope Boundaries

- No changes to detection, measurement, or workflow engine behavior — presentation and dead-code removal only.
- FLIM and Data tabs untouched.
- Cell Filter stays a launcher control (Viewer tab); it is **not** docked inside the napari viewer window (that option was considered and rejected as out of "launcher only" scope).
- Scripts and Workflows merge **visually only** — the dynamic registry vs hard-coded mechanisms are not unified.
- No broad theme/styling overhaul; reuse existing `theme.py` constants and section-label patterns.

---

## Key Decisions

- Cell Filter → **Viewer tab** (not a persistent header, not the Data tab, not docked in the viewer): selection originates in the viewer, so `Filter to Selection` sits next to where you select; stays launcher-only.
- Analysis order → **Detection-first**: Adaptive Local Clipping on top and adjacent to Particle Analysis (clipping produces the mask Particle Analysis consumes); thresholding demoted to the bottom.
- Save Labels button → **removed** (evidence-based): auto-save already persists every mutation; the button just calls the same `store.write_labels`.
- I/O Add Data / Export → **menu buttons** (not unified dialogs, not split buttons): lightest touch, matches "one button where all the features live."
- Adaptive Local Clipping → expose **Smallest-particle Ø as the manual override, cut k and Window**: the two-pass engine derives the window from Ø and fixes k=1, so Ø is the meaningful manual knob and k/Window are noise.
- Scripts + Workflows → **one tab, two light sections** (not a flat list, not scope-relabeled): one place to look, keeps a quick-vs-batch hint, lowest implementation risk.

---

## Dependencies / Assumptions

- Removing Iterative Otsu, the retired adaptive-clip methods, and the Save button means **deleting UI code**. Assumes those panels/methods are not invoked from workflows, the CLI, or tests. To verify in planning (see Outstanding Questions).
- Assumes the Cellpose-run and Track-Cells Creator paths persist their output to HDF5 at creation time (independent of the Save button), so removing Save loses nothing beyond what auto-save already covers.

---

## Outstanding Questions

### Resolve Before Planning

- None — all product decisions are made.

### Deferred to Planning

- [Affects R8, R11][Technical] Are the Iterative Otsu panel (`iterative_otsu_panel.py`) and the retired adaptive-clip window-method code paths (`granule-size`, `otsu-mean`, `otsu-smallest`, `multiscale`, plus `run_adaptive_detection*` finder helpers and the multi-scale routine) referenced anywhere else — workflows, CLI, tests, or the `2026-06-22-multiscale-adaptive-clip-routine` work? Decide delete vs. retain-but-hide per reference.
- [Affects R2][Technical] Does `AddLayerDialog`'s TCSPC tab overlap with the standalone `Batch TCSPC Append`? If functionally redundant, the `Add Data ▾` menu may collapse to fewer entries.
- [Affects R7][Technical] Confirm the Cellpose-run and Track-Cells Creator paths persist on creation (not via Save), closing the loop that removing Save is lossless.
- [Affects R1][User decision — minor] Final button wording (e.g., "New" vs "Create" Dataset; "Add Data" vs "Add to Dataset"). Treated as adjustable during implementation.

---

## Next Steps

-> `/ce-plan` for structured implementation planning.
