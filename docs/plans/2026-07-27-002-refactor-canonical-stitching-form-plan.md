---
title: "refactor: Canonical FIJI-style tile-stitching form"
type: refactor
status: active
date: 2026-07-27
origin: docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md
---

# refactor: Canonical FIJI-style tile-stitching form

## Overview

The tile-stitching controls are built independently at four live sites plus one dead
one, have drifted apart, overflow their host dialogs horizontally, and present an
`Order` dropdown that mixes two incompatible vocabularies (four FIJI-style direction
pairs *and* four corner names, all eight always shown regardless of the selected
pattern).

This plan collapses them into one canonical `StitchingForm` widget with a narrow
two-column layout, relabels the grid spinboxes to `Grid size X` / `Grid size Y`, and
restructures `Type` / `Order` to match the Fiji *Grid/Collection Stitching* plugin —
including FIJI's Type-dependent Order options.

**The domain layer does not change.** Research established that `TileConfig`'s eight
`order` values are four distinct behaviors under two alias sets, and that they already
cover all sixteen FIJI Type × Order combinations exactly. This is a presentation-layer
refactor: combos gain `itemData` carriers, labels change, stored strings do not.

---

## Hard Constraint — GUI only

**This refactor changes how the stitching controls look and where they live. It changes
nothing about how stitching is performed.** Every unit below is bound by this. If any
step appears to require relaxing it, stop and raise it rather than proceeding.

Concretely, all of the following must hold when the work is done:

- **Every file modified lives under `src/percell4/gui/`** (plus `docs/` and `tests/`).
  No edits to `domain/`, `adapters/`, `application/`, `workflows/`, `store.py`, or
  `_vendor/` — not even comments.
- **For every reachable UI state, the emitted `TileConfig` is identical to what the
  same state produced before the refactor.** Same `grid_rows`, `grid_cols`, `grid_type`,
  `order`, `overlap`, `register`, `reference_channel`, `fusion_method`. The labels the
  user reads change; the values handed downstream do not.
- **No control is removed, added, disabled, greyed, or reordered in its effect.** The
  Order combo stays enabled in every state. `BatchTCSPCDialog` keeps its overlap /
  register / reference controls. No new validation, no new cross-field coupling, no new
  gating. The only behavioral addition anywhere is that the Order combo's *item list*
  swaps when Type changes — which is the requested feature (R4) and affects presentation
  only, since the previously-selected value is preserved across the swap.
- **Each dialog's existing enable/disable and defaulting semantics are preserved
  verbatim**, including the divergence where the TCSPC tab yields a 1×1 `TileConfig` on
  unchecked while Compress and Import yield `None`. Do not unify them.
- **Stitched output is byte-identical** for any given user selection, before vs. after.

Two things were cut from an earlier draft of this plan for violating the above, and are
recorded here so they do not creep back in:

1. A unit that threaded `fusion_method` through the workflow plan-dict hop
   (`phases.py` / `config_dialog.py`). It fixed a real pre-existing bug — Linear Blending
   silently reverting to `none` — but fixing it *changes stitching behavior* and touches
   non-GUI files. **Out of scope.** Track separately if wanted.
2. Greying the `Order` combo while `Register` is checked, and dropping
   `BatchTCSPCDialog`'s registration controls. Both alter what the user can do.
   **Out of scope.**

---

## Problem Frame

Three concrete defects, all visible in the `Compress TIFF Dataset` dialog:

1. **Width.** `src/percell4/gui/compress_dialog.py:244` lays the entire stitching
   control set out as a single `QHBoxLayout` holding eight widgets
   (`Rows`, `Cols`, `Pattern`, `Start`, `Overlap`, `Register`, `Reference`, `Fusion`).
   Despite `setMinimumWidth(750)` and `resize(800, 700)` the row still overflows, and
   the dialog grows a horizontal scrollbar — the user must scroll sideways to reach
   the `Register` / `Reference` / `Fusion` controls.

2. **Incoherent Order vocabulary.** The `Start:` combo offers
   `right_down, right_up, left_down, left_up, top_left, top_right, bottom_left, bottom_right`.
   The first four are row-centric direction pairs; the last four are corner names. They
   are *aliases of the same four behaviors*, so the list presents eight options that mean
   four things, with no indication of which are meaningful for the selected `Pattern`.
   Under a column pattern, `right_up` reads as nonsense.

3. **Drift across surfaces.** Four live construction sites plus a dead one, each
   slightly different. `compress_dialog.py` alone has a `Fusion` combo; the two
   `add_layer_dialog.py` tabs have no registration controls at all;
   `src/percell4/gui/import_dialog.py:118` offers a four-corner Order list where the
   others offer all eight aliases. That last one is a *vocabulary* difference rather than
   a capability gap — the four corners cover all four distinct behaviors — but it means
   no two surfaces present the same choices, which is the drift class
   `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
   documents from PR #9. Any fix to the widget set has to be applied four times.

The origin brainstorm deferred this explicitly: *"Migrating the four duplicated
stitch-control surfaces onto `StitchingFlimForm` — separate refactor."*
(see origin: `docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md`,
Scope Boundaries). The tracked follow-up
`docs/archive/todos/037-pending-p2-migrate-add-layer-tcspc-to-stitching-flim-form.md`
was never actioned. This plan is that refactor.

---

## Requirements Trace

- R1. The stitching controls fit within a single standard window width. No host dialog
  requires horizontal scrolling to reach any stitching control.
- R2. `Rows` / `Cols` are relabeled `Grid size X` / `Grid size Y`, where
  **X → `TileConfig.grid_cols`** and **Y → `TileConfig.grid_rows`**.
- R3. The `Type` combo uses FIJI's labels: `Grid: row-by-row`, `Grid: column-by-column`,
  `Grid: snake-by-row`, `Grid: snake-by-column`.
- R4. The `Order` combo's options depend on the selected `Type`:
  row and snake-by-row types offer `Right & Down`, `Left & Down`, `Right & Up`,
  `Left & Up`; column and snake-by-column types offer `Down & Right`, `Down & Left`,
  `Up & Right`, `Up & Left`.
- R5. One canonical stitching component is used by every GUI surface that stitches
  tiles. No surface constructs its own grid/type/order widgets.
- R6. Persisted state stays compatible: `TileConfig`'s accepted `grid_type` / `order`
  string vocabulary is unchanged, existing `.h5` files whose `/metadata` carries
  `stitch_grid_type` / `stitch_order` still seed the UI correctly, and existing
  `run_config.json` plan files still replay.
- R7. The canonical component owns grid size + Type + Order, the overlap /
  register / reference-channel controls, and the fusion-mode combo. The raw FLIM
  `.bin` geometry fields are split into a separate widget.
- R8. The user-specified labels (`Grid size X`, `Grid size Y`, `Type`, `Order`, and
  FIJI's eight Order strings) are fixed. Any residual ambiguity is resolved in
  tooltips, never by changing a label.

**Origin actors:** A1 (Researcher, import), A2 (Import/compress pipeline), A3 (Decay
append flow)
**Origin flows:** F1 (register-once at import), F2 (decay-only append reusing geometry)
**Origin acceptance examples:** AE3 (0%-overlap / single-tile byte-identical passthrough)
— carried as a non-regression constraint only; this plan writes no new stitch geometry.

---

## Scope Boundaries

- No change to `src/percell4/domain/io/assembler.py` placement math, `_tile_positions`,
  or the registered-stitch path. This is presentation-only.
- No change to `TileConfig`'s field names, accepted string values, or its documented
  "dumb carrier" contract (cross-field validation stays at the importer gate).
- No new `order` values, no HDF5 migration, no `run_config.json` schema change.
- No FIJI-style traversal preview diagram (explicitly declined — labels plus tooltips
  only).
- No change to rotation/flip controls, per-channel calibration UI, `flim_freq`, or
  `AddLayerDialog`'s scan / matching / replace-state machine.
- No change to how stitching is performed, at all — see **Hard Constraint — GUI only**.
  No file outside `src/percell4/gui/` is modified. No control is removed, disabled, or
  gated. No `TileConfig` value changes for any UI state.
- No fix to the `fusion_method` workflow-hop gap, despite it being a genuine bug found
  during research: fixing it changes stitching behavior.

### Deferred to Follow-Up Work

- **Honor `order` on the registered/overlap path.** Both `estimate_tile_offsets`
  (`src/percell4/domain/io/assembler.py:258-480`) **and** the fallback
  `grid_seed_offsets` (lines 208-255) delegate seeding to the vendored `grid_positions`,
  whose `_ORDERS` table
  (`src/percell4/domain/_vendor/grid_stitching/tiles.py:82-87`) is always top-left
  seeded; `order` is passed to `_tile_positions` only to validate (lines 237, 313 — the
  call sites carry a bare `# validate order` comment). So with `Register` on, every Order
  behaves as `Right & Down`, on both branches. This plan surfaces the
  limitation in a tooltip (U4) but does not fix it — fixing it means touching a T1
  canonical file guarded by byte-identical regression tests, and belongs in its own
  change with its own verification.
- **Retiring `src/percell4/gui/import_dialog.py`.** It is not instantiated anywhere in
  `src/` (only `tests/test_gui/test_stitching_flim_form_registration.py` and
  `tests/test_gui/test_dialog_migrations.py` construct it). U7 migrates it so drift
  dies; deciding whether to delete it outright is a separate call.

---

## Context & Research

### Relevant Code and Patterns

**The four live construction sites plus one dead one:**

| Site | File / lines | Widgets | Missing vs. union |
|---|---|---|---|
| Shared form | `src/percell4/gui/_stitching_flim_form.py:66-94` | rows, cols, type, order (8 items), overlap, register, reference | fusion |
| Compress | `src/percell4/gui/compress_dialog.py:238-308` | full set **incl. fusion** (`_stitch_fusion`, line 297) | — (richest) |
| AddLayer batch tab | `src/percell4/gui/add_layer_dialog.py:278-313` | rows, cols, type, order | overlap, register, reference, fusion |
| AddLayer TCSPC tab | `src/percell4/gui/add_layer_dialog.py:943-995` | rows, cols, type, order | overlap, register, reference, fusion |
| Import (dead) | `src/percell4/gui/import_dialog.py:95-149` | rows, cols, type, **order with only 4 items**, overlap, register, reference | fusion; order list drifted |

**Consumers of the shared form:** `src/percell4/gui/batch_tcspc_dialog.py:312-325`
(constructs), `865, 869, 874-875` (reads).

**Combo `itemData` carrier convention.** Values feeding a domain dataclass ride in
`itemData`, never index or display text — established by `rotation_combo` / `flip_combo`
(`_stitching_flim_form.py:126-138`), `_stitch_fusion` (`compress_dialog.py:297-299`),
and `set_reference_channels` (`_stitching_flim_form.py:234-253`, whose docstring names
this "the PR #9 drift precedent"). `stitch_type` / `stitch_order` are the two combos
that violate it today — they are read by `currentText()` at
`_stitching_flim_form.py:265-266`. That violation is precisely what blocks relabeling.

**`QFormLayout` is the narrow-form idiom.** In-repo precedent:
`import_dialog.py:100` (already a `QFormLayout` for exactly this control set),
the FLIM `.bin` group at `_stitching_flim_form.py:163`, and `compress_dialog.py:373-375`.

**Dialog sizing.** `compress_dialog.py:62-63` (750/800×700),
`add_layer_dialog.py:72-73` (700/800×700), `batch_tcspc_dialog.py:96-97` (820/900×760),
`import_dialog.py:39-40` (500/500×500).

**GUI classification.** Every stitching control is an `Action` under
root `CLAUDE.md` → "GUI state ownership" — pre-run configuration operands that neither
read nor write the five session selection fields. Already recorded in
`docs/audits/gui-element-classification.yaml` (compress at 2207-2260, add_layer batch at
1581-1634, add_layer TCSPC at 1681-1734, import at 2052-2061, batch_tcspc at 1906-1915).
**Consolidation does not change any classification** — only the `path` / `lines` fields,
which are already stale (e.g. `compress.stitch_type_combo` records `233-237`; actual is
257-261). The audit is also *incomplete*: compress's overlap / register / reference /
fusion controls and import's rows / cols / type / order have no entries at all, so U8
adds as well as collapses.

### Institutional Learnings

- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  — the governing precedent. Names `StitchingFlimForm` as the canonical widget set
  ("Consume it; do not reimplement") and catalogs the four PR #9 drift bugs — rebuilt
  item lists, reads by `currentIndex()` rather than `itemData`, sub-rules dropped on
  rebuild, and wrong default selections. The `itemData` class is live right now in the
  `grid_type` / `order` combos at every site. Its Rule 5 (defer the original-dialog
  migration to a follow-up) fired once and produced the debt this plan pays.
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — governs the new
  Type→Order repopulation wiring. The bug class is silent: tests that drive
  `setCurrentIndex()` programmatically pass while the user-driven signal path no-ops.
  `compress_dialog.py` was fixed twice in eight days for this class.
- `docs/solutions/architecture-patterns/overlap-aware-stitching.md` (`canonical_clean`) —
  `TileConfig` is a deliberate dumb carrier; the Type→Order coupling must **not** move
  into `__post_init__`. Also: the byte-identical gate is
  `register ∧ overlap>0 ∧ grid_rows·grid_cols>1`, so the X/Y rename must not swap which
  field maps to `grid_rows` vs `grid_cols` — a swap silently transposes the mosaic and,
  on the registered path, changes `native_shape`.
- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` (`canonical_clean`) — `wrap_in_scroll`
  / `cap_to_screen` from `src/percell4/gui/_dialog_utils.py`. Relevant as a *trade*:
  narrowing the layout makes it taller. `tests/test_gui/test_dialog_helper_compliance.py`
  AST-walks `gui/**/*_dialog.py` and `*Dialog.py`; a standalone `_stitching_form.py`
  widget is outside that glob, so the consuming dialogs remain responsible for height.
- `docs/solutions/conventions/gui-panel-and-batch-workflow-method-parity-2026-07-13.md` —
  a relabeled dropdown routing to a differently-named implementation once produced a
  user-visible ~10× discrepancy. The label may change; the value handed to `TileConfig`
  must remain the canonical enum string.

### External References

Primary-source confirmation of the FIJI semantics, from the canonical upstream
`fiji/Stitching` repo (Preibisch et al.):

- [`GridType.java`](https://github.com/fiji/Stitching/blob/master/src/main/java/plugin/GridType.java)
  — the `choose2` array *is* the Type→Order dependency:
  `choose2[0]` and `choose2[2]` (row-by-row, snake by rows) =
  `{"Right & Down", "Left & Down", "Right & Up", "Left & Up"}`;
  `choose2[1]` and `choose2[3]` (column-by-column, snake by columns) =
  `{"Down & Right", "Down & Left", "Up & Right", "Up & Left"}`.
- [`Stitching_Grid.java`](https://github.com/fiji/Stitching/blob/master/src/main/java/plugin/Stitching_Grid.java)
  — `getPosition()` confirms the corner mapping. The `i == 0` init block uses identical
  `gridOrder` arithmetic for *both* row and column types, so order index 0/1/2/3 →
  top-left / top-right / bottom-left / bottom-right universally. For row types the first
  word is travel direction within a row and the second is how rows step; for column types
  the first word is travel within a column and the second is how columns step. Snake
  variants flip only the within-axis direction per boundary crossing; the stepping
  direction (second word) never flips.

---

## Key Technical Decisions

- **Store the corner-named aliases (`top_left` / `top_right` / `bottom_left` /
  `bottom_right`) as the `itemData` for every Order item, under both row and column
  Types.** The domain already accepts them (`models.py:87`), they map 1:1 onto the
  `(start_bottom, start_right)` flags `_tile_positions` actually uses, and using one
  vocabulary for both Types means restore-from-persisted-state is a single
  `findData(value)` with no Type-conditional alias resolution. The row-centric
  `right_down`-style names remain accepted by the domain as legacy input.

- **Normalize legacy `order` values on read, not on write.** A `.h5` or
  `run_config.json` carrying `right_up` must select the `Right & Up` item. A small pure
  helper maps all eight accepted strings onto the four canonical corners before
  `findData`. Nothing rewrites stored values.

- **Land `itemData` carriers before changing any label** (U2 before U4). Relabeling while
  reads still go through `currentText()` would make `TileConfig.__post_init__` raise on
  every stitch config, and would make `add_layer_dialog.py:1254-1263`'s `findText` seeding
  silently no-op. Splitting these into two commits means each is independently revertible.

- **Keep the Type→Order coupling in the widget, not in `TileConfig`.** The dumb-carrier
  decision is documented and load-bearing; every existing `TileConfig(...)` construction
  in tests and workflows must keep working with any (type, order) pair.

- **Capability flags over subclassing** for per-surface variation. `StitchingForm` takes
  `show_registration` and `show_fusion` constructor flags. Per origin R13, registration
  controls belong on Import and Compress only; append surfaces get a read-only "reusing
  persisted geometry" affordance. Flags keep one construction site.

- **Split the FLIM `.bin` geometry into its own widget.** The current
  `StitchingFlimForm` conflates "how tiles are arranged" with "how to parse a raw binary
  histogram". They have different audiences (all stitching surfaces vs TCSPC only). After
  the split, `StitchingForm` is purely about stitching and can be embedded anywhere
  without dragging in irrelevant fields.

- **`QGridLayout` inside a `QGroupBox`** for the narrow layout — **not `QFormLayout`**.
  The target arrangement pairs two label/field couples per row (`Grid size X` next to
  `Type`), which is four columns. `QFormLayout` is strictly two (label + field), so it can
  only express this by nesting a `QHBoxLayout` in the field column. The nearest precedent,
  `import_dialog.py:100-145`, is one pair per row — coherent and narrow, but it yields
  ~8 rows and trades the width problem for a height one. `QGridLayout` gets the width
  reduction without the row explosion, and keeps column alignment across rows, which a
  nested-box approach loses. Grid size X/Y and Type/Order pair up on facing rows.

---

## Open Questions

### Resolved During Planning

- *Does the FIJI Order restructure require new domain values?* No. The eight existing
  `order` values are four behaviors under two alias sets, and they cover all sixteen
  FIJI Type × Order combinations exactly. Confirmed against `_tile_positions`
  (`assembler.py:92-104`) and against FIJI's own `getPosition()`.
- *Does `Grid size X` map to rows or cols?* Cols. `assemble_tiles` computes
  `out_h = grid_rows * tile_h` / `out_w = grid_cols * tile_w` (`assembler.py:61-63`), and
  the vendored boundary passes `grid_size_x=grid_cols, grid_size_y=grid_rows`
  (`assembler.py:238-245`). Matches FIJI's own `grid_size_x` / `grid_size_y` naming.
- *Which persistence surfaces constrain the string vocabulary?* Two.
  `/metadata` attrs `stitch_grid_type` / `stitch_order` written at
  `src/percell4/adapters/importer.py:925-929` (present in every existing `.h5`), and
  `run_config.json` written at
  `src/percell4/gui/workflows/single_cell/config_dialog.py:266-278` and read with lenient
  defaults at `src/percell4/workflows/phases.py:131-148`. No CLI and no QSettings exposure.
- *Should the component include a traversal preview diagram?* No — declined; labels and
  tooltips only.
- *How many surfaces need migrating?* Four live (compress, add_layer batch, add_layer
  TCSPC, batch_tcspc) plus one dead (import_dialog). Verified: `StitchingFlimForm` has
  exactly one production consumer (`batch_tcspc_dialog.py:322`), and `ImportDialog` is
  constructed only from `tests/test_gui/test_stitching_flim_form_registration.py` and
  `tests/test_gui/test_dialog_migrations.py`.
- *Is `fusion_method` threaded through the workflow hops like the other `TileConfig`
  fields?* **No** — `phases.py:137-148` omits it, so `Linear Blending` set in the nested
  `CompressDialog` silently reverts to `"none"` on replay. A pre-existing defect, not
  caused by this refactor. **Out of scope** — fixing it would change stitching behavior
  and touch non-GUI files. Recorded here only so the knowledge is not lost.

### Deferred to Implementation

- **Exact minimum width achievable for `CompressDialog`.** The stitching group is one of
  several sections; the binding constraint may be the dataset list or the token-pattern
  group, not stitching. Measure after U4 and lower `setMinimumWidth(750)` to whatever the
  widest remaining section actually needs.
- **Whether the thin-property shims survive.** `todos/037` acceptance criterion 2 allows
  either thin properties onto the shared widgets or migrating every call site. Which is
  cheaper depends on how many of `add_layer_dialog.py`'s ~2100 lines actually touch
  `_tcspc_stitch_*`. Decide per-tab during U5.
- **Whether the height regression from narrowing trips any dialog's scroll behavior.**
  Depends on final row count; verify against `cap_to_screen` at the smallest supported
  screen size.
- ~~How prominently to surface that `Order` is inert under `Register`.~~ **Resolved: do
  nothing beyond a tooltip.** An earlier draft recommended greying the `Order` combo
  while `Register` is checked. Rejected — `Order` stays enabled and selectable in every
  state, exactly as it is today. The inertness is a pre-existing property of the
  registered seeding path, documented under Deferred to Follow-Up Work; this refactor
  neither introduces it nor changes how any control behaves. A tooltip is the ceiling.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context, not
> code to reproduce.*

### The Type → Order → stored-value matrix

This is the load-bearing table. Labels come from FIJI; `itemData` is the canonical
corner alias the domain already accepts; the flag pair is what `_tile_positions`
actually consumes.

| Selected Type | Order label shown | `itemData` stored | `(start_bottom, start_right)` | Tile 0 corner |
|---|---|---|---|---|
| `Grid: row-by-row`, `Grid: snake-by-row` | `Right & Down` | `top_left` | (F, F) | top-left |
| | `Left & Down` | `top_right` | (F, T) | top-right |
| | `Right & Up` | `bottom_left` | (T, F) | bottom-left |
| | `Left & Up` | `bottom_right` | (T, T) | bottom-right |
| `Grid: column-by-column`, `Grid: snake-by-column` | `Down & Right` | `top_left` | (F, F) | top-left |
| | `Down & Left` | `top_right` | (F, T) | top-right |
| | `Up & Right` | `bottom_left` | (T, F) | bottom-left |
| | `Up & Left` | `bottom_right` | (T, T) | bottom-right |

The corner column is Type-independent — which is exactly why one `itemData` vocabulary
serves both Types, and why switching Type can preserve the user's corner choice rather
than resetting it.

**This table was verified empirically, not derived on paper.** Running `_tile_positions`
directly confirms (a) `right_down ≡ top_left`, `left_down ≡ top_right`,
`right_up ≡ bottom_left`, `left_up ≡ bottom_right` produce identical position maps across
all four `grid_type` values, and (b) the tile-0 corners and travel directions match
FIJI's `getPosition()`. On a 3×4 grid:

```
row-by-row   Right & Down -> tile0=(0,0) tile1=(0,1)    # travel right, rows step down
row-by-row   Left  & Down -> tile0=(0,3) tile1=(0,2)    # travel left,  rows step down
row-by-row   Right & Up   -> tile0=(2,0) tile1=(2,1)    # travel right, rows step up
col-by-col   Down  & Right-> tile0=(0,0) tile1=(1,0)    # travel down,  cols step right
col-by-col   Down  & Left -> tile0=(0,3) tile1=(1,3)    # travel down,  cols step left
col-by-col   Up    & Right-> tile0=(2,0) tile1=(1,0)    # travel up,    cols step right

row_by_row   Right & Down -> (0,0)(0,1)(0,2)(0,3) (1,0)(1,1)(1,2)(1,3)
snake_by_row Right & Down -> (0,0)(0,1)(0,2)(0,3) (1,3)(1,2)(1,1)(1,0)   # serpentine
```

Snake reversal alternates the within-row direction while the row-stepping direction stays
fixed — matching FIJI, where `snakeDirectionX` flips per boundary and `snakeDirectionY`
never does.

### Legacy value normalization (read path)

```
stored string  ──▶  normalize_order(s)  ──▶  canonical corner  ──▶  combo.findData(...)

  right_down │ top_left      ──▶  top_left
  left_down  │ top_right     ──▶  top_right
  right_up   │ bottom_left   ──▶  bottom_left
  left_up    │ bottom_right  ──▶  bottom_right
```

### Layout: one wide row becomes a two-column form

```
BEFORE (compress_dialog.py:244 — one QHBoxLayout, overflows past 800px)
┌────────────────────────────────────────────────────────────────────────────────────▶ scroll
│ Rows:[1] Cols:[1] Pattern:[row_by_row ▾] Start:[right_down ▾] Overlap:[0.00%] ☐ Register…  Reference:[ch00 ▾] Fusion:[None ▾]
└────────────────────────────────────────────────────────────────────────────────────▶

AFTER (StitchingForm — QFormLayout pairs inside a QGroupBox)
┌─ Tile Stitching ──────────────────────────────────┐
│ Grid size X: [ 1 ]      Type:  [Grid: row-by-row ▾]│
│ Grid size Y: [ 1 ]      Order: [Right & Down     ▾]│
│ Overlap:     [0.00%]    ☐ Register overlapping tiles│
│ Reference:   [ch00  ▾]  Fusion: [None            ▾]│
└────────────────────────────────────────────────────┘
        (rows 3-4 hidden when show_registration/show_fusion are False)
```

### Type-change repopulation — the three guards

```
on Type changed:
    remember  = order_combo.currentData()        # a canonical corner
    blockSignals(True)
      clear();  addItem(label, corner) x4        # label set keyed by Type
      idx = findData(remember)                   # corner is Type-independent → always hits
      setCurrentIndex(idx if idx >= 0 else 0)
    blockSignals(False)
    if effective value changed:  emit changed()  # exactly once, never zero
```

---

## Implementation Units

- U1. **Split the non-stitching halves out of `StitchingFlimForm`**

**Goal:** Extract *both* non-stitching control groups — the raw-binary geometry fields
**and** the rotate/flip pair — into their own widgets, so the stitching component can be
purely about stitching and nothing is orphaned when `StitchingFlimForm` is deleted in U6.
Behavior-preserving — no label, layout, or value changes.

**Requirements:** R7

**Dependencies:** None

**Files:**
- Create: `src/percell4/gui/_flim_bin_form.py` (`FlimBinParamsForm`, `RotateFlipForm`)
- Modify: `src/percell4/gui/_stitching_flim_form.py`
- Test: `tests/test_gui/test_flim_bin_form.py`

**Approach:**
- Move the checkable `QGroupBox("FLIM .bin Parameters")` and its six fields
  (`_stitching_flim_form.py:155-194`) into `FlimBinParamsForm`, carrying the
  `flim_config(frequency_mhz=...)` accessor (lines 283-305) verbatim, including the
  unchecked → `FlimConfig(frequency_mhz=...)` defaults branch.
- **Also move the rotate/flip row (`_stitching_flim_form.py:122-140`) into
  `RotateFlipForm`, carrying `rotation_k()` and `flip_axis()` (lines 273-281).** These
  are decay-orientation concerns, not stitching-layout concerns — they apply to the
  already-stitched array. Without this, U6 deletes the only widget that owns them while
  `batch_tcspc_dialog.py:874-875` still calls `rotation_k()` / `flip_axis()`, and
  `AddLayerDialog` has only its own private copies (`add_layer_dialog.py:1016-1029`), so
  there is no fallback home.
- Keep `StitchingFlimForm` as a composite that embeds both new widgets and re-exposes
  `flim_config` / `rotation_k` / `flip_axis` / `bin_x` / `bin_dtype` / etc. so
  `batch_tcspc_dialog.py:865-875` and the existing tests keep working untouched.
- Preserve the `bin_dtype` item order (`uint32` first) and keep rotate/flip reading via
  `currentData()` — both are documented PR #9 drift bugs.
- Re-emit both extracted widgets' `changed` through the composite's `changed`.

**Execution note:** Pure refactor — run the existing
`tests/test_gui/test_batch_tcspc_dialog.py` before and after and require zero diff in
outcomes.

**Patterns to follow:**
- `src/percell4/gui/_cellpose_settings_form.py` — the repo's other extracted settings form.
- `_stitching_flim_form.py:196-232` — arg-discarding lambdas on 0-arg `changed`.

**Test scenarios:**
- Happy path: `FlimBinParamsForm` unchecked → `flim_config(frequency_mhz=80.0)` returns
  `FlimConfig(frequency_mhz=80.0)` with defaulted geometry fields.
- Happy path: checked, fields set to `(256, 256, 64, "float32", "XYT", 128)` →
  `flim_config` round-trips all six.
- Edge case: `bin_header` at 0 shows the `Auto-detect` special value text.
- Edge case: `bin_dtype` item 0 is `uint32`, not `uint16`.
- Happy path: `RotateFlipForm.rotation_k()` returns 0/1/2/3 for None/90° CCW/180°/90° CW,
  and `flip_axis()` returns `None`/0/1 for None/Vertical/Horizontal — read via
  `currentData()`, never `currentIndex()`.
- Integration: editing any field on *either* extracted widget emits `changed` on the
  composite `StitchingFlimForm` exactly once.

**Verification:** `BatchTCSPCDialog` behaves identically; no test outcome changes. Every
accessor `batch_tcspc_dialog.py:865-875` calls still resolves through the composite.

---

- U2. **Convert `grid_type` / `order` to `itemData` carriers (labels unchanged)**

**Goal:** Make every Type/Order combo carry its canonical string in `itemData` and every
read use `currentData()`, and migrate persisted-state restore from `findText` to
`findData`. Display text stays the raw strings — this commit changes no visible label.

**Requirements:** R6

**Dependencies:** None

**Files:**
- Create: `src/percell4/gui/_stitch_order.py` (label/value tables + `normalize_order`)
- Modify: `src/percell4/gui/_stitching_flim_form.py`, `src/percell4/gui/compress_dialog.py`,
  `src/percell4/gui/add_layer_dialog.py`, `src/percell4/gui/import_dialog.py`
- Test: `tests/test_gui/test_stitch_order.py` (create),
  `tests/test_gui/test_batch_tcspc_dialog.py` (rewrite the pinning test),
  `tests/test_gui/test_add_layer_stitch_metadata_seed.py` (**create — does not exist**)

**Approach:**
- Add a pure module with the four canonical corners, the two Type-keyed label tables from
  the design matrix, and `normalize_order(value) -> corner` handling all eight accepted
  strings. Keep it Qt-free so it is unit-testable without a widget.
- At every site, switch `addItems([...])` to `addItem(label, value)` and every
  `currentText()` read to `currentData()`. At this stage `label == value`, so nothing
  visibly changes.
- Migrate `add_layer_dialog.py:1254-1263` from `findText(...)` to
  `findData(normalize_order(...))` for `stitch_order`, and `findData(...)` for
  `stitch_type`. **This is the most fragile spot in the refactor** — today a miss is
  silent (`idx < 0` → no `setCurrentIndex`), so the dialog quietly falls back to a
  default rather than the dataset's persisted geometry, which misplaces decay tiles
  relative to intensity.
- **Leave `import_dialog.py:118-122`'s four-item Order list alone.** It offers
  `top_left, bottom_left, top_right, bottom_right` — the four *corners*, which by the
  alias equivalence above is all four distinct behaviors, missing none. It is the one
  surface already using a single coherent vocabulary, and that vocabulary is exactly the
  one U3 standardizes on. It is **not** a recurrence of PR #9's "Origin combo had 4 items,
  not 8" (that bug dropped real orientations; this drops only aliases), and "fixing" it to
  eight would be immediately undone by U7.
- **Touch no file outside `src/percell4/gui/`.** `models.py` carries a stale comment at
  line 62 (it lists four orders; the validator accepts eight). Leave it — correcting a
  comment in a T1 domain file is not worth putting this refactor's blast radius outside
  the GUI package.

**Execution note:** Land this as its own commit before any relabeling. It is
behavior-preserving and independently revertible; U4 depends on it being correct.

**Cost warning — the mitigation is not free.** `_tcspc_seed_stitching_from_metadata`
(`add_layer_dialog.py:1216`) has **zero test coverage today**
(`grep -rn "_tcspc_seed_stitching_from_metadata" tests/` returns nothing). The plan calls
the `findText`→`findData` change there the sharpest edge in the refactor and mitigates it
with tests — all of which must be written from scratch, including a store double whose
`.metadata` carries `stitch_grid_rows` / `stitch_grid_cols` / `stitch_grid_type` /
`stitch_order`, plus a way to invoke the TCSPC tab's seeding path. Budget for building
that harness, not just for adding assertions.

**Patterns to follow:**
- `_stitching_flim_form.py:126-138` (`rotation_combo` / `flip_combo` `itemData` carriers).
- `compress_dialog.py:297-299` (`_stitch_fusion` `itemData`).

**Test scenarios:**
- Happy path: `normalize_order` maps all eight accepted strings onto the correct one of
  four corners; `right_down`→`top_left`, `left_down`→`top_right`,
  `right_up`→`bottom_left`, `left_up`→`bottom_right`, and each corner to itself.
- Error path: `normalize_order("sideways")` raises rather than returning a default.
- Happy path: every Type/Order combo's `itemData` list equals the canonical vocabulary
  that `TileConfig.__post_init__` accepts (assert by constructing a `TileConfig` from
  each item — catches vocabulary drift at its source).
- Integration: seeding `AddLayerDialog`'s TCSPC tab from `/metadata` carrying the legacy
  `stitch_order="right_up"` selects the `bottom_left` item, not the default. Assert the
  resulting `TileConfig.order`, not the combo index.
- Integration: seeding from `/metadata` carrying an unrecognized `stitch_order` leaves
  the default selected and does not raise.
- Edge case: every surface's Order combo — including `import_dialog`'s four-corner list —
  can express all four distinct behaviors. Assert on the set of
  `(start_bottom, start_right)` outcomes reachable, not on item count, so an
  alias-vocabulary difference does not read as a defect.
- Integration: `tile_config()` on every surface returns the same `TileConfig` as before
  this unit for each of the sixteen (type, corner) pairs.

**Verification:** No visible UI change. `TileConfig` construction and metadata seeding
both round-trip every accepted string.

---

- U3. **Canonical `StitchingForm` widget**

**Goal:** Build the single narrow stitching component: FIJI labels, `Grid size X/Y`,
Type-dependent Order options, capability flags, union of all five sites' features.

**Requirements:** R1, R2, R3, R4, R5, R7, R8

**Dependencies:** U1, U2

**Files:**
- Create: `src/percell4/gui/_stitching_form.py`
- Modify: `src/percell4/gui/_stitch_order.py` (FIJI label tables)
- Test: `tests/test_gui/test_stitching_form.py`

**Approach:**
- `StitchingForm(QWidget, *, show_registration: bool = True, show_fusion: bool = False)`
  in a `QGroupBox("Tile Stitching")` using a **`QGridLayout`** (four columns: label,
  field, label, field) per the design sketch. Registration and fusion rows are constructed
  but hidden when their flag is False, so `tile_config()` has one code path.
- `tile_config()` returns `None` when stitching is disabled, and the caller decides
  whether to substitute a 1×1 `TileConfig` — see U5's note on the two divergent disable
  semantics currently in the codebase. Do **not** flatten them here.
- `Grid size X` → `grid_cols`, `Grid size Y` → `grid_rows`. Label the spinboxes exactly
  as specified; put the row/column correspondence in a tooltip (R8 — resolve ambiguity in
  tooltips, never the label).
- Type combo: FIJI labels, `itemData` = existing `row_by_row` / `column_by_column` /
  `snake_by_row` / `snake_by_column`.
- Order combo: repopulated on Type change per the three-guard sketch — block signals,
  swap the label set, restore the previous corner via `findData`, unblock, then emit
  `changed` exactly once if the effective value changed. Because `itemData` is the
  Type-independent corner, the user's choice always survives a Type switch.
- Carry `set_reference_channels` forward from `_stitching_flim_form.py:234-253` (editable
  combo, `findText` is correct *there* — names are the data; do not over-apply the
  `findData` change), but give it an explicit
  **`preserve: Literal["text", "index"] = "text"`** parameter. Two surfaces need opposite
  semantics on the same widget: the shared form preserves the pick **by text**, while
  `CompressDialog._refresh_reference_combo` (`compress_dialog.py:889-897`) deliberately
  preserves **by index**, because a Manual-mode rename `ch00 → "ER"` must keep the same
  channel *position*. Without the parameter, `StitchingForm` ships one behavior while
  `CompressDialog` externally drives the combo with the other. `StitchingForm` owns the
  API; U4 passes `preserve="index"`.
- Tooltip on Order noting it is ignored when `Register` is checked (see Deferred to
  Follow-Up Work).
- `tile_config()` returns a `TileConfig` including `fusion_method` when the fusion row is
  enabled, matching `compress_dialog.py:475-488`.

**Execution note:** Write the Type→Order repopulation test first. Per
`docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`, this bug class is
silent under programmatic setters — the test must drive `setCurrentIndex` on the *Type*
combo and assert the *Order* combo's contents changed.

**Patterns to follow:**
- `src/percell4/gui/import_dialog.py:99-149` — `QFormLayout` inside a
  `QGroupBox("Tile Stitching")`, the nearest existing narrow layout for this control set.
- `_stitching_flim_form.py:196-232` — `changed` wiring with arg-discarding lambdas.

**Test scenarios:**
- Happy path: default state yields `TileConfig(grid_rows=1, grid_cols=1,
  grid_type="row_by_row", order="top_left")`.
- Happy path: `Grid size X = 3`, `Grid size Y = 2` → `grid_cols == 3` **and
  `grid_rows == 2`**. Deliberately non-square — a transposition is invisible on the
  square grids most existing tests use, and on the registered path it changes
  `native_shape`.
- Happy path: selecting each of the four row-type Order labels produces the corner from
  the design matrix; same for the four column-type labels.
- Edge case: switching Type from `Grid: row-by-row` to `Grid: column-by-column` while
  `Left & Up` is selected leaves the stored value `bottom_right` and shows `Up & Left`.
- Edge case: switching Type emits `changed` exactly once when the effective value
  changes, and does not emit when it does not.
- Edge case: with `show_registration=False`, the overlap/register/reference widgets are
  hidden and `tile_config()` returns `overlap=0.0, register=False,
  reference_channel=None` (the gate stays closed).
- Edge case: with `show_fusion=False`, `tile_config()` omits `fusion_method` / leaves it
  at its default.
- Error path: no combination of Type and Order selections can produce a `TileConfig` that
  raises in `__post_init__` — assert by iterating all 4×4 UI combinations.
- Integration: every user-visible control emits `changed` when driven through its signal
  path (`setCurrentIndex`, `setValue`, `setChecked`), not via a bare setter.
- Happy path (R1): the widget's `sizeHint().width()` fits within a stated budget
  (~560px) so it cannot silently regress to a wide row.

**Verification:** The widget renders in one standard window width, Order options track
Type per FIJI, and every UI combination maps to a valid `TileConfig`.

---

- U4. **Migrate `CompressDialog` onto `StitchingForm`**

**Goal:** Replace the richest duplicate — the dialog in the user's screenshots — and
eliminate its horizontal scrollbar.

**Requirements:** R1, R5, R7

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/gui/compress_dialog.py`
- Test: `tests/test_gui/test_compress_dialog_stitch_registration.py`,
  `tests/test_gui/test_compress_dialog_checkbox_signal.py`

**Approach:**
- Delete the construction block at lines 238-308; instantiate
  `StitchingForm(show_registration=True, show_fusion=True)`.
- Keep `_stitch_check` toggling the form's visibility (the existing idiom at lines
  239-241).
- Expose `_stitch_rows` / `_stitch_cols` / `_stitch_type` / `_stitch_order` /
  `_stitch_overlap` / `_stitch_register` / `_stitch_reference` / `_stitch_fusion` as thin
  properties onto the form's widgets so the existing tests and the reference-combo
  repopulation at line 889 keep working.
- Replace the `TileConfig` build at lines 475-488 with a call to `form.tile_config()`.
- Lower `setMinimumWidth(750)` (line 62) to whatever the widest *remaining* section needs
  — measure rather than guess (see Deferred to Implementation).
- Re-check `wrap_in_scroll` / `cap_to_screen` behavior: narrowing trades width for height.

**Test scenarios:**
- Happy path: setting grid, type, order, overlap, register, reference, and fusion through
  the form produces the same `TileConfig` the pre-migration dialog produced for identical
  inputs.
- Edge case: `_stitch_check` unchecked → `tile_config` is `None` / stitching disabled,
  exactly as before.
- Edge case: `Register` unchecked → `register is False` and `overlap == 0.0`, keeping the
  byte-identical gate closed (origin R1 / AE3).
- Edge case: selecting `Linear Blending` on a dataset that carries FLIM decay still ends
  up with `fusion_method == "none"` at import — the importer forces it, because blending
  alters overlap intensities and would break `/intensity` ↔ `/decay` pixel coherence (see
  `src/percell4/domain/io/models.py:66-71`). The combo must not imply otherwise; surface
  the override rather than silently disagreeing with the user's selection.
- Integration: renaming a channel in Manual mode (`ch00 → "ER"`) still repopulates the
  reference combo **and keeps the same channel position selected** — i.e. U4 passes
  `preserve="index"` and the by-text default does not silently take over.
- Integration: discovering tiles still auto-checks `_stitch_check`
  (`compress_dialog.py:821-822` references it from outside the block U4 deletes) and the
  new form becomes visible.
- Regression (R1): the dialog's `minimumSizeHint().width()` is at or below the new
  minimum width — i.e. no horizontal scrollbar at the dialog's own minimum size.

**Verification:** The Compress dialog shows every stitching control without horizontal
scrolling, and produces byte-identical `TileConfig` values for identical user input.

---

- U5. **Migrate `AddLayerDialog`'s batch and TCSPC tabs**

**Goal:** Retire both in-file duplicates — closing `todos/037`, the follow-up that was
filed in May and never actioned.

**Requirements:** R5

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/gui/add_layer_dialog.py`
- Test: `tests/test_gui/test_add_layer_stitch_metadata_seed.py`,
  `tests/test_gui/test_add_layer_timepoint.py`

**Approach:**
- Batch tab: replace lines 278-313 with `StitchingForm(show_registration=False,
  show_fusion=False)`; replace the `TileConfig` build at 527-534. The inline
  `_stitch_plane` → `assemble_tiles` path (552-569) is unchanged.
- TCSPC tab: replace lines 943-995 similarly, and replace its `TileConfig` build at
  **`add_layer_dialog.py:1576-1584`**. **Watch the disable branch:** when the stitch
  checkbox is unchecked, the TCSPC tab builds `TileConfig(grid_rows=1, grid_cols=1)`,
  whereas `compress_dialog.py:475-476` and `import_dialog.py:382-383` set `tile_config =
  None`. Three surfaces, two disable semantics. Preserve each surface's existing behavior
  — `StitchingForm.tile_config()` returns `None` and the *caller* substitutes the 1×1
  where that is what it did before. Flattening these is a silent behavior change on the
  decay path. Re-wire `_tcspc_stitching_user_edited`
  through the form's `changed` signal (`todos/037` criterion 3) — it gates whether a
  re-Scan recomputes default stitching from the dataset's compress config.
- **Connect `changed` only after the initial seeding**, following the existing precedent
  at lines 988-995, so programmatic seeding does not spuriously mark the form
  user-edited. The Type→Order repopulation adds a new emission source here. Note the
  seeding path already brackets itself with a save/restore of
  `_tcspc_stitching_user_edited` (lines 1250 and 1264), which should absorb the extra
  emission — confirm rather than assume, since that guard restores a captured value
  rather than suppressing the signal.
- The metadata seeding at 1216-1268 already moved to `findData` in U2; repoint it at the
  form's widgets.
- The status message at lines 1265-1268 interpolates the raw `grid_type` / `order`
  strings. Switch it to the combos' display text so the confirmation the user reads
  matches the labels they see.
- Keep `_tcspc_stitch_*` / `_batch_stitch_*` as thin properties, or migrate every call
  site — decide per tab by counting references (see Deferred to Implementation).
- Per origin R13, add the read-only "reusing persisted geometry" affordance rather than
  registration controls.

**Execution note:** `add_layer_dialog.py` is ~2100 lines with deep coupling to the TCSPC
table and replace-state machine. `todos/037` explicitly recommends a find-and-replace
migration with focused tests over a structural rewrite.

**Test scenarios:**
- Happy path: batch-tab stitching produces the same `TileConfig` as before for identical
  inputs.
- Integration: seeding the TCSPC tab from a dataset's `/metadata` selects the persisted
  Type and Order — asserted on the resulting `TileConfig`, including a legacy
  `right_up` value.
- Edge case: programmatic seeding does **not** set `_tcspc_stitching_user_edited`;
  a user-driven Type change **does**.
- Edge case: after a user edit, a re-Scan does not clobber the user's stitching values.
- Edge case: with the TCSPC stitch checkbox unchecked, the tab still yields
  `TileConfig(grid_rows=1, grid_cols=1)` — **not `None`** — preserving the divergence from
  `CompressDialog` rather than unifying it.
- Integration: the reuse affordance appears when the dataset is flagged `registered` and
  no registration controls are offered on either tab.

**Verification:** Both tabs use the canonical form; the user-edited suppression still
works; `todos/037` acceptance criteria 1-6 are met.

---

- U6. **Migrate `BatchTCSPCDialog` and retire `StitchingFlimForm`**

**Goal:** Move the one existing shared-form consumer onto the split widgets and delete
the transitional composite.

**Requirements:** R5, R7

**Dependencies:** U1, U3. **Not** U4 or U5 — `_stitching_flim_form` is imported at exactly
one place in `src/` (`batch_tcspc_dialog.py:72`), so this unit can land in parallel with
the other migrations.

**Files:**
- Modify: `src/percell4/gui/batch_tcspc_dialog.py`
- Delete: `src/percell4/gui/_stitching_flim_form.py`
- Test: `tests/test_gui/test_batch_tcspc_dialog.py`,
  `tests/test_gui/test_stitching_flim_form_registration.py` (rename/retarget)

**Approach:**
- Replace the `StitchingFlimForm` at lines 312-325 with `StitchingForm` plus
  `RotateFlipForm` plus `FlimBinParamsForm` inside the existing
  `QGroupBox("5. Stitching & orientation …")`; update the reads at 865-875. All three
  widgets exist by U1, so nothing is orphaned.
- **Pass `show_registration=True`.** `BatchTCSPCDialog` shows overlap / register /
  reference today via the shared form, and four tests pin them
  (`tests/test_gui/test_stitching_flim_form_registration.py:11-56`). Keep every one of
  them exactly as-is. An earlier draft proposed `False` on the argument that origin R13
  reserves registration controls for Import and Compress — that would **remove controls
  the user can currently see**, which is outside this refactor's remit. Not doing it. The
  four tests stay green, unmodified.
- Wire all three widgets' `changed` to `_invalidate_run`.
- Delete `_stitching_flim_form.py` once no importer remains; retarget its tests.

**Test scenarios:**
- Happy path: the dialog builds the same `TileConfig`, `rotation_k`, `flip_axis`, and
  `FlimConfig` as before for identical inputs.
- Integration: changing any control on *either* widget disables Run
  (`_invalidate_run` fires).
- Edge case: the rotate/flip combos still read via `itemData`, never `currentIndex` —
  a documented PR #9 drift bug.
- Regression: no module under `src/` imports `_stitching_flim_form` (assert by grep in
  the test, so a stray re-import fails CI).

**Verification:** One stitching widget class remains in the codebase.

---

- U7. **Migrate `ImportDialog`**

**Goal:** Kill the last duplicate — the one already carrying a recurrence of the PR #9
four-item drift bug.

**Requirements:** R5

**Dependencies:** U3

**Files:**
- Modify: `src/percell4/gui/import_dialog.py`
- Test: `tests/test_gui/test_stitching_flim_form_registration.py`,
  `tests/test_gui/test_dialog_migrations.py`

**Approach:**
- Replace lines 95-149 with `StitchingForm(show_registration=True, show_fusion=False)`;
  replace the `tile_config` property at 380-394 with a delegation.
- Keep `_tile_enabled` toggling visibility, and `_tile_rows` / `_tile_cols` as thin
  properties for the two tests that drive them.
- This dialog is not instantiated anywhere in `src/`. Migrating is cheap and removes the
  drift; whether to delete it outright is deferred (see Scope Boundaries).

**Test scenarios:**
- Happy path: `tile_config` returns the same `TileConfig` as before for identical inputs.
- Edge case: the Order combo shows four Type-appropriate FIJI labels, same as every other
  migrated surface.
- Integration: `test_dialog_migrations.py`'s scroll-wrapper smoke test still passes.

**Verification:** No stitching widget is constructed outside `_stitching_form.py`.

---

- U8. **Audit artifacts, canonical-source registration, and docs**

**Goal:** Make the consolidation stick — so the next person to edit a stitching widget is
warned, per the repo's audit-driven retrieval convention.

**Requirements:** R5

**Dependencies:** U4, U5, U6, U7

**Files:**
- Modify: `docs/audits/canonical-sources-matrix.yaml`,
  `docs/audits/gui-element-classification.yaml`, `src/percell4/gui/CLAUDE.md`
- Create: `docs/solutions/architecture-patterns/canonical-stitching-form.md`
- Delete: `docs/archive/todos/037-pending-p2-migrate-add-layer-tcspc-to-stitching-flim-form.md`

**Approach:**
- Register `src/percell4/gui/_stitching_form.py` as a canonical source with `applies_to`
  globs covering all four dialogs. **Today the registry surfaces zero stitching-specific
  entries for any of them** — the `PreToolUse` hook
  (`scripts/claude_code_hooks/check_learnings_retrieval.py`) will not warn anyone editing
  a stitching widget. Verify with `python3 scripts/learnings_applicability.py` against
  each dialog path.
- Collapse the `stitch_*` entries in `gui-element-classification.yaml` to one canonical
  set — **and add the ones that were never recorded.** The compress block runs
  `compress.stitch_check` (line 2207) through `compress.stitch_order_combo` (ending 2260):
  five entries covering check/rows/cols/type/order, with **no entries at all** for
  `stitch_overlap`, `stitch_register`, `stitch_reference`, or `stitch_fusion`.
  `import_dialog` has only `tile_enabled_check` (2052-2061) — no rows/cols/type/order.
  So this is an *add-and-collapse*, not a rename. **Classification does not change** —
  every control remains an `Action`; only `path` / `lines` change for existing entries,
  and the existing line values are already stale.
- Write the solutions entry: the Type→Order matrix, the corner-alias equivalence, and the
  "labels are presentation, `itemData` is the wire format" rule.
- Note in the solutions entry that `order` is inert on the registered path, cross-linking
  the deferred follow-up.
- Update `src/percell4/gui/CLAUDE.md` to describe the new widgets (current state only —
  no history, per the repo's documentation rules).
- Delete `todos/037`; it is now closed by U5.

**Test scenarios:**
- Test expectation: none — documentation and audit metadata carry no behavior.
- Verification is by query, not assertion: `scripts/learnings_applicability.py` returns
  the new canonical entry for all four dialog paths.

**Verification:** The registry warns on edits to any stitching surface; no audit artifact
still points at a deleted widget.

---

## System-Wide Impact

- **Interaction graph:** `StitchingForm.changed` feeds `BatchTCSPCDialog._invalidate_run`
  (Run-button gating) and `AddLayerDialog._tcspc_stitching_user_edited` (re-Scan
  suppression). The new Type→Order repopulation is a *new emission source* on that
  signal — it must fire exactly once on a real change and never during programmatic
  seeding, or Run gating and re-Scan suppression both misbehave.
- **Error propagation:** `TileConfig.__post_init__` is the only validator for these
  strings. As long as `itemData` carries canonical values, no UI combination can raise.
  U3's exhaustive 4×4 test is what enforces that.
- **State lifecycle risks:** The `findText` → `findData` migration at
  `add_layer_dialog.py:1254-1263` is the sharpest edge in the plan. A miss is *silent*
  today — no exception, just a wrong default. Because that seeded geometry places decay
  tiles relative to already-stitched intensity, a silent fallback misaligns `/decay`
  against `/intensity`, which is the exact failure class
  `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md` exists to
  prevent. Test on the resulting `TileConfig`, never on a combo index.
- **API surface parity:** Four dialogs must produce identical `TileConfig` values
  post-migration. The parity tests
  (`tests/test_workflows/test_phases_compress_tile_config.py`,
  `tests/test_gui_workflows/test_compress_plan_field_parity.py`) guard the plan-dict hop
  and should be extended rather than replaced.
- **Integration coverage:** The `run_config.json` round-trip
  (`config_dialog.py:266-278` → `phases.py:131-148`) is not exercised by any widget-level
  test. Keep the existing plan-dict tests green; they are the only guard that the four-hop
  threading still carries `grid_type` / `order`.
- **Unchanged invariants:** `TileConfig` field names and accepted vocabulary;
  `_tile_positions` placement math; the registered-stitch geometry contract
  (`docs/solutions/architecture-patterns/overlap-aware-stitching.md`); the
  `register ∧ overlap>0 ∧ grid>1×1` byte-identical gate; `/metadata` attr names written
  at `importer.py:925-929`; every element's Selector/Creator/Action classification.

---

## Baseline

Green as of 2026-07-27, before any unit lands — 72 passed:

```
tests/test_io/test_assembler.py                          15
tests/test_domain/test_models_tile_config.py             16
tests/test_gui/test_batch_tcspc_dialog.py                24
tests/test_gui/test_compress_dialog_stitch_registration.py  7
tests/test_gui/test_stitching_flim_form_registration.py   6
tests/test_workflows/test_phases_compress_tile_config.py   4
```

Every unit except U2 and U3 should leave this suite green without edits. U2 requires
rewriting `test_batch_tcspc_dialog.py::test_stitching_combos_match_existing_dialog_conventions`
(it hard-pins `itemText` lists); U3 adds new coverage. Any *other* failure in this set is
a regression, not an expected update.

---

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| Relabeling breaks `TileConfig` validation, because reads go through `currentText()` | U2 lands `itemData` carriers as a separate behavior-preserving commit *before* any label changes. Each half is independently revertible. |
| `findText` seeding silently no-ops after relabeling, misaligning decay against intensity | Migrated to `findData(normalize_order(...))` in U2; tested on the resulting `TileConfig` rather than the combo index, including legacy `right_up`-style stored values. |
| Grid size X/Y wired to the wrong `TileConfig` field, transposing mosaics | X→`grid_cols`, Y→`grid_rows`, confirmed from `assembler.py:61-63` and the vendored `grid_size_x=grid_cols` boundary. U3 tests a deliberately **non-square** 3×2 grid — a transposition is invisible on the square grids existing tests use. |
| Type→Order repopulation emits `changed` zero times or many times, breaking Run gating and re-Scan suppression | The three-guard pattern in U3; a signal-path test that drives `setCurrentIndex` rather than a bare setter, per the twice-burned convention doc. |
| Narrowing the layout makes dialogs too tall | `wrap_in_scroll` / `cap_to_screen` already exist and are enforced by `test_dialog_helper_compliance.py` for `*_dialog.py`. Note the extracted widget is *outside* that glob — the consuming dialogs stay responsible. Re-verify per dialog in U4-U7. |
| `tests/test_gui/test_batch_tcspc_dialog.py:514-568` fails | Expected and correct — it hard-pins `itemText` lists. Rewrite it to pin `itemData` so it keeps guarding the wire format after display strings move. Note its tail also asserts `bin_dtype` / `bin_header`, which U1 moves — so this one test spans both U1 and U2. |
| Users pick an Order that is silently ignored under `Register` | Tooltip in U3. The real fix is deferred; do not let the tooltip's existence imply the behavior is intended. |
| `add_layer_dialog.py`'s size and coupling make migration risky | `todos/037` recommends find-and-replace with focused tests over a structural rewrite; thin properties keep call sites intact. |

---

## Alternative Approaches Considered

- **A separate modal "Grid/Collection Stitching" dialog**, matching FIJI's actual UX and
  launched by a button on each surface. Would shrink the host dialogs the most and kill
  the horizontal scrollbar outright. Rejected by the user in favor of the inline widget:
  a modal hides the settings one click deeper and would require summary text on each host
  so the current configuration stays visible.

- **Changing `TileConfig`'s `grid_type` / `order` vocabulary to FIJI's strings.** The
  obvious-looking move, and wrong. Those strings are written into `/metadata` as
  `stitch_grid_type` / `stitch_order` for every `.h5` ever created
  (`src/percell4/adapters/importer.py:925-929`) and into every `run_config.json`. Changing
  them would orphan existing files and break plan replay, for zero benefit — the existing
  vocabulary already expresses all sixteen FIJI combinations. Labels are presentation;
  the stored string is a wire format.

- **Keeping all eight Order options visible regardless of Type**, and merely relabeling
  them. Cheaper, but it preserves the actual defect: eight options that mean four things,
  half of which read as nonsense under the selected pattern. It also would not match FIJI,
  which is the stated goal.

- **A shared abstract base class per surface instead of capability flags.** Rejected —
  subclassing would reintroduce multiple construction sites, which is the exact drift this
  plan exists to eliminate. Flags keep one `_build_ui`.

---

## Documentation / Operational Notes

- No data migration, no `.h5` rewrite, no user-facing behavior change to existing
  datasets. Files imported before this change seed the UI correctly via
  `normalize_order`.
- The visible change is label-only: `Rows`/`Cols`/`Pattern`/`Start` become
  `Grid size X`/`Grid size Y`/`Type`/`Order`, and Order shows four Type-appropriate
  options instead of eight mixed ones.
- Users who previously chose a corner-named order under a *row* pattern (e.g. `top_left`
  with `row_by_row`) will see it as `Right & Down`. Same behavior, clearer name.

---

## Sources & References

- **Origin document (partial):**
  `docs/brainstorms/2026-06-24-mosaic-merge-overlap-stitching-requirements.md` — this plan
  executes its deferred "separate refactor" scope item and honors its R13.
- Tracked follow-up closed by U5:
  `docs/archive/todos/037-pending-p2-migrate-add-layer-tcspc-to-stitching-flim-form.md`
- Governing precedent:
  `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
- Geometry contract:
  `docs/solutions/architecture-patterns/overlap-aware-stitching.md`
- Signal-wiring convention:
  `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md`
- Dialog sizing convention: `docs/solutions/ui-bugs/dialog-scroll-when-tall.md`
- FIJI primary source:
  [`GridType.java`](https://github.com/fiji/Stitching/blob/master/src/main/java/plugin/GridType.java),
  [`Stitching_Grid.java`](https://github.com/fiji/Stitching/blob/master/src/main/java/plugin/Stitching_Grid.java)
