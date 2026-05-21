---
title: Pixel-size visibility and TIFF metadata round-trip
type: feat
status: completed
date: 2026-05-21
---

# Pixel-size visibility and TIFF metadata round-trip

## Overview

Three related changes that close gaps in how PerCell4 surfaces and preserves
the dataset's physical pixel size:

1. **TIFF export preserves metadata** carried in from the original input
   TIFFs. Today the reader decodes `XResolution` / `YResolution` /
   `ResolutionUnit` into `pixel_size_um` at import, but the writer emits
   bare arrays — so an export → re-import round-trip silently loses the
   physical scale. Raw channel exports must round-trip; derived layers
   (segmentation labels, masks) must carry the same spatial metadata so
   they can be opened in ImageJ / napari with the correct calibration.
2. **Pixel resolution shown in Dataset Info** on the Data tab. Currently
   the panel reports `File`, `Shape`, `Native`, `Creation bin`, `View bin`,
   `Labels`, `Masks`, but never the µm/px scale that drives every
   `area_um2` column in measurements.
3. **Min particle area** in the single-cell thresholding workflow gains a
   `px` ↔ `µm²` unit selector next to the spinbox. Users currently must
   pre-convert manually, with no reminder of the dataset's pixel size.

---

## Problem Frame

The user just ran a workflow analysis and was momentarily confused by the
ratio between `area` (px) and `area_um2` columns — the underlying math is
correct, but the pixel size is invisible at every UI surface where it
matters (Dataset Info), every artifact that flows downstream (exported
TIFFs are calibration-bare), and every input that depends on it (Min
particle area only accepts pixels). All three gaps point at the same
underlying problem: `pixel_size_um` lives in HDF5 metadata but is
otherwise a private implementation detail. The plan promotes it to a
first-class, visible, round-tripping field.

---

## Requirements Trace

- R1. A TIFF written by `ExportImages` and re-imported via the existing
  TIFF reader produces the same `pixel_size_um` (within float tolerance)
  as the source dataset, for raw channels.
- R2. Derived layers (segmentation labels, masks) exported as TIFF carry
  the dataset's `pixel_size_um` in their `XResolution` / `YResolution` /
  `ResolutionUnit` tags so ImageJ and napari render them at the correct
  physical scale.
- R3. When exporting with `view_bin > 1`, the emitted resolution tags
  reflect the *output* pixel spacing (`stored_pixel_size_um × view_bin`),
  not the native spacing — so the file is self-describing.
- R4. The Dataset Info group on the Data tab displays the dataset's pixel
  size in both linear and areal forms (e.g.
  `Pixel size: 0.1204 µm/px (0.01449 µm²/px)`).
- R5. The Min particle area field in the single-cell thresholding workflow
  config dialog offers a unit selector — `px` (default, current behavior)
  or `µm²` — and the workflow honors the chosen unit per-dataset using
  each dataset's own `pixel_size_um`.
- R6. Datasets that do not carry a `pixel_size_um` (legacy h5 files, or
  TIFFs imported without resolution tags) must continue to work for px-mode
  workflows and px-mode UI; only µm²-mode actions and unit-dependent
  displays gracefully degrade.

---

## Scope Boundaries

- Anisotropic pixels (`XResolution ≠ YResolution`) — out of scope; the
  reader already collapses to a single scalar `pixel_size_um` and PerCell4
  has no anisotropy story elsewhere. The writer will emit
  `YResolution = XResolution`.
- OME-XML round-trip — out of scope. PerCell4 does not produce OME-XML
  metadata today and parsing it on import would be a separate effort.
- Per-image pixel size within a dataset — out of scope. Pixel size is
  dataset-wide; we will not introduce per-channel or per-image overrides.
- A "Pixel size" editor — out of scope. The Dataset Info display is
  read-only. Correcting a dataset's pixel size requires re-import.
- Backfilling existing HDF5 files with an extended tag bundle — out of
  scope. Legacy datasets without the bundle export with whatever they
  have (at minimum `pixel_size_um` if present, otherwise no resolution
  tags).
- Anisotropic minimum-area handling, particle-area unit selector on
  *other* measurement columns (e.g. integrated intensity area thresholds)
  — out of scope.

---

## Context & Research

### Relevant Code and Patterns

- **Reader**: `src/percell4/adapters/readers.py` — `_pixel_size_um_from_tags`
  (lines 27–63), `read_tiff` (66–90), `read_tiff_metadata` (93–108).
  Currently only `XResolution` + `ResolutionUnit` are decoded; nothing
  else is captured.
- **Importer**: `src/percell4/adapters/importer.py:517` writes the
  creation-bin-scaled `pixel_size_um` into `/metadata.pixel_size_um`.
  The *raw* (creation-bin = 1) value is reconstructible by dividing by
  `creation_bin`.
- **Export use case**: `src/percell4/application/use_cases/export_images.py`
  — three `tifffile.imwrite(str(out_path), data)` call sites with no
  metadata argument: channels (line 65), labels (74), masks (83).
- **Export dialog**: `src/percell4/gui/export_images_dialog.py` drives
  the use case (collects selection, calls `ExportImages.execute`).
- **Batch export**: `src/percell4/application/use_cases/batch_export_images.py`
  wraps `ExportImages` for the multi-dataset path.
- **Workflow config dialog**: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  — `WorkflowConfigDialog._build_particle_group`, `_particle_min_area`
  QSpinBox + label at lines 549–557.
- **Workflow config model**: `src/percell4/workflows/models.py` —
  `ParticleSettings` dataclass holding `min_area: int`. Serialized to
  JSON via `src/percell4/workflows/artifacts.py`.
- **Threshold consumer**: `src/percell4/workflows/phases.py` —
  `analyze_particles` (line 1057+) and `analyze_particles_headless`
  (line 822+). `pixel_size_um` is already read at line 939 via
  `_read_pixel_size_um(store)` and passed to `_add_area_um2_columns`;
  the same value is what we will use to convert µm² thresholds back
  to px per dataset.
- **Data panel**: `src/percell4/interfaces/gui/task_panels/data_panel.py`
  — `refresh_dataset_info` (279–319) is the single edit point for
  R4. `_info_label` is a `QLabel` updated by string concatenation.
- **`Store.metadata` shape**: per `src/percell4/CLAUDE.md` and the
  importer, `pixel_size_um`, `creation_bin`, and `native_shape` are
  flat keys on `store.metadata` (the `/metadata` HDF5 group).

### Institutional Learnings

Re-run the audit-driven retrieval before editing T1 modules — the
reader, importer, and the export use case are inside the T1 set
defined by the I/O principles audit
(`docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md`,
`docs/audits/canonical-sources-matrix.yaml`). The audit's seven I/O
principles apply directly here, especially "single canonical source"
for `pixel_size_um` and "metadata round-trips with the bytes".

### External References

- TIFF 6.0 specification: `XResolution`, `YResolution`, `ResolutionUnit`,
  `ImageDescription`, `Software`, `DateTime` tags.
- `tifffile.imwrite` accepts a `resolution=(xres, yres)` tuple and a
  `resolutionunit` parameter (or via `extratags`). Both fields can be
  set in a single call.

---

## Key Technical Decisions

- **`pixel_size_um` is the single source of truth.** Both export and the
  Dataset Info display read it from `store.metadata`. The writer derives
  the TIFF rational from `pixel_size_um` rather than carrying a raw tag
  bundle through HDF5 — this keeps a single canonical field and avoids a
  second source of truth that could drift.
- **Effective resolution for binned exports.** Output pixel size is
  `store.metadata["pixel_size_um"] × view_bin`. We *do not* store
  separate "raw" and "native" pixel sizes; `creation_bin` scaling is
  already applied at import, and view-bin scaling happens at export
  time.
- **ResolutionUnit choice on export.** Always emit centimeters
  (`ResolutionUnit = 3`) because that's what the source files in this
  lab use and what `tifffile`'s default resolution rational
  representation handles cleanly. Inch (`2`) is round-trippable but
  unnecessary.
- **Descriptive tags (ImageDescription, Software, DateTime) — minimal.**
  Set `Software = "PerCell4 <version>"` and `DateTime = export
  timestamp`. We do not attempt to round-trip a free-form
  `ImageDescription` because we never captured it. This keeps the round
  trip honest about what's preserved (spatial calibration) vs. what's
  derived (a description of what we exported).
- **Min particle area unit is per-config, applied per-dataset.** The
  dialog stores `(value, unit)`. The runner converts µm² → px
  independently for each dataset using that dataset's
  `pixel_size_um`. If a dataset lacks `pixel_size_um` and the user
  selected µm², the workflow fails fast for that dataset with a clear
  message; px mode never depends on `pixel_size_um`.
- **Switching units in the dialog does not auto-convert the entered
  value.** The user types intent. Auto-conversion would obscure the
  fact that µm² mode is per-dataset (each dataset re-converts at runtime
  with its own pixel size), and would force a pixel-size-known dependency
  inside the dialog that doesn't exist there today.
- **Dataset Info displays the *stored* (creation-bin-scaled) pixel size.**
  This matches what every other downstream consumer (`_add_area_um2_columns`,
  the to-be-added µm² threshold conversion) actually uses. When
  `View bin > 1`, the display additionally notes the effective view-bin
  pixel size so users can reason about exported files.

---

## Open Questions

### Resolved During Planning

- Should we capture and round-trip the full TIFF tag bundle (including
  `ImageDescription`, `Software`, `DateTime`)? **No** — out of scope per
  Key Decisions. We derive resolution tags from `pixel_size_um` and emit
  our own `Software` / `DateTime`.
- Switching units in the dialog — auto-convert or preserve numeric value?
  **Preserve.** User re-enters intent.
- Where does µm² → px conversion happen? **In the workflow phase**, not
  in the dialog or the dataclass post-init, because each dataset uses
  its own `pixel_size_um`.

### Deferred to Implementation

- Exact widget swap (QSpinBox ↔ QDoubleSpinBox) versus a single
  QDoubleSpinBox with dynamic decimals + step — implementer's call.
  Behavior matters; widget type does not.
- Precise format string for the Dataset Info pixel-size line — final
  wording can be settled at implementation time.

---

## Implementation Units

- U1. **TIFF writer emits resolution tags**

**Goal:** Add a small helper that maps a `pixel_size_um` scalar to
`(resolution, resolutionunit)` arguments suitable for
`tifffile.imwrite`, plus a thin `Software` + `DateTime` tag, and wire it
through every TIFF export call site.

**Requirements:** R1, R2, R3, R6

**Dependencies:** None

**Files:**
- Create: `src/percell4/adapters/tiff_writer.py`
- Modify: `src/percell4/application/use_cases/export_images.py`
- Modify: `src/percell4/application/use_cases/batch_export_images.py`
  (only if it invokes `tifffile.imwrite` directly — otherwise unchanged)
- Test: `tests/adapters/test_tiff_writer.py`
- Test: `tests/application/test_export_images_metadata.py`

**Approach:**
- New helper `write_tiff_with_metadata(path, data, pixel_size_um, view_bin)`
  centralizes resolution-tag derivation: when `pixel_size_um` is `None`,
  write bare (legacy compatibility); otherwise emit
  `resolution=(px_per_cm, px_per_cm)` and `resolutionunit="CENTIMETER"`,
  with `px_per_cm = 10000.0 / (pixel_size_um × view_bin)`. Always emit
  `Software` and `DateTime`.
- `ExportImages.execute` reads `pixel_size_um` from the repo (new
  repo accessor or via `store.metadata`) once per call and passes it,
  along with `request.view_bin`, to the helper at each of the three
  imwrite sites (channels, labels, masks).
- Repo accessor: extend `DatasetRepository` port with
  `pixel_size_um(handle) -> float | None` (and implement in the HDF5
  adapter) to keep `ExportImages` adapter-agnostic.

**Patterns to follow:**
- `_pixel_size_um_from_tags` in `readers.py` is the inverse formula —
  mirror it on the writer side and unit-test that the pair round-trips.
- T1 module audit: this touches `src/percell4/application/use_cases/`
  and `src/percell4/adapters/` — run `compound-engineering:ce-learnings-researcher`
  before editing per CLAUDE.md R15/R16.

**Test scenarios:**
- *Happy path.* Write a 2D array with `pixel_size_um=0.1204`,
  `view_bin=1`, then read back with `read_tiff_metadata` →
  resulting `pixel_size_um` equals `0.1204` within `1e-4`.
- *Happy path.* Same flow with `view_bin=2` → readback equals
  `0.2408` (exported file describes its own coarser sampling).
- *Edge case.* `pixel_size_um=None` → no resolution tags written;
  readback `pixel_size_um` is missing; data array round-trips.
- *Edge case.* `pixel_size_um=0` or negative → treated as `None`
  (defensive — matches reader's defensive checks).
- *Edge case.* Labels (`uint32`) and masks (`uint8`) get resolution
  tags identical to channels, since spatial calibration applies to
  all pixel types.
- *Integration.* `ExportImages.execute` with one channel + one label +
  one mask, `pixel_size_um=0.1204`, `view_bin=1` → all three output
  TIFFs reload with `pixel_size_um≈0.1204` via `read_tiff_metadata`.

**Verification:**
- Exporting a dataset and re-importing the exported TIFFs through the
  existing import path produces a new HDF5 with the same
  `pixel_size_um` (within float tolerance).
- Opening an exported TIFF in ImageJ (Image → Properties) shows the
  correct µm/px scale.

---

- U2. **Dataset Info shows pixel resolution**

**Goal:** Add a `Pixel size:` line to `refresh_dataset_info` on the
Data tab, showing both linear and areal forms, plus an effective
view-bin pixel size when `view_bin > 1`.

**Requirements:** R4, R6

**Dependencies:** None

**Files:**
- Modify: `src/percell4/interfaces/gui/task_panels/data_panel.py`
- Test: `tests/interfaces/gui/task_panels/test_data_panel_info.py`
  (extend if it exists; otherwise create)

**Approach:**
- Inside `refresh_dataset_info`, read `meta.get("pixel_size_um")` (same
  pattern as `creation_bin`, `native_shape`).
- Format:
  - `Pixel size: 0.1204 µm/px (0.01449 µm²/px)` when known.
  - `Pixel size: unknown` when missing.
  - When `active_bin > 1`, append an effective-line:
    `View-bin pixel size: 0.2408 µm/px (0.05799 µm²/px)`.
- Insert the new line(s) between `bin_line` and the Labels / Masks
  line.

**Patterns to follow:**
- Existing pattern in `refresh_dataset_info` (lines 295–317) — read
  `meta`, format, concatenate.

**Test scenarios:**
- *Happy path.* Dataset with `pixel_size_um=0.1204` and `active_bin=1` →
  panel text contains `0.1204 µm/px` and `0.01449 µm²/px`.
- *Happy path.* Same dataset with `active_bin=2` → panel text contains
  a `View-bin pixel size: 0.2408 µm/px` line.
- *Edge case.* Dataset whose `/metadata.pixel_size_um` is missing →
  panel text contains `Pixel size: unknown` and no µm² value.
- *Edge case.* `refresh_dataset_info` called with no dataset loaded
  remains "No dataset loaded" (current behavior unchanged).

**Verification:**
- Load the test fixture used in research
  (`/Volumes/NX-01-A/2026-05-21_export/datasets/60min_As_Merged.h5` once
  it carries a `pixel_size_um=0.12034`) — Dataset Info reports
  `Pixel size: 0.1203 µm/px (0.01449 µm²/px)`.

---

- U3. **Min particle area gains a unit selector in the workflow dialog**

**Goal:** Add a `px` / `µm²` combo next to `_particle_min_area`,
expose the chosen unit through `ParticleSettings`, and surface a
read-only hint of the dataset's pixel size so the user can sanity-check
the µm² value they entered.

**Requirements:** R5, R6

**Dependencies:** None

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Modify: `src/percell4/workflows/models.py`
- Modify: `src/percell4/workflows/artifacts.py`
  (JSON serialization of the new field)
- Test: `tests/workflows/test_particle_settings_units.py`
- Test: `tests/gui/workflows/single_cell/test_config_dialog_min_area_unit.py`

**Approach:**
- In `ParticleSettings`, change `min_area: int` to two fields:
  `min_area_value: float` (replace) and
  `min_area_unit: Literal["px", "um2"]` (new, default `"px"`).
  Keep backwards-compatible JSON read (treat a bare int as
  `{"min_area_value": v, "min_area_unit": "px"}`).
- In `_build_particle_group`, add a `QComboBox` with entries `px` and
  `µm²`. When `µm²` is selected, route the spinbox into a
  `QDoubleSpinBox` mode (e.g., decimals = 4, suffix = ` µm²`,
  range = `0 … 1e6`). When `px` is selected, use the integer-step
  behavior (decimals = 0, suffix = ` px`, range = `0 … 1_000_000`).
  Implementation may use a single `QDoubleSpinBox` reconfigured on
  unit change rather than swapping widgets.
- The form label changes to a static `Min particle area:`; the unit
  lives in the combo, not the label.
- Switching units leaves the numeric value untouched (per Key
  Decisions).
- No pixel-size lookup inside the dialog — the dialog does not depend
  on which datasets are queued.

**Patterns to follow:**
- Existing `QComboBox` usages in the dialog
  (`_thresholding_rounds_table`, `_csv_columns_picker`) for combo
  styling and signal wiring.

**Test scenarios:**
- *Happy path.* Construct `WorkflowConfigDialog`, leave defaults →
  resulting config has `min_area_value=0.0`, `min_area_unit="px"`.
- *Happy path.* Switch unit combo to `µm²`, enter `0.5`, accept →
  config carries `min_area_value=0.5`, `min_area_unit="um2"`.
- *Happy path.* `ParticleSettings.to_json` and
  `ParticleSettings.from_json` round-trip both new fields.
- *Edge case.* Loading a legacy `run_config.json` written under the
  old schema (`"particle_settings": {"min_area": 0}`) yields
  `min_area_value=0.0`, `min_area_unit="px"`.
- *Edge case.* Switching unit combo px → µm² → px leaves the entered
  numeric value unchanged.

**Verification:**
- Saved `run_config.json` under either unit re-parses cleanly into
  `ParticleSettings`.
- Dialog visual: the combo is to the right of the spinbox in the
  same form row.

---

- U4. **Workflow applies µm² thresholds per-dataset using each dataset's
  pixel size**

**Goal:** Make `analyze_particles` and `analyze_particles_headless`
honor `ParticleSettings.min_area_unit`. When `unit == "um2"`, convert
to a per-dataset integer pixel threshold using that dataset's
`pixel_size_um`. When `unit == "px"`, behave exactly as today.

**Requirements:** R5, R6

**Dependencies:** U3 (the new `ParticleSettings` fields must exist)

**Files:**
- Modify: `src/percell4/workflows/phases.py`
- Test: `tests/workflows/test_analyze_particles_units.py`

**Approach:**
- Where `particle_settings.min_area` is read today (lines ~971, ~1128),
  introduce a small private helper
  `_resolve_min_area_px(particle_settings, pixel_size_um) -> int`:
  - `unit == "px"` → `int(round(min_area_value))`.
  - `unit == "um2"` and `pixel_size_um is not None` →
    `int(round(min_area_value / (pixel_size_um ** 2)))`.
  - `unit == "um2"` and `pixel_size_um is None` → raise a clear
    `ValueError` ("dataset X: µm² threshold requires a known pixel
    size; re-import this dataset with resolution metadata").
- Log the resolved per-dataset px threshold into `run_log.jsonl`
  (existing log channel) so a researcher can audit what was actually
  applied.
- Existing `_read_pixel_size_um(store)` call at line 939 is the
  source of `pixel_size_um` here.

**Patterns to follow:**
- `_add_area_um2_columns` already reads `pixel_size_um` from the
  store and consumes it; mirror that lookup ordering.
- Error propagation in the surrounding workflow phases —
  surface as a `PhaseResult` failure for that dataset, not a global
  abort.

**Test scenarios:**
- *Happy path.* `unit="px"`, `min_area_value=10`, any `pixel_size_um` →
  resolved threshold = 10. (Backwards-compat with current behavior.)
- *Happy path.* `unit="um2"`, `min_area_value=0.5`,
  `pixel_size_um=0.12034` → resolved threshold = 35
  (`round(0.5 / 0.01448)`).
- *Happy path.* `unit="um2"`, `min_area_value=0.0` → resolved
  threshold = 0 regardless of `pixel_size_um`.
- *Edge case.* `unit="um2"`, `pixel_size_um=None` → workflow fails
  for that dataset with a message that names the unit and the
  dataset.
- *Edge case.* Two datasets in one run with different `pixel_size_um`
  values + `unit="um2"`, `min_area_value=0.5` → each dataset converts
  independently; resolved px thresholds differ.
- *Integration.* End-to-end run of a 2-dataset workflow with
  µm² mode produces the same per-cell particle counts as an
  equivalent px-mode run using the manually-computed per-dataset
  thresholds.

**Verification:**
- Run the existing single-cell workflow against the
  `60min_As_+_Noco_Merged` dataset with `min_area_value=0.5`,
  `unit="um2"` → the resolved-threshold log line reports `35 px`,
  and `particles.parquet` excludes connected components smaller
  than 35 px.

---

## System-Wide Impact

- **Interaction graph:** Dataset Info refresh path is unchanged; the
  new line piggy-backs on existing StateChange-driven refreshes. The
  workflow dialog → `ParticleSettings` → phase consumer chain gains
  one new field, threaded through unchanged serialization. The TIFF
  writer change is local to the `export_images` use case (single + batch
  paths) and a new helper.
- **Error propagation:** New failure mode — µm² mode against a dataset
  missing `pixel_size_um` raises in the phase. Bubbles up as a
  `PhaseResult` failure (per workflow runner pattern) rather than
  crashing the run.
- **State lifecycle risks:** None — every change reads from
  `store.metadata`, which is read-only after import.
- **API surface parity:** `DatasetRepository` port gains
  `pixel_size_um(handle)`. All implementations (HDF5 adapter; any test
  fakes) must add it.
- **Integration coverage:** TIFF round-trip (export → re-import) is the
  primary integration assertion; the unit test fixture exists in
  `tests/adapters/`. The workflow µm² path is covered by an end-to-end
  test that runs the analyze-particles phase on a real dataset.
- **Unchanged invariants:** `pixel_size_um` storage location
  (`/metadata.pixel_size_um`) and creation-bin scaling at import are
  unchanged. The px-mode behavior of Min particle area is bit-identical
  to today; this plan adds a µm² mode rather than reinterpreting the
  existing one.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `ParticleSettings` schema change breaks existing `run_config.json` files. | Backwards-compatible JSON loader: accept bare int `min_area`, normalize to `(value, "px")`. Add a focused test. |
| `tifffile`'s `resolution` argument semantics differ across tifffile versions. | Pin the assumption in a unit test that round-trips through `read_tiff_metadata`; the test will fail loudly if the library reinterprets the field. |
| `view_bin × pixel_size_um` mis-scaling on exports silently produces wrong calibration. | Round-trip integration test (U1) explicitly exercises `view_bin > 1`. |
| µm² threshold + heterogeneous pixel sizes confuses users who expect a single threshold across all datasets. | Log the resolved per-dataset px threshold in `run_log.jsonl`. Dialog label can also include a short helper tooltip describing the per-dataset behavior. |
| Pre-existing bug noted by research: `_add_area_um2_columns` may use unscaled `pixel_size_um` against view-bin-scaled data. | Out of scope for this plan, but flag as a follow-up; this plan's µm² threshold conversion uses the same `_read_pixel_size_um` and inherits the same assumption (always operates at native, never at view-bin in workflow context). |

---

## Documentation / Operational Notes

- Update `src/percell4/adapters/CLAUDE.md` (if it exists) and the
  `docs/audits/canonical-sources-matrix.yaml` entry for TIFF I/O to
  reflect that exports now round-trip spatial calibration.
- If a `docs/solutions/` entry for "TIFF metadata round-trip" does not
  already exist, capture one after merge (small architecture-pattern
  note: how the writer derives tags from `pixel_size_um`, why we don't
  cache a tag bundle).
- No migration script needed; legacy HDF5 files that already carry
  `pixel_size_um` work transparently.

---

## Sources & References

- TIFF metadata evidence:
  `/Volumes/NX-01-A/2026-05-21_export/2026-05-19_A549_mNG11-G3BP1_KI_stress_granule_EU_labeling/*.tif`
  (`XResolution=(4294967295, 51698)`, `ResolutionUnit=3` → 0.12034 µm/px).
- Workflow run that motivated the plan:
  `/Volumes/NX-01-A/2026-05-21_export/analysis/run_2026-05-21T181854Z_b9812c6d/`.
- Related plan (not modified by this one):
  `docs/plans/2026-05-19-001-feat-binned-tif-export-option-plan.md`.
- I/O principles audit:
  `docs/brainstorms/2026-04-29-io-principles-audit-and-remediation-brainstorm.md`,
  `docs/audits/canonical-sources-matrix.yaml`.
- Reader (canonical source for the inverse formula):
  `src/percell4/adapters/readers.py:27-63`.
