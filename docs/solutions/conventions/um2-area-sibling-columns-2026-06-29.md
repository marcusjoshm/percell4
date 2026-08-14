---
title: "Pixel-area columns get a µm² sibling from dataset pixel_size_um in run_analysis"
date: 2026-06-29
last_updated: 2026-06-29
category: conventions
module: percell4.application.use_cases.run_analysis
problem_type: convention
component: tooling
severity: low
canonical_source: src/percell4/application/use_cases/run_analysis.py
applies_to:
  - "src/percell4/application/use_cases/run_analysis*.py"
  - "src/percell4/application/analysis/modules/**/*.py"
status: canonical_clean
tags:
  - analysis
  - area
  - micron
  - pixel-size
  - run-analysis
  - table-output
  - units
  - convention
applies_when:
  - an analysis emits a "*_area_px" column
  - reasoning about physical-unit (µm²) area outputs
  - combining calibrated and uncalibrated datasets in one batch
---

# Pixel-area columns get a µm² sibling in `run_analysis`

## Context

Registered analyses report area in **pixels** (`<base>_area_px`) because the
pure cores are calibration-agnostic — they never see the microscope. But the
user wants physical area in µm². Rather than make every analysis re-implement
the conversion (and re-thread `pixel_size_um` through every pure core),
`run_analysis` adds the calibrated sibling **once, generically, after the
analysis runs**, as the final post-processing step before returning outputs.

## Guidance

After type validation, `run_analysis` walks every returned output and, for each
`TableOutput`, calls `_add_area_um2_columns(df, pixel_size_um)`:

```python
_meta = DatasetStore(h5_path).metadata
pixel_size_um = _meta.get("pixel_size_um")   # read ONCE per dataset
...
for name, value in outputs.items():
    if isinstance(cls.outputs[name], TableOutput):
        outputs[name] = _add_area_um2_columns(value, pixel_size_um)
```

`_add_area_um2_columns` inserts a `<base>_area_um2` column **immediately after**
each `<base>_area_px` column, valued `px_count * pixel_size_um**2`. So
`pbody_area_px` → `pbody_area_um2`, `cell_area_px` → `cell_area_um2`,
`particle_area_px` → `particle_area_um2`. It re-fetches each insert position
inside the loop because prior inserts shift the column index.

Three invariants make this safe to apply blindly:

- **Graceful no-op on missing/bad calibration.** `pixel_size_um` comes from
  `DatasetStore(h5).metadata` (set at TIFF import from the XResolution tags).
  Because it is *external* metadata, the value is coerced with
  `float(pixel_size_um)` inside a `try/except (TypeError, ValueError)`; a
  missing, non-numeric, or non-positive (`not (px > 0)`) value returns the
  frame **unchanged** — never raises. An uncalibrated single-dataset output is
  therefore byte-identical to the pre-feature output.
- **Idempotent.** A `_area_um2` sibling that already exists is skipped, so
  re-running over an already-augmented table is a no-op.
- **Generic, not per-analysis.** It lives in `run_analysis`, not in any module,
  so a *new* analysis emitting `*_area_px` gets µm² for free with zero code.

## Why This Matters

Centralizing the conversion means there is exactly one place where pixel area
becomes physical area — no analysis can ship a subtly different formula, and
adding a new area metric needs no calibration plumbing. The "treat bad external
metadata as absent" guard keeps the framework from turning a malformed
XResolution tag into a hard batch failure: the µm² column is an *additive
convenience*, so its absence is the correct outcome, not an error.

## When to Apply

- You are adding/renaming an area output: name it `<base>_area_px` and the µm²
  sibling appears automatically — do not hand-roll `_area_um2`.
- You are reasoning about a combined CSV from a batch that mixes calibrated and
  uncalibrated datasets: pandas unions columns by name, so `_area_um2` appears
  with **`NaN`** for the uncalibrated rows. That `NaN` is the correct "µm²
  unknown" semantic, not a defect — do not "fix" it by defaulting to 1 µm/px.
- You are touching multi-timepoint output: the conversion composes naturally —
  it runs on the final tables, including the aggregated table produced by
  `_aggregate_timepoints` (per-cell and per-particle tables alike).

## Examples

```python
# pbody_area_px = 250 px on a dataset with pixel_size_um = 0.120369
#   -> pbody_area_um2 = 250 * 0.120369**2 ≈ 3.622  (inserted right after px col)

# Same analysis, an import with no resolution tags (pixel_size_um is None):
#   -> output is byte-identical; only pbody_area_px is present.
```

## Related

- `[[adding-thresholding-method-to-single-cell-workflow-2026-06-15]]` — the
  canonical `pixel_size_um`-from-metadata threading convention. **Deliberate
  contrast:** that doc mandates failing the dataset cleanly when pixel size is
  missing/non-positive (never default to 1 µm/px) because size is a *required*
  physical parameter of the thresholding math. Here `_add_area_um2_columns` is a
  graceful **no-op** because the µm² column is *additive* — both behaviors are
  correct for their context; the divergence is intentional, not drift.
- `[[adaptive-clip-window-and-k-rules-2026-06-23]]` — the broader µm-physical-unit
  convention (rules written in µm, converted per image via the pixel size).
- `[[registered-analysis-multitimepoint-contract-2026-06-29]]` — the sibling
  `run_analysis` post-processing step; both transform `TableOutput`s on the way
  out (this one column-wise, that one by concatenating per-timepoint frames),
  and this conversion runs on the aggregated table.
- `[[registered-analysis-framework]]` — the parent pattern; rule 7 (outputs
  persist by dict key) is the step this post-processing runs after.
