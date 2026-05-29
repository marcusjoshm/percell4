---
title: "feat: Match per-particle multi-channel CSV to original script (group, cell_<ch>_mean, exact order)"
type: feat
status: completed
date: 2026-05-29
deepened: 2026-05-29
---

# feat: Match per-particle multi-channel CSV to original script

## Overview

Reshape the **`per_particle_multichannel`** analysis's per-particle CSV
(`particle_table`) so it matches the column set and order of the lab's original
`make_csv.py` script, generalized to arbitrary channel names. For a dataset with
analyzed channels `{CA-SiR, mNG, mTQ2}` and only `mTQ2` selected for a whole-cell
mean, the header becomes exactly:

```
group, particle_id, cell_id,
cell_mTQ2_mean,
particle_area_px, donut_area_px,
condensed_CA-SiR_mean, dilute_CA-SiR_mean, CA-SiR_condensed_over_dilute,
condensed_mNG_mean,    dilute_mNG_mean,    mNG_condensed_over_dilute,
condensed_mTQ2_mean,   dilute_mTQ2_mean,   mTQ2_condensed_over_dilute
```

Generalized: `group, particle_id, cell_id, [cell_<ch>_mean for each selected
channel, sorted], particle_area_px, donut_area_px, [condensed_<ch>_mean,
dilute_<ch>_mean, <ch>_condensed_over_dilute for each analyzed channel, sorted]`.

Three behavior changes drive this:

1. **Exact columns + order.** Drop the `condensed_<ch>_integ` / `dilute_<ch>_integ`
   columns from `particle_table` and emit columns in exactly the order above.
2. **New per-particle `cell_<ch>_mean`.** Each particle row gains the whole-cell
   mean of its parent cell, for a user-chosen subset of channels — a value that
   today exists only in the per-*cell* table (`single_cell=True`). Rows stay
   **one per particle** (default: no channel selected → no `cell_*_mean` columns).
3. **Leading id column named `group`.** The framework's auto-prepended id column
   (currently `dataset`, = `.h5` filename stem) is renamed to `group` for this
   analysis via a small, generalized opt-in on the shared batch writer.

The dialog gains a **"cell mean" checkbox on each measurement-channel row** so the
user picks which channels get a `cell_<ch>_mean` column (one, several, or all).

---

## Problem Frame

The lab has an original Python script (`make_csv.py`, on a Box drive **not mounted
on this machine** — confirmed `/Users/leelab/Library/CloudStorage` is absent) that
emits a per-particle condensate CSV with this exact header for a 3-channel dataset:

```
group  particle_id  cell_id  cell_mTQ2_mean  particle_area_px  donut_area_px
condensed_CA-SiR_mean  dilute_CA-SiR_mean  CA-SiR_condensed_over_dilute
condensed_mNG_mean     dilute_mNG_mean     mNG_condensed_over_dilute
condensed_mTQ2_mean    dilute_mTQ2_mean    mTQ2_condensed_over_dilute
```

PerCell4 already has a faithful in-tree port of that script's math: the pure core
`src/percell4/domain/analysis/_impl/per_particle_multichannel.py` (single source of
truth, shared by the repo-root CLI `per_particle_multichannel.py` and the registered
`PerParticleMultichannel` analysis). The registered analysis's current
`particle_table` differs from the target in four ways: it has no `group` column (it
has a framework `dataset` column instead), it carries extra `*_integ` columns, its
column order differs, and it has **no** per-particle `cell_<ch>_mean`.

**Two authorities, deliberately split.** Because Box is unmounted, the original
`make_csv.py` cannot be read, so:

- **Column ORDER + membership is authoritative from the user's stated header** (the
  `group … mTQ2_condensed_over_dilute` list above). This represents the original
  `make_csv.py` output the user wants to reproduce.
- **The MATH/semantics is authoritative from the in-tree pure core** (donut geometry,
  particle→cell assignment, `condensed/dilute` means, `<ch>_condensed_over_dilute`
  ratio, and `cell_<ch>_mean` = whole-cell `np.mean`), shared by the repo-root CLI and
  the registered analysis.

These are split on purpose because the in-tree CLI's `save_results` (lines 112–159) does
**not** match the target order: it interleaves `cell_<ch>_mean` *inside* each channel's
block and emits it *only* in single-cell mode. So the CLI proves the numbers, not the
layout. This plan reproduces the user's header exactly while reusing the core's math,
generalized to any channel names, and adds per-particle `cell_<ch>_mean` (a new
capability the CLI does not have).

---

## Requirements Trace

- R1. `particle_table` has **exactly the following columns, in exactly this order**,
  including only the conditional columns that apply for the given inputs: `group`,
  `particle_id`, `cell_id` (present iff a cell-label image `cp_mask` is supplied),
  then `cell_<ch>_mean` for each user-selected channel (sorted by channel name),
  then `particle_area_px`, `donut_area_px`, then for each analyzed channel in sorted
  order the triple `condensed_<ch>_mean`, `dilute_<ch>_mean`,
  `<ch>_condensed_over_dilute`. **No `*_integ` columns.** "Exactly" governs the
  ordering and the absence of extra columns; the conditional members (`cell_id`,
  `cell_<ch>_mean`) are included or omitted per the rules above, never reordered.
- R2. Each row of `particle_table` is **one particle**, never a whole-cell aggregate.
- R3. `cell_<ch>_mean` on a particle row is the whole-cell mean of channel `<ch>`
  over the cell the particle is assigned to, emitted only for user-selected channels,
  and numerically equal to the `cell_table`'s `cell_<ch>_mean` for that same
  cell/channel.
- R4. The dialog lets the user choose, **per measurement-channel row**, whether that
  channel contributes a `cell_<ch>_mean` column (one, several, or all). The control
  **defaults off** and is disabled when no cell-label image (`cp_mask`) is assigned.
- R5. The leading id column is named **`group`** and equals the `.h5` filename stem.
- R6. All of the above generalizes to arbitrary channel names — nothing hardcodes
  `mTQ2` / `CA-SiR` / `mNG`.
- R7. Other analyses' CSV output is unchanged (their id column stays `dataset`); their
  existing parity/regression tests still pass.

---

## Scope Boundaries

- **`cell_table` (`single_cell=True`) is not reshaped.** It already emits
  `cell_<ch>_mean` (+ median/mode/min/max/integ) for all channels and is not the
  user's target. It **does** inherit the cosmetic id-column rename `dataset` → `group`
  (the `dataset_column_label` attribute is class-wide, so it applies to every table
  this analysis emits). **This rename on `cell_table` is intended** — it keeps both
  tables of one analysis consistent — and no existing test asserts a `dataset` column
  on the multichannel `cell_table` (verified: `test_single_cell_produces_cell_table`
  calls `run_analysis` directly and checks only metric columns; `test_dataset_column_first`
  uses `per_particle_donut`).
- **Out of scope: the repo-root CLI `per_particle_multichannel.py`.** The user works
  through the GUI analysis. The CLI keeps its current output (it already leads with
  `group` and includes `*_integ`). U2's core change is backward-compatible
  (`cell_mean_channels` defaults to "none"), so the CLI — and its committed regression
  fixtures — stay byte-for-byte unchanged.
- **Out of scope: changing the donut/condensed/dilute detection math.** Buffer, donut
  width, min-size, particle→cell assignment, and the ratio are reused verbatim.
- **Out of scope: importing/reading the original `make_csv.py`.** Box is unmounted; the
  in-tree CLI port is the authoritative proxy. If the original later becomes available
  and reveals a different region/threshold definition, that is a separate change.

---

## Context & Research

### Relevant Code and Patterns

- **Pure core (single source of truth):**
  `src/percell4/domain/analysis/_impl/per_particle_multichannel.py`
  - `analyze_particles()` builds one row dict per particle with
    `particle_id`, `particle_area_px`, `donut_area_px`, and per-channel
    `condensed_<ch>_mean` / `dilute_<ch>_mean` / `condensed_<ch>_integ` /
    `dilute_<ch>_integ` / `<ch>_condensed_over_dilute` (lines 98–119).
  - `run_one_image_set()` assigns `cell_id` to each particle row when a `cp_mask`
    is provided (lines 303–307) — this is the join key for the new `cell_<ch>_mean`.
  - `_whole_cell_stats()` (lines 131–150) computes `float(np.mean(values))` over a
    cell's pixels; `aggregate_by_cell()` uses it for the existing per-cell
    `cell_<ch>_mean` (lines 192–202). The new per-particle `cell_<ch>_mean` must reuse
    the **same mean definition** so values match across tables (R3).
- **Module schema/wiring:**
  `src/percell4/application/analysis/modules/per_particle_multichannel.py`
  - Channels modeled as `channel_1` (required) + `channel_2..8` (optional), NOT an
    `input_group`. `_CHANNEL_ROLES`, `_MAX_CHANNELS = 8` (lines 41–45).
  - `run()` already receives `layer_map`, maps role → chosen layer name, and builds
    `channels = dict(sorted(channels.items()))` — **keyed by layer name, sorted by
    name** (lines 196–201). This sorted dict is the canonical channel order and the
    place the reshape and `cell_mean_channels` selection belong.
- **Shared batch writer (T1):**
  `src/percell4/application/use_cases/run_analysis_batch.py`
  - `_persist_outputs()` does `df.insert(0, "dataset", h5_path.stem)` for every
    `TableOutput` (line 205). `_write_csvs()` derives the per-dataset filename stem from
    `df["dataset"].iloc[0]` (line 227) — it has **no access to `cls`**, so the label
    must be threaded in as a parameter.
- **Base class:** `src/percell4/domain/analysis/base.py` — class attributes end at
  `dialog_class` (line 70). There is **no** existing `dataset_column_label`; this plan
  **adds** it as a new base-class attribute (default `"dataset"`). (Earlier drafts
  claimed lines 58–60 "reserved" it; that was wrong — those lines are the `presets`
  comment.)
- **Dialog:** `src/percell4/gui/per_particle_multichannel_dialog.py`
  - Dynamic 1–8 channel rows: `_add_channel_row()` (lines 279–302), `_resolve_layer_map()`
    (lines 505–520) — note role keys are derived by **positional enumeration**
    (`channel_{i+1}` from `enumerate(self._channel_rows)`), `_collect_params()`
    (lines 522–523), generic param form `_build_section_params()` (lines 238–255),
    refresh cascade `_refresh_state()` (lines 379–385), `requires` gating
    `_refresh_requires_gating()` (lines 434–448).
  - Already an **Action**: reads no session selection fields, only calls
    `session.refresh_resource_lists` at end of run (lines 602–622). Already wrapped in
    `wrap_in_scroll` + `cap_to_screen` (lines 82, 150).
- **Existing tests that constrain this work:**
  - `tests/test_domain/test_per_particle_multichannel_pure.py` — asserts `*_integ`
    keys **exist** in pure-core rows (lines 43–44). The core is unchanged for existing
    columns, so these stay green and must NOT be removed.
  - `tests/test_application/test_per_particle_multichannel_module.py:81–114`
    (`test_columns_named_by_layer_and_parity_with_cli`) — compares the framework
    `particle_table` against the committed CLI fixture `group_a_expected/combined.csv`
    (which contains `*_integ`) by reindexing both to `sorted(columns)`. **This test
    breaks when the framework drops `*_integ`** and must be updated (U5).
  - `tests/test_scripts/test_per_particle_multichannel_regression.py` — runs the
    **CLI** and compares to the same fixtures. CLI is out of scope/unchanged, so this
    stays green and the **fixtures are not regenerated**.
  - `tests/test_application/test_run_analysis_batch.py:77`
    (`test_dataset_column_first`) — asserts `columns[0] == "dataset"` for
    `per_particle_donut`. The default `dataset_column_label="dataset"` keeps it green;
    this is the R7 guard test.
- **Sibling/canonical patterns:** `..._impl/whole_field_intensity.py` (mean-over-mask:
  `cell_region = cp_mask_img == cell_id`; `np.nanmean` guard) and
  `application/analysis/modules/per_particle_donut.py` (declared `canonical_source`).

### Institutional Learnings (`ce-learnings-researcher`)

- `docs/solutions/architecture-patterns/registered-analysis-framework.md` (canonical;
  `canonical_source: per_particle_donut.py`). Load-bearing constraints:
  - **Pure core is single source of truth** — thread the channel subset as a param;
    do not fork the `cell_<ch>_mean` math. Keep `_impl/` dataset-id-free.
  - **CSV column order is NOT auto-verified** — the framework's parity comparison
    reindexes to `sorted(columns)`, so order drift ships silently. An **explicit
    `list(df.columns)` equality assertion** is required (U3).
  - **Channel multi-select → per-slot `BoolParam`**, never a multi-role `input_group`.
  - **Dataset-identity columns belong at the framework/module layer**, not `_impl/`.
  - **Presets are immutable** — N/A here (`presets = {}` for this analysis).
- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — keep the new per-row checkbox
  **inside** the `wrap_in_scroll` content; `tests/test_gui/test_dialog_helper_compliance.py`
  AST-checks this.
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — wire the new
  checkbox's `toggled`/`stateChanged` to `_refresh_state()` **at construction**; add a
  signal-path test using `setCheckState()`.
- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-*.md` —
  guardrail: the dialog stays an **Action**; do not introduce `session.set_active_*` /
  filter / selection writes.

### External References

- None required. Well-patterned, in-codebase change; the original script's intent is
  captured by the in-tree CLI port.

---

## Key Technical Decisions

- **Reshape at the module `run()` boundary, not in the pure core.** The core keeps
  emitting full rows (incl. `*_integ`); `run()` selects and orders columns to the exact
  target. Preserves single-source-of-truth, keeps the CLI's richer output and its
  regression fixtures intact, and keeps the pure-core `*_integ` tests green.
- **`cell_<ch>_mean` enrichment lives in the pure core** (it is math, and must match
  `cell_table` numerically per R3). The core gains a `cell_mean_channels: list[str] | None`
  parameter; when provided with a `cp_mask`, it attaches `cell_<ch>_mean` to each
  particle row by `cell_id`, using the same `float(np.mean(...))` definition as
  `_whole_cell_stats`.
- **One canonical sorted channel list.** Both the `cell_<ch>_mean` block and the
  `condensed/dilute/ratio` block derive their order from the **same**
  `sorted(channels.keys())` (the layer-name-keyed dict already built in `run()`).
  `cell_mean_channels` is `sorted(set(...))` of that same key space — so the cell-mean
  infixes are always a sorted **subsequence** of the analyzed infixes (no cross-block
  divergence). The ordered column list is built **only** from these known channel-name
  lists — never by parsing or filtering existing DataFrame column strings (defends
  against channels named e.g. `cell` or `mNG_condensed`).
- **Sort is Python default `sorted()` (ASCII, case-sensitive), matching the CLI's
  `sorted(all_channel_names)`.** So `CA-SiR` sorts before `mNG` (uppercase < lowercase),
  which matches the user's example header; a lowercase channel like `actin` would sort
  after `CA-SiR`. This is faithful to the proxy and is the documented behavior — not a
  case-insensitive sort.
- **Channel (layer) names are trusted verbatim — no sanitization.** Output columns are
  `f"condensed_{name}_mean"` etc. with `name` = the user-chosen layer name. Names
  containing reserved tokens (`_mean`, `condensed`, `cell`) produce ugly-but-unique
  headers; they do not collide at the dict level (names are distinct keys). R6
  ("arbitrary channel names") means *the example names aren't hardcoded*, not that every
  pathological string yields a pretty header. A `cell`-named-channel test (U3) locks the
  no-crash, columns-built-from-known-names guarantee.
- **Per-channel cell-mean selection is modeled as 8 per-slot `BoolParam`s**
  (`channel_1_cell_mean … channel_8_cell_mean`, default `False`). This is the only
  framework-native way to express a dynamic subset (no list param type exists), is
  CLI-accessible, and is recorded in `run_config.json`. A single `ChoiceParam`
  (`none`/`all`/`channel-1-only`…) was rejected because it cannot express an arbitrary
  subset. The dialog renders them as per-row checkboxes (not in the generic param form).
  Acknowledged tradeoff: this couples four dialog touch-points (declare,
  exclude-from-form, render-per-row, merge-in-collect) — mitigated by excluding them from
  the generic form via a single declared key set (`_CELL_MEAN_PARAM_KEYS`), not an ad-hoc
  name match.
- **The cell-mean bools deliberately carry NO `requires=("cp_mask",)`.** The framework
  runner *raises `ValueError`* when a `BoolParam` is `True` but a `requires` role is
  absent from the layer map (the same mechanism that makes `single_cell` without
  `cp_mask` raise). If the cell-mean bools carried `requires`, a stray checked box with
  no `cp_mask` — reachable via the CLI/API path, or a dialog-gating slip — would
  **hard-fail the entire dataset**. Instead: the pure core (U2) silently no-ops cell-mean
  when `cp_mask is None`, and `run()` (U3) defensively computes `cell_mean_channels=[]`
  when `"cp_mask" not in inputs`. The dialog still disables the checkboxes without a
  `cp_mask` purely for UX, but correctness no longer depends on that gating. (`single_cell`
  keeps its `requires` — that one genuinely cannot proceed without `cp_mask`.)
- **Rename the id column via a generalized base attribute** `dataset_column_label`
  (new on `Analysis`, default `"dataset"`), set to `"group"` on
  `PerParticleMultichannel`. The shared writer reads it (via
  `getattr(cls, "dataset_column_label", "dataset")`) for both the inserted column name
  and — threaded into `_write_csvs` — the per-dataset filename-stem lookup. Default
  value → zero behavior change for all other analyses (R7).
- **Empty/partial frames use `df.reindex(columns=order)`, never `df[order]`.** On a
  dataset with zero surviving particles, `pd.DataFrame([])` has no columns; `df[order]`
  would `KeyError`. `reindex` yields the exact ordered columns (NaN-filled, zero rows),
  preserving the contract without crashing.
- **Bump `PerParticleMultichannel.version` `1.0.0 → 1.1.0`** — the `particle_table`
  output schema changes. (Grep for any test asserting the version string first; none
  known.)
- **Default per-channel cell-mean checkbox to OFF (opt-in).** `cell_<ch>_mean` needs a
  `cp_mask`; defaulting off avoids surprising NaN columns. Acknowledged UX tension: the
  original target CSV *includes* `cell_mTQ2_mean`, so reproducing it requires the user to
  check the box for the desired channel each run. This is the intended tradeoff (off is
  safe without a `cp_mask`); it is the only manual step to recreate the original output.
- **Column reshape applies only to `particle_table` (`single_cell=False`).**

---

## Open Questions

### Resolved During Planning

- *Match scope?* → **Exact match**: trim `*_integ`, reorder to the target, generalized
  to any channel names.
- *`group` source?* → **`.h5` filename stem.**
- *cell-mean selection UI?* → **Checkbox per measurement-channel row, default off.**
- *`group` vs the framework's `dataset` column?* → **Rename to `group`** for this
  analysis (exact header, no duplicate), via a generalized opt-in on the shared writer.
- *Sorted order for multiple `cell_<ch>_mean` columns?* → **Sorted by channel name**,
  matching the analyzed-channel block and the original CLI's `sorted(...)` ordering.
  The user's example exercised only a single cell-mean channel; sorted order is the
  documented, consistent choice for the multi-select case.

### Deferred to Implementation

- Exact helper/method names in the dialog for per-row checkbox wiring and the
  `_collect_params` merge.
- Whether the per-particle `cell_<ch>_mean` enrichment is cleanest as a small helper
  in the core vs inline in `run_one_image_set`.
- Behavior when the same layer is mapped to two channel slots (pre-existing: the
  layer-keyed dict collapses to one entry, last-wins). `cell_mean_channels` is
  de-duplicated via `sorted(set(...))`; documented as an accepted, pre-existing edge.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context, not
> code to reproduce.*

Data + control flow for one dataset (per-particle / `single_cell=False`):

```
Dialog (per channel row: layer combo + [cell mean] checkbox, default off, gated on cp_mask)
   │  layer_map = {mask, channel_1..N, cp_mask?}
   │  params    = {buffer, donut, min_size, single_cell=False, export_donuts,
   │               channel_1_cell_mean..channel_8_cell_mean}   # all 8 always present
   ▼
PerParticleMultichannel.run(inputs, params, layer_map)
   │  names      = layer_map                       # role -> chosen layer name
   │  channels   = dict(sorted({layername: arr}))  # canonical order
   │  cell_means = sorted(set( names[ch_role]
   │                 for ch_role in inputs if params[<role>_cell_mean] and "cp_mask" in inputs ))
   ▼
run_one_image_set(..., cell_mean_channels=cell_means)        # PURE CORE
   │  analyze_particles -> one row per particle (full cols incl *_integ)
   │  assign cell_id per particle (existing)
   │  NEW: for ch in cell_mean_channels: row['cell_<ch>_mean'] =
   │         float(np.mean(ch_img[cp_mask == row.cell_id]))   # == _whole_cell_stats mean
   ▼  returns particle_rows (superset of needed columns)
run() reshapes via df.reindex(columns=order)  -> EXACTLY (no 'group' yet):
   particle_id, [cell_id], cell_<sel>_mean..., particle_area_px, donut_area_px,
   <per sorted channel> condensed/dilute/ratio
   ▼
batch _persist_outputs: df.insert(0, getattr(cls,"dataset_column_label","dataset")="group", stem)
   _write_csvs(label="group"): per-dataset stem from df[label].iloc[0]
   ▼
combined_particle_table.csv / per_dataset/<stem>_particle_table.csv
   header == group, particle_id, cell_id, cell_<sel>_mean..., particle_area_px,
             donut_area_px, condensed_<ch>_mean, dilute_<ch>_mean, <ch>_condensed_over_dilute …
```

---

## Implementation Units

- U1. **Generalize the batch writer's id column (`dataset_column_label`)**

**Goal:** Let an analysis name its framework-injected id column. Default `"dataset"`
(no change for existing analyses); `PerParticleMultichannel` sets `"group"` in U3.

**Requirements:** R5, R7

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/analysis/base.py` (add new class attribute
  `dataset_column_label: ClassVar[str] = "dataset"` near `dialog_class`, with a short
  docstring note).
- Modify: `src/percell4/application/use_cases/run_analysis_batch.py`
  (`_persist_outputs` resolves `label = getattr(cls, "dataset_column_label", "dataset")`
  and uses it in `df.insert(0, label, stem)`; thread `label` as a parameter into
  `_write_csvs` and use it for the per-dataset stem lookup `df[label].iloc[0]` instead
  of the literal `"dataset"`).
- Test: `tests/test_application/test_run_analysis_batch.py`

**Approach:**
- Use `getattr(..., "dataset_column_label", "dataset")` so any third-party Analysis
  subclass without the attribute still works.
- `_write_csvs` currently has no `cls`; pass the resolved label down as a parameter (all
  tables in one batch share one `cls`, so a single `label` parameter suffices). **The
  load-bearing change is line 227** — `stem = df[label].iloc[0] if len(df) else "unknown"`
  (not the literal `df["dataset"]`); missing this makes the per-dataset filename lookup
  `KeyError` once the column is renamed to `group`. The `len(df)` guard already handles
  empty frames — keep it.

**Patterns to follow:** existing `_persist_outputs` / `_write_csvs` structure; keep the
single-insert-at-position-0 convention.

**Test scenarios:**
- Happy path (R7 guard): `test_dataset_column_first` (per_particle_donut, default label)
  still passes — `combined.columns[0] == "dataset"`, per-dataset filenames use the stem.
- Happy path (R5): a subclass/fixture with `dataset_column_label = "group"` produces a
  `group` first column (no `dataset` column), and per-dataset filenames still use the
  `.h5` stem (read via the renamed column).
- Edge case: empty table accumulator for a table name → no file written, no crash.

**Verification:** Existing whole_field and per_particle_donut batch/parity tests pass
unchanged; the override test shows a `group`-labelled id column and correct per-dataset
filenames.

---

- U2. **Pure-core per-particle `cell_<ch>_mean` enrichment**

**Goal:** Add an optional `cell_mean_channels` parameter to the pure core so each
particle row carries the whole-cell mean of its parent cell for the requested channels,
matching the `cell_table` definition. Own all pure-core tests for this behavior.

**Requirements:** R2, R3, R6

**Dependencies:** None

**Files:**
- Modify: `src/percell4/domain/analysis/_impl/per_particle_multichannel.py`
  (`run_one_image_set` gains `cell_mean_channels: list[str] | None = None`; after the
  existing per-particle `cell_id` assignment, attach `cell_<ch>_mean` for each requested
  channel present in `channels`).
- Test: `tests/test_domain/test_per_particle_multichannel_pure.py`

**Approach:**
- Active only when `cp_mask is not None` and `cell_mean_channels` is non-empty;
  otherwise rows are unchanged (keeps the CLI path and existing callers byte-for-byte
  identical, and keeps existing `*_integ` pure tests green).
- Compute each cell's mean once per requested channel from the **float64-coerced**
  `channel_images` dict (the same `arr.astype(np.float64)` arrays `analyze_particles` and
  `aggregate_by_cell` use — NOT the raw `channels` arg; a uint input would otherwise make
  `np.mean` round differently and break R3): `float(np.mean(ch_img[cp_mask == cid]))`,
  NaN when the cell has no pixels. Map onto particle rows by `cell_id`; particles with
  `cell_id == 0` (unassigned) get `np.nan`.
- Reuse the exact mean semantics of `_whole_cell_stats` (first tuple element) so R3
  holds.
- Skip any requested channel not in `channels` (no column, no `KeyError`).
- Rows stay one-per-particle; do not aggregate.
- **Test ownership:** U2 owns *all* pure-core tests for this analysis — the new
  enrichment tests below AND the pre-existing `*_integ`-still-present checks (which stay
  unchanged and green). U5 owns only framework-level edits.

**Patterns to follow:** `aggregate_by_cell` cell-pixel selection (`cp_mask_img == cell_id`,
lines 178–195) and `_whole_cell_stats` mean (line 144).

**Test scenarios:**
- Happy path: with `cp_mask` and `cell_mean_channels=["CA-SiR"]`, every particle row has
  `cell_CA-SiR_mean` and **no** `cell_mNG_mean`; row count == particle count (R2).
- Happy path / R3: the per-particle `cell_CA-SiR_mean` equals the `cell_table`
  `cell_CA-SiR_mean` (run once with `single_cell=True`) for the cell that particle
  belongs to (`== pytest.approx(...)`). **Restrict the comparison to particles with
  `cell_id != 0`** — `aggregate_by_cell` iterates only non-zero cell ids, so unassigned
  particles have no `cell_table` row to compare against.
- Edge case: `cell_mean_channels=None` (and `[]`) → particle rows contain no
  `cell_*_mean` keys (back-compat; the existing `*_integ` assertions still hold).
- Edge case: `cell_mean_channels=["CA-SiR"]` but `cp_mask=None` → no `cell_*_mean` keys,
  no crash.
- Edge case: a particle whose `cell_id == 0` → its `cell_CA-SiR_mean` is NaN.
- Edge case: `cell_mean_channels` containing a name not in `channels` → skipped, no
  `KeyError`.
- R6: a run with non-example channel names (e.g., `{"GFP", "foo"}`) and
  `cell_mean_channels=["foo"]` produces `cell_foo_mean` and the per-channel blocks for
  both — proving nothing hardcodes the example names.

**Verification:** Pure-core tests pass; the new column appears only for requested
channels; numeric parity with `cell_table` confirmed.

---

- U3. **Module schema, version, id-label, and `run()` reshape**

**Goal:** Wire the new selection params and the exact column contract into the registered
analysis: add per-slot cell-mean `BoolParam`s, set `dataset_column_label = "group"`,
bump the version, pass `cell_mean_channels` to the core, and reshape `particle_table`
to the exact target order (dropping `*_integ`).

**Requirements:** R1, R2, R3, R5, R6

**Dependencies:** U1 (id label), U2 (`cell_mean_channels`)

**Files:**
- Modify: `src/percell4/application/analysis/modules/per_particle_multichannel.py`
- Test: `tests/test_application/test_per_particle_multichannel_module.py`

**Approach:**
- Add `channel_{i}_cell_mean = BoolParam(default=False, desc=…)` for `i in 1..8` to
  `parameters` — **no `requires`** (see Key Technical Decisions: avoids the hard-batch-fail
  foot-gun). Expose the set of these keys as a module-level constant
  (`_CELL_MEAN_PARAM_KEYS`) so the dialog can exclude them from the generic form without
  an ad-hoc name match.
- Set `dataset_column_label = "group"`; set `version = "1.1.0"`.
- Verify `resolve_params` fills defaults for unsupplied params (confirmed: it backfills
  every declared param with `decl.default`, so `params=None`/`{}` → all 8 bools `False`).
  Add an assertion-bearing test for this.
- In `run()`: after building the sorted `channels` dict, **assert at least one channel is
  mapped** (`channels` non-empty — defends the CLI/API path; the dialog already enforces
  it). Then compute
  `cell_mean_channels = sorted(set(names[role] for role in _CHANNEL_ROLES if role in
  inputs and params.get(f"{role}_cell_mean"))) if "cp_mask" in inputs else []`. The
  `else []` is the defensive guard that makes the absent-`requires` decision safe:
  cell-mean is silently dropped when no `cp_mask`, never raised. Pass to
  `run_one_image_set(..., cell_mean_channels=…)`.
- After `pd.DataFrame(result["particle_rows"])`, build the ordered column list from the
  **known** channel list `analyzed = sorted(channels.keys())` and `cell_mean_channels`:
  `particle_id`, `cell_id` (only if `"cp_mask" in inputs`), `cell_<ch>_mean` for each
  `ch in cell_mean_channels`, `particle_area_px`, `donut_area_px`, then for each
  `ch in analyzed` the triple `condensed_<ch>_mean`, `dilute_<ch>_mean`,
  `<ch>_condensed_over_dilute`. Apply via `df.reindex(columns=order)` (NOT `df[order]`)
  so an empty result yields the columns with zero rows rather than crashing. Do **not**
  add `group` here — the framework prepends it.

**Technical design:** *(directional)* the reshape is a deterministic column-list build
from `analyzed` + `cell_mean_channels`; `reindex` tolerates the empty-frame and
absent-optional cases by NaN-filling, which is the desired behavior for a contract-shaped
output.

**Patterns to follow:** existing `run()` role→layer mapping and sorted-channel iteration
(lines 196–201); `per_particle_donut.py` module structure.

**Test scenarios:**
- Happy path / R1 (load-bearing): build a 2-channel + `cp_mask` `.h5`, run via
  `batch_run_analysis`, read `combined_particle_table.csv`, assert
  `list(df.columns) == ["group", "particle_id", "cell_id", "cell_CA-SiR_mean",
  "particle_area_px", "donut_area_px", "condensed_CA-SiR_mean", "dilute_CA-SiR_mean",
  "CA-SiR_condensed_over_dilute", "condensed_mNG_mean", "dilute_mNG_mean",
  "mNG_condensed_over_dilute"]` when only `channel_1_cell_mean=True` (CA-SiR selected).
  Exact ordered-list equality — not a membership check.
- Happy path / R5: leading column is `group`, values are the `.h5` stems; **no**
  `dataset` column.
- Happy path / R2: row count == particle count (not cell count).
- Edge case: no channel selected → no `cell_*_mean` columns; order otherwise exact.
- Edge case: all channels selected → `cell_CA-SiR_mean` then `cell_mNG_mean` (sorted),
  immediately after `cell_id`. Additionally assert the cell-mean block's channel infixes
  are a subsequence of the condensed/dilute block's infixes, in the same order
  (locks the single-canonical-sort decision).
- Edge case / R1: no `cp_mask` → `cell_id` and all `cell_*_mean` absent; rest exact.
- Edge case: dataset with only sub-`min_size` particles → run via `batch_run_analysis`
  (not just `run()`) and confirm `combined_particle_table.csv` is written with exactly
  the ordered columns and **zero rows**, no `KeyError`/`IndexError` (proves the `reindex`
  path AND that the writer's `len(df)` guard handles the empty frame end-to-end).
- `params={}` path: all 8 `channel_*_cell_mean` resolve to `False` (no crash, no
  cell-mean columns).
- Defensive guard / no-`cp_mask`: set `channel_1_cell_mean=True` but supply **no**
  `cp_mask` → the run **succeeds** (does not raise) and emits no `cell_*_mean` columns.
  This is the regression that locks in the no-`requires` decision.
- Negative: assert **no** `condensed_*_integ` / `dilute_*_integ` columns.
- Covers R3: a `cell_<ch>_mean` value in `particle_table` equals the corresponding
  `cell_table` value for that cell (cross-run check; restrict to `cell_id != 0`).
- R6 (framework-level, required — not optional): repeat the **exact-order** assertion
  with non-example channel names (e.g. `{"GFP", "foo"}`, selecting `foo` for cell-mean)
  to prove the module reshape isn't pattern-matching the example names. Include a channel
  literally named `cell` in one case to prove the order list is built from known channel
  names, not by parsing column strings.
- Single-dataset order (heterogeneity guard): assert the exact ordered header on a
  **one-dataset** `combined_particle_table.csv`. (`pd.concat` unions+reorders columns
  across frames with *different* column sets; the GUI passes one `layer_map` for all
  datasets so batches are homogeneous, but a single-dataset assertion pins order
  independent of concat behavior. See System-Wide Impact.)

**Verification:** Exact-order assertion passes; `group` replaces `dataset`; `*_integ`
gone; rows are per-particle; empty-result path emits columns without crashing.

---

- U4. **Dialog: per-channel "cell mean" checkbox**

**Goal:** Add a "cell mean" checkbox to each measurement-channel row, bound to the
corresponding `channel_{i}_cell_mean` param, defaulting off, gated on a `cp_mask` being
assigned, and merged into the submitted params.

**Requirements:** R4

**Dependencies:** U3 (params + `_CELL_MEAN_PARAM_KEYS` must exist)

**Files:**
- Modify: `src/percell4/gui/per_particle_multichannel_dialog.py`
- Test: `tests/test_gui/test_per_particle_multichannel_dialog.py`

**Approach:**
- In `_add_channel_row()`, add a `QCheckBox("cell mean")` (default unchecked) to the
  row, kept inside the `wrap_in_scroll`'d content. Store it alongside the row's
  combo/label so positional row index `i` maps to `channel_{i+1}_cell_mean` — the same
  positional convention `_resolve_layer_map` already uses for `channel_{i+1}`. (This
  keeps the layer→cell-mean pairing aligned: output columns are named by **layer name**,
  so what must stay consistent is that a given row's layer and its cell-mean flag travel
  together, which positional indexing guarantees.)
- Connect the checkbox's `toggled` (a user-edit signal) to `_refresh_state()` at
  construction.
- Exclude `_CELL_MEAN_PARAM_KEYS` from `_build_section_params()` so they don't render in
  the generic Parameters form.
- In `_collect_params()`, derive the cell-mean flags by **enumerating
  `self._channel_rows` at call time** (mirroring how `_resolve_layer_map` derives
  `channel_{i+1}` at call time) — NOT from a per-slot dict captured at row-add time.
  This is load-bearing: after a middle row is removed, the dialog re-enumerates rows
  contiguously, so the layer combo and its cell-mean checkbox for a given visual row
  must be read in the same pass to stay paired. Captured-at-add-time slot bindings would
  silently pair the wrong layer with the wrong flag after a removal (no crash, wrong
  CSV). Merge: `{f"channel_{i+1}_cell_mean": rows[i].checkbox.isChecked()}` for existing
  rows, `False` for absent slots `i+1..8` (so all eight declared bools are always
  present).
- **Store the checkbox as a 4th element of the row tuple**
  (`(row_widget, combo, label, cell_mean_check)`) rather than a parallel list. The tuple
  is unpacked at four sites — `_remove_channel_row` (308), `_renumber_channel_rows` (321),
  `_refresh_combos` (419), `_resolve_layer_map` (512) — **all four must be updated** to
  the 4-tuple (and the type hint at line 96). A 4-tuple keeps the checkbox bound to its
  row so removal can't desync the pairing; a parallel list can drift on removal. Audit
  all unpack sites before running tests (a missed site raises `ValueError: too many
  values to unpack`).
- Gate the checkboxes for **UX only**: when `cp_mask` is unassigned, disable them and
  force unchecked in the refresh cascade (extend `_refresh_requires_gating` /
  `_refresh_channel_controls`). Note: because the cell-mean bools carry no `requires` and
  `run()` defensively zeroes them without a `cp_mask` (U3), this gating is a convenience,
  not a correctness guard — a slip here cannot hard-fail a run.
- **Duplicate-layer guard:** the channel dict is keyed by layer name, so mapping the same
  layer to two rows silently collapses to one channel (pre-existing behavior). Add a
  dialog-level check that disables Start (with an explanatory `_start_disabled_reason`)
  when two channel rows select the same non-sentinel layer, rather than silently dropping
  a channel. Keep this minimal — it is a guardrail, not new behavior.
- Keep the dialog an **Action**: no session selection writes.

**Execution note:** Add the signal-path test first (encodes the qt-wire-user-edit-signals
learning) — drive the checkbox with `setCheckState()` and assert the submitted params
change.

**Patterns to follow:** existing `_add_channel_row` row construction (lines 279–302),
`_collect_params` (lines 522–523), `_refresh_requires_gating` (lines 434–448);
`wrap_in_scroll` usage in `_build_ui`.

**Test scenarios:**
- Happy path / R4: with `cp_mask` assigned, checking row 1's "cell mean" makes
  `_collect_params()["channel_1_cell_mean"]` `True`, others `False`.
- Signal wiring: toggling via `setCheckState(Qt.Checked)` fires `_refresh_state` and is
  reflected in collected params (guards an unwired widget).
- Edge case: no `cp_mask` assigned → checkboxes disabled and collected
  `channel_*_cell_mean` all `False`.
- Duplicate-layer guard: map the same layer to two channel rows → Start is disabled with
  the duplicate-layer reason.
- Row-removal invariant (correctness): add 3 rows; set rows to layers L1,L2,L3 and check
  the cell-mean box on the L3 row; remove the middle (L2) row; assert that the
  **(layer L3 → cell_mean True)** pairing survives in the collected `(layer_map,
  cell_mean params)` — i.e., the layer still selected for cell-mean is L3, regardless of
  which `channel_N` index it now occupies. (Do not assert a fixed `channel_3_cell_mean`
  key; positional roles renumber by design.)
- Compliance: `tests/test_gui/test_dialog_helper_compliance.py` still passes (new widget
  inside scroll content).

**Verification:** Checkboxes appear per row, default off, gate on `cp_mask`, flow into
run params; helper-compliance and signal-path tests pass.

---

- U5. **Update the framework↔CLI parity test for the new schema**

**Goal:** Make the existing framework-vs-fixture parity test pass under the new
`particle_table` schema **without** regenerating the CLI fixtures (the CLI is unchanged
and its own regression test must keep matching those fixtures).

**Requirements:** R1, R5, R7

**Dependencies:** U1, U2, U3

**Files:**
- Modify: `tests/test_application/test_per_particle_multichannel_module.py`
  - `test_columns_named_by_layer_and_parity_with_cli` (lines 81–114): the framework
    `particle_table` now (a) omits `*_integ`, (b) may include `cell_*_mean`. Update the
    comparison to **numeric parity on the shared columns only**: from the loaded
    `expected` (CLI fixture) drop the `group` column **and** all `*_integ` columns, then
    compare against the framework table's overlapping columns (reindex both to the
    sorted intersection). This keeps the numeric lock on `condensed/dilute/ratio`/areas
    while tolerating the framework's column-set change. (When the run requests no
    cell-mean, the framework table's columns are exactly `expected` minus `group` minus
    `*_integ`.)
- Verify (no change expected): `tests/test_scripts/test_per_particle_multichannel_regression.py`
  still passes (CLI unchanged) and the committed fixtures
  `tests/fixtures/per_particle_multichannel/group_*_expected/combined.csv` are **not**
  modified.
- Verify: `tests/test_domain/test_per_particle_multichannel_pure.py` lines 43–44
  (`*_integ` keys) remain and still pass — the core is unchanged for those columns;
  do **not** delete them.
- Verify: grep `tests/` for any assertion of this analysis's `version == "1.0.0"` or a
  `dataset` column on its `cell_table`/`particle_table`; update if found (none known).

**Approach:** Surgical edit to one parity test plus verification greps. The substantive
new assertions live in U2/U3/U4; this unit keeps the suite green and preserves the
numeric lock against the CLI baseline. **Test-ownership boundary:** U2 owns *all*
pure-core tests (enrichment + the unchanged `*_integ` checks); U5 owns *only* the
framework parity-test edit and any `dataset`→`group` reconciliation. Do not split R3
across both units.

**Do not regenerate fixtures.** `tests/fixtures/per_particle_multichannel/_generate_fixtures.py`
exists; running it would regenerate `group_*_expected/*.csv` from the *current* code and
recouple the CLI baseline to post-change output, defeating the regression's purpose. The
fixtures stay frozen; the fix is purely in the framework parity test's handling of
`expected`.

**Test scenarios:** *Test expectation: none — this unit edits/verifies existing tests to
match the new contract; its success criterion is the green suite. The meaningful new
assertions are enumerated under U2/U3/U4.*

**Verification:** `pytest tests/test_domain/test_per_particle_multichannel_pure.py
tests/test_application/test_per_particle_multichannel_module.py
tests/test_scripts/test_per_particle_multichannel_regression.py
tests/test_gui/test_per_particle_multichannel_dialog.py
tests/test_application/test_run_analysis_batch.py` is green; whole_field /
per_particle_donut suites remain green (R7).

---

## System-Wide Impact

- **Interaction graph:** U1 touches the shared batch writer used by *all* registered
  analyses (whole_field, per_particle_donut, per_particle_multichannel). Risk is
  contained by defaulting `dataset_column_label` to `"dataset"` — only this analysis
  opts into `"group"`. `test_dataset_column_first` (per_particle_donut) is the trip-wire.
- **Class-wide rename reaches `cell_table`:** because `dataset_column_label` is a class
  attribute, the multichannel `cell_table` (single-cell mode) also gets `group` instead
  of `dataset`. Intended and consistent; no existing test asserts `dataset` on that
  table (verified).
- **Error propagation:** the reshape uses `df.reindex(columns=order)`, which NaN-fills
  rather than raising; an empty result emits a valid zero-row table. A genuinely missing
  *expected* column (a logic bug) surfaces as an all-NaN column in the exact-order test,
  caught by U3's value assertions. The cell-mean bools carry no `requires`, so a checked
  box without a `cp_mask` no longer raises — `run()` drops cell-mean defensively.
- **Combined-CSV column union (heterogeneous batches):** `_write_csvs` does
  `pd.concat(dfs, ignore_index=True)`. If two datasets in one batch emit *different*
  column sets (e.g., one has a `cp_mask` → `cell_id`/`cell_<ch>_mean`, another does not),
  pandas unions and reorders columns, breaking the exact `combined_*.csv` order. The GUI
  always passes one `layer_map` for all datasets (`lambda _p: layer_map`), so GUI batches
  are homogeneous and safe; a heterogeneous batch is only reachable via the Python/CLI
  API. Mitigation: U3 pins exact order on a **single-dataset** combined CSV (concat-order
  independent); the homogeneity assumption is documented here rather than enforced.
- **State lifecycle risks:** none — no persisted resources change shape; CSVs are
  run-folder outputs, `.h5` writes (donut mask) unaffected.
- **API surface parity:** the per-cell `cell_table` keeps its column schema (only the id
  label renames). The CLI path is explicitly unchanged (U2 param defaults to off), so
  its committed fixtures stay valid.
- **Integration coverage:** the load-bearing proof is the **end-to-end** U3 test that
  reads `combined_particle_table.csv` and asserts the exact ordered header *after* the
  framework prepends `group` — unit-level DataFrame checks alone won't prove the writer +
  reshape + rename compose correctly.
- **Unchanged invariants:** donut/condensed/dilute detection math; particle→cell
  assignment; pure-core `*_integ` columns; CLI output + fixtures; other analyses'
  `dataset` column; the dialog's Action contract (no session writes).

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Framework↔CLI parity test breaks when `*_integ` dropped (guaranteed) | U5 updates `test_columns_named_by_layer_and_parity_with_cli` to numeric parity on shared columns; fixtures untouched, CLI regression stays green |
| Empty result → `df[order]` `KeyError` | U3 uses `df.reindex(columns=order)`; U3 adds a zero-particle test |
| Column order ships wrong (framework parity sorts columns, hiding order drift) | U3 adds explicit `list(df.columns)` equality on the combined CSV |
| `cell_<ch>_mean` diverges numerically from `cell_table` | U2 reuses the exact `_whole_cell_stats` mean; U2/U3 add a cross-table parity assertion (R3) |
| Shared-writer change breaks other analyses | `dataset_column_label` defaults to `"dataset"`; `test_dataset_column_first` + whole_field/per_particle_donut suites guard (R7) |
| `_write_csvs` reads the now-renamed id column via `.iloc[0]` | U1 threads the resolved `label` into `_write_csvs` |
| `version` bump breaks a registry/snapshot test | U5 greps for `1.0.0`/version assertions before finalizing |
| 8 cell-mean `BoolParam`s leak into the generic param form | U4 excludes `_CELL_MEAN_PARAM_KEYS` from `_build_section_params` |
| New checkbox unwired at runtime (passes via programmatic set, no-ops on click) | U4 wires `toggled` at construction + a `setCheckState()` signal-path test |
| New widget outside the scroll area breaks `cap_to_screen` | Add inside `wrap_in_scroll`; `test_dialog_helper_compliance.py` enforces |
| Same layer mapped to two slots silently collapses one channel | U4 adds a dialog Start-disabled guard on duplicate non-sentinel layer selection; `cell_mean_channels = sorted(set(...))` for the flags |
| Dialog row-removal renumbers positional roles, confusing the cell-mean mapping | Positional index keeps layer↔flag paired; U4 test asserts the layer→cell_mean pairing, not a fixed `channel_N` key |
| **`requires=("cp_mask",)` on cell-mean bools would hard-fail a batch** if a box is checked without a `cp_mask` (reachable via CLI/API) | **Cell-mean bools carry NO `requires`**; core no-ops + `run()` zeroes `cell_mean_channels` without `cp_mask`; U3 adds the no-`cp_mask`-with-box-checked succeeds-test |
| Enrichment reads raw (possibly uint) `channels` → `np.mean` rounds differently from `cell_table` | U2 computes from the float64-coerced `channel_images`, identical to `aggregate_by_cell` |
| Heterogeneous batch unions/reorders combined-CSV columns | GUI batches are homogeneous (one `layer_map`); U3 pins order on a single-dataset combined CSV; documented in System-Wide Impact |
| `_generate_fixtures.py` re-run recouples CLI baseline to post-change output | U5: fixtures frozen; generator must not be run as part of this change |
| Original `make_csv.py` (Box, unmounted) differs from the in-tree CLI proxy | Documented assumption; region/threshold math reused verbatim and out of scope |

---

## Documentation / Operational Notes

- Update the pure-core and module docstrings to mention `cell_mean_channels` and the
  `particle_table` column contract.
- If `docs/writing_an_analysis.md` documents the framework id column as `dataset`, add a
  one-line note that an analysis may override it via `dataset_column_label`.
- No migration: outputs are run-folder CSVs regenerated each run; the version bump to
  `1.1.0` records the schema change in `run_config.json`.

---

## Sources & References

- Authoritative proxy for the original script: `per_particle_multichannel.py` (repo-root
  CLI; `save_results` column assembly, lines 112–159).
- Pure core: `src/percell4/domain/analysis/_impl/per_particle_multichannel.py`.
- Module: `src/percell4/application/analysis/modules/per_particle_multichannel.py`.
- Dialog: `src/percell4/gui/per_particle_multichannel_dialog.py`.
- Shared writer: `src/percell4/application/use_cases/run_analysis_batch.py`
  (`_persist_outputs` line 205, `_write_csvs` line 227).
- Base class: `src/percell4/domain/analysis/base.py` (new `dataset_column_label`).
- Constraining tests: `tests/test_application/test_per_particle_multichannel_module.py:81–114`,
  `tests/test_scripts/test_per_particle_multichannel_regression.py`,
  `tests/test_domain/test_per_particle_multichannel_pure.py:43–44`,
  `tests/test_application/test_run_analysis_batch.py:77`.
- Fixtures (not modified): `tests/fixtures/per_particle_multichannel/group_*_expected/combined.csv`.
- Learnings: `docs/solutions/architecture-patterns/registered-analysis-framework.md`,
  `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`,
  `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`.
- Sibling/canonical patterns: `..._impl/whole_field_intensity.py`,
  `..._impl/per_particle_donut.py`, `application/analysis/modules/per_particle_donut.py`.
- Prior plans: `docs/plans/2026-05-27-004-feat-analysis-integration-plan.md`,
  `docs/plans/2026-05-28-001-feat-incorporate-whole-field-multichannel-analyses-plan.md`.
