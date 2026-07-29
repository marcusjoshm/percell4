---
title: "Canonical stitching form — one widget for every tiling surface, with display labels separable from the persisted wire format"
date: 2026-07-28
category: architecture-patterns
module: percell4.gui._stitching_form, percell4.gui._stitch_order, percell4.gui.compress_dialog, percell4.gui.add_layer_dialog, percell4.gui.batch_tcspc_dialog, percell4.gui.import_dialog
problem_type: architecture_pattern
component: gui
canonical_source: src/percell4/gui/_stitching_form.py
applies_to:
  - "src/percell4/gui/_stitching_form.py"
  - "src/percell4/gui/_stitch_order.py"
  - "src/percell4/gui/_tile_order_preview.py"
  - "src/percell4/gui/compress_dialog.py"
  - "src/percell4/gui/add_layer_dialog.py"
  - "src/percell4/gui/batch_tcspc_dialog.py"
  - "src/percell4/gui/import_dialog.py"
canonical_functions:
  - "src/percell4/gui/_stitching_form.py::StitchingForm"
  - "src/percell4/gui/_stitching_form.py::StitchingForm.tile_config"
  - "src/percell4/gui/_stitching_form.py::StitchingForm.set_reference_channels"
  - "src/percell4/gui/_stitch_order.py::normalize_order"
  - "src/percell4/gui/_stitch_order.py::order_labels_for"
status: canonical_clean
tags:
  - stitching
  - mosaic
  - qt
  - dialog
  - widget-extraction
  - shared-widget
  - itemdata
  - drift
  - fiji
  - canonical-source
related_components: [gui, io]
---

# Canonical stitching form

Every GUI surface that stitches tiles embeds one widget: `StitchingForm`. Before
this, four dialogs built their own grid / pattern / order controls and drifted
apart — the same failure `sibling-dialog-extract-shared-widget-2026-05-12.md`
documents from PR #9, **recurring even after a shared widget existed and was
documented as canonical**, because nothing failed when someone made a copy.

## Label and value are separate concerns

This is the load-bearing rule and the reason the Fiji relabel was safe.

- **Display text is presentation.** It follows the Fiji *Grid/Collection
  Stitching* plugin: `Pattern` and `Order`, with the Order options **keyed to
  the selected Pattern** (row types offer Right/Left & Down/Up; column types
  offer Down/Up & Right/Left — Fiji's own `GridType.java` `choose2[gridType]`).
- **`itemData` is the wire format.** It carries PerCell4's existing canonical
  strings, which are written into `.h5` `/metadata` as `stitch_grid_type` /
  `stitch_order` (`adapters/importer.py`) and serialized into
  `run_config.json`. **These must never move**, or existing files and saved
  plans stop replaying.

Reads therefore go through `currentData()`, never `currentText()` and never
`currentIndex()`; restores go through `findData()`, never `findText()`. That is
the same `itemData`-carrier convention `rotation_combo` / `flip_combo` /
`_stitch_fusion` already followed, and the two combos that violated it were
exactly the ones that could not be relabeled.

## `TileConfig.order` is four behaviors under two alias sets

`assembler._tile_positions` normalizes all eight accepted `order` strings to two
booleans `(start_bottom, start_right)`, and the grid type independently decides
whether the scan walks rows or columns first. So:

| corner name     | row-centric alias | `(start_bottom, start_right)` | tile 0    |
|-----------------|-------------------|-------------------------------|-----------|
| `top_left`      | `right_down`      | (F, F)                        | top-left  |
| `top_right`     | `left_down`       | (F, T)                        | top-right |
| `bottom_left`   | `right_up`        | (T, F)                        | bottom-left |
| `bottom_right`  | `left_up`         | (T, T)                        | bottom-right |

Which corner tile 0 occupies is **independent of the grid type**. That is why a
single four-item corner vocabulary serves both Pattern families, why switching
Pattern can *reword* the user's pick instead of resetting it, and why adopting
Fiji's sixteen Pattern × Order combinations needed **no domain change at all**.

`_stitch_order.normalize_order` maps any accepted value onto its canonical
corner. It **raises rather than defaulting** — a silent default here is the
failure mode this whole area exists to prevent.

## Rules

1. **Never build stitching controls outside `_stitching_form.py`.** Enforced by
   `tests/test_gui/test_stitching_form_consolidation.py`, which AST-scans every
   `gui/**/*.py` for the grid-type vocabulary. Its `PENDING_MIGRATION` set is
   empty and must stay that way.

2. **Per-surface variation is constructor flags, not subclasses.**
   `show_registration` / `show_fusion` keep one `_build_ui`. With registration
   hidden, `tile_config()` returns `register=False, overlap=0.0,
   reference_channel=None` so the byte-identical importer gate
   (`register ∧ overlap>0 ∧ grid>1×1`) stays shut.

3. **Grid size X is `grid_cols`; Grid size Y is `grid_rows`.** Matching Fiji's
   own `grid_size_x`/`grid_size_y` and the assembler, where the canvas is
   `grid_rows·tile_h` by `grid_cols·tile_w`. A swap transposes the mosaic, is
   invisible on the square grids most tests use, and on the registered path
   changes `native_shape`. Test non-square.

4. **Seed persisted state through `normalize_order` + `findData`.**
   `add_layer_dialog._tcspc_seed_stitching_from_metadata` is the live example. A
   miss is silent (`idx < 0` skips `setCurrentIndex`), leaving the default
   selected — which misplaces decay tiles against already-stitched intensity,
   the exact class
   `docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md`
   exists to prevent. Assert on the resulting `TileConfig`, never a combo index.

5. **The disable semantics differ per surface, deliberately.** Compress and
   Import yield `None` when their checkbox is off; the AddLayer TCSPC tab yields
   `TileConfig(grid_rows=1, grid_cols=1)`. **Do not unify them** — that is a
   silent behavior change on the decay path.

6. **`set_reference_channels(preserve=…)` has two real callers with opposite
   needs.** `"text"` (Import) keeps the channel *name* across a re-discovery;
   `"index"` (Compress) keeps the *position*, because a Manual-mode rename
   changes the name out from under the selection.

7. **The Pattern→Order repopulation must emit `changed` exactly once.** Zero
   leaves a Run button enabled against a stale config; two signals a double
   wire. It is also a *new* emission source on a signal that gates
   `_tcspc_stitching_user_edited`, so programmatic seeding must not trip it —
   the seeding path's save/restore of that flag absorbs it, and a test seeds a
   deliberately non-default Pattern to prove the repopulation actually fires.

8. **The preview diagram does not reimplement the traversal.**
   `_tile_order_preview.TileOrderPreview` derives its path from
   `assembler._tile_positions` — the same function that places real tiles — so
   the picture cannot disagree with what the importer will do. Redrawing the
   walk in the GUI would reintroduce exactly the duplication this pattern
   removes.

## Known limitation (not introduced here)

`order` is **inert on the registered path**. Both `estimate_tile_offsets` and
`grid_seed_offsets` delegate seeding to the vendored `grid_positions`, which is
always top-left seeded; `order` is passed to `_tile_positions` only to validate.
So with Register on, every Order behaves as `Right & Down`. Surfaced in the
Order tooltip. Fixing it is a domain change to a T1 file guarded by
byte-identical regression tests and belongs in its own work.

## Related

- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`
  — the precedent whose deferred follow-up (`todos/037`) produced this debt.
- `docs/solutions/architecture-patterns/overlap-aware-stitching.md` — the
  geometry contract behind `TileConfig`; it is a deliberate dumb carrier, so the
  Pattern→Order coupling lives in the widget, not `__post_init__`.
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — why the
  repopulation needs a signal-path test, not a bare-setter one.
- `docs/solutions/ui-bugs/dialog-scroll-when-tall.md` — narrowing trades width
  for height; the consuming dialogs stay responsible for scroll.
