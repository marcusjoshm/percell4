---
title: "fix: Consistent area units (px²/µm²) across GUI labels + bin-scaling correctness"
type: fix
status: completed
date: 2026-07-23
---

# fix: Consistent area units (px²/µm²) across GUI labels + bin-scaling correctness

## Overview

Area filters and readouts in PerCell4 are computed correctly — every pixel↔micron
area conversion squares the pixel size, and every filter compares an area against a
pixel-count area (`regionprops.area`), never a linear dimension. The defect is
**labeling**: several UI widgets annotate a pixel-count area as `px` (or say "size (px)")
instead of `px²`, which makes the meaning ambiguous — the user cannot tell whether "9"
means *9 total pixels* or *9×9 pixels*. This plan normalizes every pixel-count-area label
to `px²` (matching the already-correct `px²`/`µm²` widgets like `round_card.py`) with
**zero change to any math or stored value**.

While auditing area handling, one genuine correctness bug surfaced: a stale bin-scaling
guard in `measure_cells.py` references a column name (`area_pixels`) that no longer
exists, so the whole-cell `area` column is not scaled by `bin²` when measuring at
`view_bin > 1`. This plan fixes that too, with a regression test.

---

## Problem Frame

The user set a "Min particle area" filter to `9` and could not tell what it would do,
because the unit dropdown reads `px` / `µm²` (asymmetric — one has the square, one does
not). Investigation confirmed:

- **`9` with `px` selected** → keeps particles whose area ≥ **9 total pixels** (`regionprops.area < min_area` at `src/percell4/domain/measure/particle.py:305`). `9` means 9 pixels, **not** 81.
- **`9` with `µm²` selected** → `round(9 / pixel_size_um²)` px (pixel size correctly squared), then the same pixel-count comparison. At 0.12 µm/px, `9 µm² ≈ 625 px`.

So the number already behaves as a true area in both units. The `px` label is simply
missing its `²`. The same mislabel (or "size (px)" phrasing for a pixel-count area)
recurs in several other panels. The audit found **no computational unit errors anywhere**
— all area conversions square the pixel size, all filters compare area-vs-area, and none
silently default `pixel_size_um` to 1 for a µm² threshold.

Separately, `src/percell4/application/use_cases/measure_cells.py:198-202` scales
pixel-count-area columns by `bin²` at `view_bin > 1`, but its guard
(`col == "area_pixels" or col.endswith("_area")`) never matches the whole-cell area
column, which is named exactly `area` (`src/percell4/domain/measure/measurer.py:25,112`).
ROI columns (`<roi>_area`) are scaled; whole-cell `area` is not.

---

## Requirements Trace

- R1. Every UI label, tooltip, and status/log string that annotates a **pixel-count area**
  must use `px²` (not `px`), so the value's meaning is unambiguous.
- R2. µm²-annotated area widgets stay `µm²` (already correct) — no change.
- R3. Label/wording changes must not alter any stored value, enum/`userData` code
  (`"px"` / `"um2"`), signal payload, conversion math, or filter behavior.
- R4. Cellpose "Min cell size (px)" / "Min cell area (px)" family — which are also
  pixel-count areas — normalize the unit token to `px²` (per user decision to normalize
  *all* area labels; the word "size" may stay, only the unit symbol changes).
- R5. Whole-cell `area` (and every pixel-count-area column produced in the measure path)
  must be scaled by `bin²` at `view_bin > 1`, consistent with ROI area columns.

---

## Scope Boundaries

- **No math changes.** All pixel↔µm area conversions, the `regionprops.area` comparisons,
  and the µm² sibling-column derivation are already correct and stay byte-for-byte
  identical. This plan touches only display text and one stale column-name guard.
- **`userData` / enum codes are frozen.** The combo `userData` values `"px"` / `"um2"`
  and the `ParticleSettings.min_area_unit` / `min_particle_size_unit` enum members
  (`{"px","um2"}`) do **not** change — only the human-visible display string `"px"` →
  `"px²"` changes. Downstream conversion keys off `currentData()`, not display text.
- **Length units stay as-is.** Cellpose diameter, adaptive-clip window, and
  "Smallest Particle Diameter" are **lengths** and correctly use `px` / `µm` (not `px²`).
  Do not touch them.
- **CSV/HDF5 column names are out of scope.** The `area` vs `area_px` naming divergence
  between pipelines (documented in `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md`)
  is a separate naming concern; renaming stored columns risks breaking downstream readers
  and is not required for unit-label clarity.

### Deferred to Follow-Up Work

- CLI help strings and code comments that describe a count as "in pixels" / "this many px"
  (e.g. `src/percell4/interfaces/cli/batch_process.py:186`,
  `src/percell4/application/analysis/modules/whole_field_intensity.py:122`): these use
  count phrasing rather than a literal `(px)` unit token, so they read correctly as
  "a number of pixels". Left untouched to keep this change to unambiguous unit-symbol
  mislabels. Revisit only if the user wants prose normalized too.

---

## Context & Research

### Relevant Code and Patterns

- **Correct pattern to mirror:** `src/percell4/gui/workflows/single_cell/round_card.py:282-288`
  — sibling widget for the same quantity: `addItem("px²", userData="px")`,
  `addItem("µm²", userData="um2")`, tooltip "px² applies the value directly; µm² resolves
  to pixels…". The screenshot panel (`config_dialog.py`) should match this exactly.
- **Also already-correct:** `src/percell4/gui/_adaptive_clip_settings.py:33-34`
  (`_UNIT_LABELS = ("px²", "µm²")`, `_UNIT_CODES = {"px²": "px", "µm²": "um2"}`) and
  `src/percell4/gui/adaptive_clip_panel.py:508` (`"min particle {min_spot_px} px²"`).
- **Conversion (correct, do not touch):** `src/percell4/workflows/phases.py:1646-1674`
  (`_resolve_area_px`, `/pixel_size²`), `phases.py:1692-1706` (`_add_area_um2_columns`,
  `×pixel_size²`), `src/percell4/domain/measure/adaptive_clip.py:752-770`
  (`resolve_min_area_px`).
- **Filter (correct, do not touch):** `src/percell4/domain/measure/particle.py:305`
  (`if prop.area < min_area`).
- **Bin-scaling site:** `src/percell4/application/use_cases/measure_cells.py:198-202`;
  whole-cell area column produced at `src/percell4/domain/measure/measurer.py:25,112`
  (name = `area`); ROI area at `measurer.py:254` (`<roi>_area`).

### Institutional Learnings

- `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md` — canonical rule:
  pixel-count area columns are named `<base>_area_px`; `run_analysis` adds a `_area_um2`
  sibling generically via `×pixel_size_um²`; never default `pixel_size_um` to 1 for µm².
  Confirms the µm² path is intentional and must not be "fixed."
- `docs/solutions/architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`
  — physical-µm parameters use a four-guard chain and **fail cleanly** when `pixel_size_um`
  is missing (never default to 1). The existing µm² area path already honors this; this
  plan does not disturb it.
- `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md`
  — the min-particle-size widget is already `px²` in the round card; a parity spy test
  guards GUI↔batch threading. Our label change must not affect that threading.

### External References

- None — this is an internal label/consistency change with no external contract surface.

---

## Key Technical Decisions

- **Change display strings only; freeze `userData`/enum codes.** `"px"` → `"px²"` in
  visible text only. `userData="px"`, `ParticleSettings.min_area_unit="px"`, and all
  conversion logic (which reads `currentData()`) are untouched. Verified:
  `_on_min_area_unit_changed` (`config_dialog.py:1310+`) reads `currentData()`, not label
  text, so the decimals/step swap is unaffected.
- **Normalize the Cellpose "size (px)" family too** (per user decision). Keep the word
  "size"; change the unit token `(px)` → `(px²)`. These are pixel-count areas
  (Cellpose `min_size`), so `px²` is technically correct even though "size" reads as a count.
- **Bin-scaling fix: correct the predicate, not the k² math.** Replace the dead
  `col == "area_pixels"` reference with `col == "area"` so the whole-cell area column is
  scaled; keep `endswith("_area")` for ROI columns. The `bin²` scale factor itself is
  already correct. Explicitly ensure no `_um2`/`_area_um2` column is matched (µm² columns,
  if ever present in this path, must not be scaled here).

---

## Open Questions

### Resolved During Planning

- *Does "9 px" mean 9 pixels or 81 pixels?* → 9 pixels. Comparison is against
  `regionprops.area` (a pixel count). Confirmed by code trace.
- *Are any conversions using `pixel_size¹` where `pixel_size²` is needed for area?* → No.
  Every area conversion squares the pixel size. Confirmed across the codebase.
- *Will renaming the combo display break the unit-swap or downstream config?* → No.
  All logic keys off `userData`/`currentData()`. Confirmed at `config_dialog.py:1291-1319`.
- *Is the whole-cell area bin-scaling bug real?* → Yes. Column is named `area`; the guard
  only matches `area_pixels` (nonexistent) or `*_area`. Confirmed at `measurer.py:25,112`.

### Deferred to Implementation

- Exact final wording of the two `config_dialog.py` tooltips (particle-area tooltip at
  1268-1274 and unit tooltip at 1280-1286) — reword the `px` mention to `px²` while
  keeping the sentence natural. Wording is editorial, settled at edit time.
- Whether the `measure_cells.py` predicate is best expressed inline or extracted to a
  shared `_is_pixel_area_column` helper aligned with `csv_columns._is_area_column` — decide
  when touching the code; a local inline fix is acceptable if extraction adds no clarity.

---

## Implementation Units

- U1. **Fix the particle-analysis area unit dropdown + tooltips (the screenshot panel)**

**Goal:** Make the `WorkflowConfigDialog` "Min particle area" selector unambiguous:
`px` → `px²`, matching its `µm²` sibling and the `round_card.py` pattern.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py` (line 1277 combo item;
  tooltips at 1268-1274 and 1280-1286)
- Test: `tests/` — locate the existing `WorkflowConfigDialog` / `config_dialog` test module
  and add an assertion there (search for existing coverage of `_particle_min_area_unit`
  before creating a new file).

**Approach:**
- Change `addItem("px", userData="px")` → `addItem("px²", userData="px")`. Leave `userData`
  and the `µm²` item untouched.
- Update both tooltips so the `px` mention reads `px²` ("px² applies a uniform pixel
  threshold to every dataset").
- Do not touch `_on_min_area_unit_changed` — it reads `currentData()`.

**Patterns to follow:**
- `src/percell4/gui/workflows/single_cell/round_card.py:282-288` (correct `px²`/`µm²` combo + tooltip).

**Test scenarios:**
- Happy path: after building the dialog, the particle-area unit combo item 0 display text
  is `"px²"` and its `currentData()` is still `"px"`; item 1 is `"µm²"` / `"um2"`.
- Integration (behavior unchanged): selecting the `px²` item and reading back
  `ParticleSettings.min_area_unit` yields `"px"` (the enum code), and a µm²→px resolution
  for a known `pixel_size_um` is unchanged from before (guards against accidental code drift).

**Verification:**
- The dropdown shows `px²` / `µm²`; `ParticleSettings.min_area_unit` still serializes as
  `"px"`/`"um2"`; no conversion or run behavior changes.

---

- U2. **Normalize remaining pixel-count-area labels + status strings across the GUI**

**Goal:** Every other widget/status line that annotates a pixel-count area reads `px²`.

**Requirements:** R1, R3, R4

**Dependencies:** None (independent of U1; may land together)

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/analysis_panel.py`
  (line 127 label `"Min particle area (px):"` → `"…(px²):"`; line 684 status
  `"min area: {min_area} px"` → `"…px²"`)
- Modify: `src/percell4/gui/segmentation_panel.py` (line 298 `"Min cell area (px):"` → `px²`)
- Modify: `src/percell4/gui/workflows/single_cell/seg_qc.py` (line 378 `"Min area (px):"` → `px²`;
  line 515 `"Min cell size (px):"` → `"Min cell size (px²):"` per R4)
- Modify: `src/percell4/gui/_cellpose_settings_form.py` (line 106 `"Min cell size (px):"` → `px²` per R4)
- Modify: `src/percell4/gui/adaptive_clip_panel.py` (line 550 status
  `"area {report.area_px} px"` → `"…px²"`)
- Test: add/extend a lightweight assertion for the two **status-string** formats
  (`analysis_panel` line 684, `adaptive_clip_panel` line 550) in their existing test
  modules; pure `QLabel` text edits need no test (see below).

**Approach:**
- Pure mechanical text edits. Search each file for the exact string before editing to
  confirm no drift from the audited line numbers.
- For the Cellpose "size" labels, keep "size", change only `(px)` → `(px²)` (R4).
- Do not touch length labels (Diameter, window, Smallest Particle Diameter).

**Patterns to follow:**
- `src/percell4/gui/adaptive_clip_panel.py:508` (`"min particle {min_spot_px} px²"`) — the
  in-file precedent for `px²` in a status string.

**Test scenarios:**
- Happy path (status strings): formatting the analysis-panel and adaptive-clip status
  lines with a sample area value produces a string containing `"px²"` and not a bare
  `" px"` area token.
- `Test expectation: none — pure QLabel text` for the static label edits
  (`analysis_panel:127`, `segmentation_panel:298`, `seg_qc:378/515`, `_cellpose_settings_form:106`):
  no behavioral change; label text is not asserted by the suite and GUI label tests only
  run on CI. Covered by manual/visual confirmation in Verification.

**Verification:**
- Grep for area labels annotated as bare `(px)` / `" px"` in these files returns nothing
  (only length labels retain `px`). Status strings render `px²`.

---

- U3. **Fix whole-cell area bin-scaling at `view_bin > 1`**

**Goal:** The whole-cell `area` column is scaled by `bin²` when measuring at `view_bin > 1`,
matching ROI area columns (correctness bug, not a label change).

**Requirements:** R5

**Dependencies:** None (independent; separate concern from U1/U2)

**Files:**
- Modify: `src/percell4/application/use_cases/measure_cells.py` (lines 198-202)
- Test: the existing test module covering `MeasureCells` / `_measure_one` bin behavior
  (search for `view_bin` / `bin_at_measure` tests; extend it, else create
  `tests/application/use_cases/test_measure_cells_bin_scaling.py`)

**Approach:**
- Replace the stale guard `if col == "area_pixels" or col.endswith("_area"):` with a
  predicate that matches the actual whole-cell area column: `if col == "area" or col.endswith("_area"):`.
- Confirm no pixel-count-area column produced by `measure_multichannel` /
  `measure_multichannel_multi_roi` is missed, and that no `_um2` column is present in this
  path (it is not — µm² siblings are added downstream). If extracting a
  `_is_pixel_area_column` helper improves clarity, align it with
  `csv_columns._is_area_column` semantics; otherwise keep the inline fix.
- The `scale = view_bin * view_bin` factor is already correct — do not change it.

**Execution note:** Start with a failing test that measures a small synthetic frame at
`view_bin=2` and asserts the whole-cell `area` column is scaled by 4 before applying the fix.

**Patterns to follow:**
- Existing bin-scaling block structure in `measure_cells.py:196-204`.
- `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md` for area-column naming.

**Test scenarios:**
- Happy path: measure a synthetic frame (known whole-cell pixel area A) at `view_bin=2`;
  the returned `area` value equals `A * 4`.
- Edge case: at `view_bin=1`, `area` is unchanged (scale block does not run).
- Integration (multi-ROI): with a multi-ROI mask at `view_bin=2`, both whole-cell `area`
  and each `<roi>_area` are scaled by 4 in the same DataFrame.
- Guard: a non-area numeric column (e.g. a mean-intensity metric) is **not** scaled at
  `view_bin=2`; and if a `_um2`/`_area_um2` column were present it would not be scaled by
  this block.

**Verification:**
- At `view_bin > 1`, whole-cell `area` and ROI areas are reported in k=1-equivalent pixels
  (× bin²); intensity/non-area columns are untouched; the new regression test passes.

---

## System-Wide Impact

- **Interaction graph:** U1/U2 are display-only — no signals, session fields, or stored
  values change; subscribers and `state_changed` handling are unaffected. U3 changes the
  numeric content of the `area` column only at `view_bin > 1`.
- **API surface parity:** The `px²`/`µm²` labeling now matches across `round_card.py`,
  `_adaptive_clip_settings.py`, `config_dialog.py`, and the segmentation/analysis panels —
  the change *removes* an inconsistency rather than adding surface.
- **State lifecycle risks:** U3 affects downstream consumers of the whole-cell `area`
  column at `view_bin > 1` (they now receive correctly-scaled values). This is a
  correctness improvement; verify no consumer double-scales `area` (grep for other
  `view_bin`-conditioned scaling of `area`).
- **Unchanged invariants:** All area math, µm² conversion, `regionprops.area` comparisons,
  `userData`/enum codes (`"px"`/`"um2"`), and CSV/HDF5 column names are explicitly
  unchanged. Cellpose diameter and adaptive-clip window length labels (`px`/`µm`) are
  unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| A test asserts the literal `"px"` display string and breaks on `"px²"`. | Search tests for `"(px)"` / `'px'` label assertions before editing; update any that pin display text. |
| Someone reads the combo display text instead of `userData` somewhere. | Confirmed `_on_min_area_unit_changed` uses `currentData()`; grep for `.currentText()` on the area unit combos to be sure none rely on display text. |
| U3 double-scales `area` if another site already scales it. | Grep for other `view_bin`/`bin` scaling of `area`; the audit found only this one site. Regression test pins the expected ×bin² result. |
| Cellpose "size (px²)" reads awkwardly to users. | Per explicit user decision (normalize all area labels). "size" wording retained; only the unit token changes. |

---

## Sources & References

- Investigation: unit trace of the "Min particle area" filter (px/µm² path) and a
  codebase-wide area/size unit audit (this session).
- Related code: `src/percell4/gui/workflows/single_cell/config_dialog.py:1276-1297`,
  `round_card.py:282-288`, `src/percell4/domain/measure/particle.py:305`,
  `src/percell4/workflows/phases.py:1646-1706`,
  `src/percell4/application/use_cases/measure_cells.py:198-202`,
  `src/percell4/domain/measure/measurer.py:25,112,254`.
- Learnings: `docs/solutions/conventions/um2-area-sibling-columns-2026-06-29.md`,
  `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md`,
  `docs/solutions/architecture-patterns/adding-thresholding-method-to-single-cell-workflow-2026-06-15.md`.
