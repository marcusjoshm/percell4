---
title: "refactor: Replace the Thresholding Rounds table with per-round cards and drop σ-clipping"
type: refactor
status: active
date: 2026-07-22
---

# refactor: Replace the Thresholding Rounds table with per-round cards and drop σ-clipping

## Overview

The single-cell workflow config dialog edits its thresholding rounds through a
17-column `QTableWidget`. The columns belong to three different methods (Grouped
Otsu, Adaptive σ-clipping, Adaptive Local Clipping two-pass), all shown at once
and greyed when inactive — so the table is wider than the window and gives no
signal about which fields belong to which method.

This plan replaces the flat table with a **vertical list of per-round cards**.
Each card is a full-window-width form that shows only its own method's fields,
labeled and grouped. It also **removes the Adaptive σ-clipping method from the
dialog** (dialog-only — the domain type stays), which deletes the two columns
(`k`, `global`) that exist solely for it.

The change is confined to `config_dialog.py` and its tests. The
`ThresholdingRound` build contract — the dict of per-round values and the
sentinel-dataclass construction that consumes it — is preserved so the workflow
core, runner, phases, and batch CLI are untouched.

---

## Problem Frame

The Thresholding Rounds editor has three problems, all rooted in one design
choice — every method's fields live in one flat row:

1. **Column ownership is invisible.** `Algorithm` / `GMM max` / `K-means K`
   belong to Grouped Otsu; `d_min` / `Unit` / `CNR split` / `CNR thr` /
   `GMM 2-pop` belong to Adaptive Local Clipping; `k` / `global` belong to
   σ-clipping. The header row gives no grouping, and the dialog communicates
   ownership only by greying inactive cells — which the user has to discover by
   changing the Method combo and watching what lights up.
2. **σ-clipping is unused.** The `Adaptive σ-clipping (single-window)` method has
   no GUI-panel counterpart and the user no longer runs it. Its presence in the
   Method combo (and its `k` / `global` columns) is pure clutter.
3. **The table is wider than the window.** The 17 columns sum to ~1600 px of
   seeded width; the group box overflows horizontally, so the user scrolls
   sideways to reach `Min size`.

The user chose a **round-cards** layout: each round becomes a card in a
scrollable list, showing only the fields for its selected method, with per-card
reorder/remove controls.

---

## Requirements Trace

- R1. The Thresholding Rounds editor presents each round as a full-width card in
  a vertical, scrollable list — no horizontal overflow.
- R2. Each card shows only the fields relevant to its selected Method; switching
  Method swaps the visible method-specific fields (no greyed-but-present
  columns).
- R3. The `Adaptive σ-clipping (single-window)` method is gone from the dialog:
  not in the Method dropdown, no `k` / `global` controls, no build branch.
- R4. Every surviving capability of the current table is preserved: Name (with
  live regex validation; uniqueness stays enforced at build time by
  `WorkflowConfig.__post_init__`, not live — matching today), Channel, Metric,
  Method, the Grouped Otsu fields (Algorithm, GMM max, K-means K), the ALC
  fields (Smallest Particle Diameter + unit, CNR split, CNR threshold, GMM
  2-pop), the shared Smoothing σ, and the method-agnostic Min. Particle Area +
  unit.
- R5. Add, remove, and reorder (up/down) rounds all work on cards, and reorder
  preserves each round's full field state.
- R6. The `ThresholdingRound` list produced by the dialog is identical to today
  for any configuration expressible in both UIs — the workflow core, runner,
  phases, artifacts, and batch CLI are unchanged.
- R7. The dialog's pre-flight behaviour is unchanged: a run is still blocked when
  a round needs a pixel size (µm Smallest override, **or a µm² Min size on any
  method including Grouped Otsu**) and a dataset lacks one.
- R8. Removing every round is still blocked at Start (the existing zero-round
  guard), and the empty rounds list shows a placeholder prompting the user to
  add a round.

---

## Scope Boundaries

- **Dialog-only removal of σ-clipping.** `AdaptiveClipSettings` (the dataclass)
  and the `ThresholdingRound.adaptive_clip` field stay exactly as they are.
  They are used by `src/percell4/workflows/models.py`, `phases.py`,
  `artifacts.py`, `window_bakeoff.py`, `runner.py`, and the batch CLI
  (`interfaces/cli/batch_threshold.py`), which can still produce σ-clipping
  rounds. Only the config dialog stops offering the method.
- **No change to the round data contract.** The per-round value dict and the
  `ThresholdingRound` construction in `_rounds_from_table` (sentinel selection,
  CNR settings, min-size) are preserved. The build logic loses only its
  `_METHOD_ADAPTIVE` branch.
- **No config-file migration.** The dialog is always constructed fresh
  (`WorkflowConfigDialog(parent=self)` in `main_window.py`); it never
  deserialises a saved `run_config.json` back into the editor. So there is no
  "load an old σ-clipping round into the new dialog" case to handle. (Saved
  configs remain runnable via the runner/CLI, which still understand
  `adaptive_clip`.)
- **No change to any other part of the dialog** — dataset picker, Cellpose
  settings, existing-mask group builder, segmentation picker, CSV column picker,
  output parent, particle analysis. Only the Thresholding Rounds group and the
  round-lifecycle methods it owns.
- **No new round capability.** This is a layout change plus one method removal,
  not a place to add detection features.

---

## Context & Research

### Relevant Code and Patterns

- `src/percell4/gui/workflows/single_cell/config_dialog.py` — the entire change.
  Key surfaces (all repo-relative, referenced by symbol not line):
  - `_build_rounds_group` — builds the `QGroupBox`, the `QTableWidget`, column
    widths, and the Add/Remove/↑/↓ button row.
  - `_on_add_round` — creates one row and all its cell widgets (the canonical
    list of per-round widgets, their ranges, defaults, tooltips, and signal
    wiring).
  - `_read_round_row` / `_write_round_row` — the **dict contract**: the keys
    `name, channel, metric, algorithm, method, gmm_max, kmeans_k, sigma,
    d_min_um, k, global_sigma, size_unit, cnr_classify, cnr_threshold,
    cnr_forced, min_particle_size, min_particle_size_unit`. This dict is the
    seam the card must preserve (minus `k` / `global_sigma`).
  - `_swap_rounds` / `_on_round_up` / `_on_round_down` / `_on_remove_round` —
    reorder + remove, currently via read-all-dicts → rebuild-table.
  - `_update_method_columns_enabled` / `_update_algo_columns_enabled` /
    `_update_cnr_columns_enabled` / `_on_method_changed` / `_dmin_minimum_for` —
    the greying logic that becomes per-card show/hide.
  - `_is_adaptive_row` / `_is_auto_extract_row` / `_is_alc_row` — method
    predicates used by gating, the CNR-column enable, and the pixel-size
    pre-flight.
  - `_on_round_item_changed` — live Name regex validation (`_ROUND_NAME_RE`).
  - `_round_names_from_table` / `_rounds_from_table` — name collection +
    `ThresholdingRound` construction.
  - `_refresh_column_picker` / `_refresh_column_picker_async` — the CSV column
    picker refresh, called on channel change, name change, add, remove, reorder.
- **Local precedent for the card/list pattern — in this same file.**
  `_MaskGroupPanel` and `_SegGroupPanel` are already "append another
  full-width panel to a vertical layout" builders with per-panel controls and
  Add-group buttons. The rounds cards should mirror their construction and
  ownership style (each panel a self-contained `QWidget` subclass exposing a
  small read API).
- **Local precedent for a method-driven settings form.**
  `src/percell4/gui/_adaptive_clip_settings.py` (just refactored) shows the
  frozen-`current_config()` + aggregated-`config_changed` pattern for a
  reusable settings widget; the card can adopt the same shape.

### Institutional Learnings

- Per `docs/audits/` GUI-element-classification: the config dialog is a batch
  **pre-run config surface** — dialog-local state only, never session mutations.
  The cards inherit that; they read/write only dialog-local round state.
- Per the repo's "current state only" documentation rule, the
  `src/percell4/gui/workflows/CLAUDE.md` description of the rounds table (which
  currently documents all three methods and the column-greying behaviour) must
  be rewritten in the same change, not left describing the table.
- Per the just-completed `2026-07-22-001` plan and the
  `feedback-user-facing-naming-is-fixed` memory: user-facing field labels use
  the user's vocabulary. Reuse the labels standardised there —
  **Smallest Particle Diameter**, **Min. Particle Area** — on the cards rather
  than the terse `d_min` / `Min size` column headers.

### External References

- None. This is Qt layout work following strong local patterns (`_MaskGroupPanel`
  in the same file, `_adaptive_clip_settings.py` next door). No external research.

---

## Key Technical Decisions

- **Introduce a `RoundCard(QWidget)` that owns its widgets and preserves the
  dict contract.** The card exposes `to_dict()` returning the exact key set
  `_read_round_row` produces today (minus `k` / `global_sigma`) and
  `from_dict(data)` mirroring `_write_round_row`. This keeps
  `_rounds_from_table`'s `ThresholdingRound`-building logic — the tricky
  sentinel-selection part — almost unchanged: it iterates cards instead of rows
  and drops the `_METHOD_ADAPTIVE` branch. The dict is the blast-radius
  container.
- **Method switches show/hide field groups, not grey them.** Each card holds a
  Grouped-Otsu sub-group (Algorithm, GMM max, K-means K) and an ALC sub-group
  (Smallest Particle Diameter + unit, CNR split, CNR threshold, GMM 2-pop);
  `σ` and `Min. Particle Area` are always visible (shared). Changing Method
  hides one sub-group and shows the other. Hidden fields retain their values so
  toggling Method back restores prior input — the same retention the greying
  gave.
- **Reorder swaps cards, keeping the read-all-dicts → rebuild pattern.** The
  existing `_swap_rounds` already round-trips through the value dicts; the card
  version does the same (swap in a Python list, re-lay-out the cards) so field
  state is preserved by construction. A per-card ↑/↓/✕ control set replaces the
  shared button row's selection-based move. Whether to *also* keep a shared
  "Add round" button (yes) vs shared move buttons (drop — per-card is clearer)
  is settled: per-card reorder, shared Add.
- **σ-clipping removal is inherent, not a separate rip-out.** Because the card is
  built fresh with only two methods, the `k` / `global` widgets and the
  `_METHOD_ADAPTIVE` combo entry simply never exist on it. The explicit cleanup
  is limited to deleting the now-unreferenced `_METHOD_ADAPTIVE` constant, the
  `_is_adaptive_row` predicate, the `_rounds_from_table` σ-clipping branch, the
  `_dmin_minimum_for` adaptive floor, and the σ-clipping tests.
- **Scroll container.** The card list lives in a `QScrollArea` so many rounds
  never force the dialog wider or taller than the screen — the group box keeps a
  sensible minimum height and scrolls internally.

---

## Open Questions

### Resolved During Planning

- *Does removing σ-clipping need a config migration?* No — the dialog is always
  built fresh and never loads a saved config back into the editor
  (`_write_round_row` is used only by reorder). Saved σ-clipping configs still
  run via the runner/CLI, which keep `adaptive_clip`.
- *Does the domain type get deleted?* No — dialog-only removal;
  `AdaptiveClipSettings` has many non-dialog consumers.
- *Per-card reorder controls or a shared selection-based move row?* Per-card
  ↑/↓/✕ (matches the chosen mockup and removes the "which row is selected"
  ambiguity), plus a shared "Add round" button at the bottom.
- *`RoundCard` as a sibling module or a nested class?* Sibling module
  (`single_cell/round_card.py`), matching how `_adaptive_clip_settings.py` sits
  beside its panel — it makes the card unit-testable in isolation (U1). U1's
  Files list reflects this; the nested-class option is considered-and-rejected.
- *Confirm before per-card ✕ removal?* No — the current table's Remove button
  also removes instantly with no confirmation, so per-card ✕ is not a regression.
  Match existing behaviour (instant remove).

### Deferred to Implementation

- Exact card visual chrome (frame style, header format `N · round_name`, spacing)
  — pin against the rendered dialog and the dark theme; the mockup is directional.
- Method-switch scroll stability: swapping a method's sub-group changes card
  height and reflows the list. Keep the edited card's top edge stable (or scroll
  it back into view) after the toggle so the user isn't thrown off the card they
  just clicked. Likewise scroll a newly-added card into view. Tune against the
  rendered dialog.
- Keyboard/tab order across cards (Name → Channel → Metric → Method →
  method-specific fields → ▲/▼/✕), and that the three per-card buttons are
  focusable — verify against the rendered dialog; default Qt order is likely
  fine but confirm.
- Whether the Name field moves from a table-cell `QTableWidgetItem` to a
  `QLineEdit` with a validator or keeps regex-on-`textChanged` styling — either
  preserves R4's live validation; decide against the rendered card.
- The precise `QScrollArea` sizing (min height, resize policy) that keeps 2–3
  cards visible without scrolling — tune against the dialog.

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

The seam that contains blast radius is the per-round **value dict**. Today:

```
QTableWidget row  ──_read_round_row──►  {name, channel, …, k, global_sigma, …}  ──_rounds_from_table──►  ThresholdingRound
                  ◄─_write_round_row──                                          (sentinel selection by `method`)
```

After:

```
RoundCard  ──card.to_dict()──►  {name, channel, …}  (no k / global_sigma)  ──_rounds_from_cards──►  ThresholdingRound
           ◄─card.from_dict()──                                            (Grouped Otsu / Auto-extract branches only)
```

`_rounds_from_cards` is `_rounds_from_table` with the row loop replaced by a card
loop and the `if method == _METHOD_ADAPTIVE:` branch deleted. Everything
downstream of the dict — sentinel construction, CNR settings, min-size, the
per-round `ThresholdingRound(...)` call, the row-numbered `ValueError`
messages — is unchanged.

Card internal structure (each round):

```
┌─ {N} · {name} ────────────────────────────  [▲][▼][✕] ┐
│ Name: [__________]   (live regex validation)          │
│ Channel: [▾]   Metric: [▾]   Method: [▾]              │   ← always shown
│ ┌ Grouped Otsu ─────────────────┐  (shown iff method │
│ │ Algorithm:[▾] GMM max:[] K:[] │   == Grouped Otsu)  │
│ └───────────────────────────────┘                    │
│ ┌ Adaptive Local Clipping ──────┐  (shown iff method │
│ │ Smallest Ø:[][unit▾]          │   == Auto-extract)  │
│ │ ☐ CNR split  thr:[]  ☐ 2-pop  │                    │
│ └───────────────────────────────┘                    │
│ Smoothing σ:[]   Min. Particle Area:[][unit▾]         │   ← always shown
└───────────────────────────────────────────────────────┘
```

Method → visible-group mapping (decision matrix):

| Method                        | Grouped Otsu group | ALC group | σ | Min. Area |
|-------------------------------|:------------------:|:---------:|:-:|:---------:|
| Grouped Otsu                  | shown              | hidden    | ✓ | ✓         |
| Adaptive Local Clipping (2-pass) | hidden          | shown     | ✓ | ✓         |

(CNR threshold within the ALC group is itself enabled only when `CNR split` is
on, and greyed when `GMM 2-pop` is on — the existing `_update_cnr_columns_enabled`
logic, moved onto the card.)

---

## Implementation Units

- U1. **`RoundCard` widget: one round as a self-contained form**

**Goal:** A reusable `QWidget` that renders one round's fields with method-driven
show/hide and exposes the value-dict contract, offering only Grouped Otsu and
Adaptive Local Clipping.

**Requirements:** R2, R3, R4

**Dependencies:** None

**Files:**
- Create: `src/percell4/gui/workflows/single_cell/round_card.py`
- Test: `tests/test_gui_workflows/test_round_card.py`

**Approach:**
- Build the card from the widget inventory currently in `_on_add_round`: Name,
  Channel combo, Metric combo, Method combo (only `_METHOD_GROUPED` and
  `_METHOD_AUTO_EXTRACT`), Algorithm/GMM max/K-means K, σ, Smallest Particle
  Diameter (the `d_min` spinbox) + unit, CNR split / CNR threshold / GMM 2-pop,
  Min. Particle Area + unit. Preserve every range, default, decimals, and
  tooltip from the current cell widgets — except drop the `k` and `global`
  widgets and drop σ-clipping-specific tooltip text.
- Group the widgets: a Grouped-Otsu container (Algorithm, GMM max, K-means K)
  and an ALC container (Smallest Ø + unit, CNR split, CNR threshold, GMM 2-pop);
  σ and Min. Particle Area always visible. A method-change slot shows the
  matching container and hides the other, retaining hidden values.
- Fold the existing enable logic onto the card: Algorithm gates GMM max vs
  K-means K (`_update_algo_columns_enabled`); CNR split gates CNR threshold, and
  GMM 2-pop greys CNR threshold (`_update_cnr_columns_enabled`); the Smallest Ø
  floor follows the method (`_dmin_minimum_for`, now only Grouped/Auto-extract).
- `to_dict()` returns the exact keys `_read_round_row` produces **minus** `k` and
  `global_sigma`. `from_dict(data)` mirrors `_write_round_row` (tolerating
  absent `k`/`global_sigma`). Use the labels the last plan standardised
  (Smallest Particle Diameter, Min. Particle Area).
- Preserve the σ auto-seed: when a card enters an ALC method with σ == 0, seed σ
  to the validated 1.0 (mirroring `_on_method_changed` — a 0 detector presmooth
  silently collapses ALC detection). Programmatic `from_dict` writes must NOT
  re-seed, so a saved σ survives a reorder round-trip (today `_write_round_row`
  calls the gating directly and skips the seed).
- Expose the signals the host needs: name-changed (for validation + column
  picker), channel-changed (for column picker), method-changed. Provide
  `set_channels(list)` to repopulate the Channel combo from the intersection.
- Provide `is_alc()` / `is_auto_extract()` helpers **for the card's own CNR/method
  gating only**. The host pixel-size pre-flight does NOT use them — it iterates
  the *built* `ThresholdingRound` list (see U2), so no method predicate is needed
  there.

**Patterns to follow:**
- `src/percell4/gui/_adaptive_clip_settings.py` — frozen read API + aggregated
  change signal + method-driven enable/hide.
- `_MaskGroupPanel` / `_SegGroupPanel` in `config_dialog.py` — self-contained
  `QWidget` panel with a small read API, appended to a vertical layout.

**Test scenarios:**
- Happy path: a fresh card's `to_dict()` returns the Grouped-Otsu defaults
  (method Grouped Otsu, algorithm gmm, gmm_max 10, kmeans_k 3, sigma default,
  min defaults) and contains no `k` / `global_sigma` keys.
- Happy path: setting Method to Adaptive Local Clipping shows the ALC group,
  hides the Grouped-Otsu group, and `to_dict()["method"]` is the auto-extract
  label; switching back restores the Grouped-Otsu group with its prior values.
- Edge case: the Method combo offers exactly two entries — Grouped Otsu and
  Adaptive Local Clipping — and no σ-clipping entry.
- Edge case: `from_dict(card.to_dict())` round-trips every field (identity), and
  `from_dict` tolerates a dict that still carries stale `k`/`global_sigma` keys
  without error (defensive, since old in-memory dicts might).
- Edge case: on an ALC card, CNR threshold is disabled until CNR split is on, and
  is greyed when GMM 2-pop is on; on a Grouped-Otsu card the CNR controls are not
  shown at all.
- Edge case: Algorithm=gmm enables GMM max and disables K-means K; Algorithm=
  kmeans inverts — on a Grouped-Otsu card.
- Edge case: the Smallest Ø floor is 0 (auto-detect allowed) on an ALC card.
- Edge case: switching a fresh card (σ == 0) to Adaptive Local Clipping seeds
  `to_dict()["sigma"] == 1.0`; a card whose σ was already set keeps its value;
  `from_dict({... "sigma": 0.0, "method": <ALC>})` does **not** re-seed (a saved
  0 survives). Covers the detection-collapse guard.
- Error path: an invalid Name (fails `_ROUND_NAME_RE`) marks the field as invalid
  (styling/tooltip) and the card reports it via its name-changed signal so the
  host can block the run.

**Verification:**
- The card renders within the dialog width, shows only one method's fields at a
  time, and `to_dict()`/`from_dict()` round-trip losslessly.

---

- U2. **Swap the rounds container from table to card list and rewire the host**

**Goal:** Replace the `QTableWidget` with a scrollable card list, and repoint
every round-lifecycle method (`add`, `remove`, reorder, name collection,
`ThresholdingRound` build, column-picker refresh, pixel-size pre-flight) at
cards.

**Requirements:** R1, R4, R5, R6, R7

**Dependencies:** U1

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui_workflows/test_config_dialog.py`

**Approach:**
- In `_build_rounds_group`, replace the `QTableWidget` (and its
  `_ROUND_COL_*` widths/headers) with a `QScrollArea` holding a vertical layout
  of `RoundCard`s, plus a shared bottom "Add round" button. Drop the shared
  ↑/↓/Remove button row (moves onto each card) — or keep a shared Add only.
  Maintain a `self._round_cards: list[RoundCard]` as the ordered source of truth.
- `_on_add_round`: append a new `RoundCard` seeded from the current channel
  intersection, wire its signals (name-changed → validation + column picker;
  channel-changed → column picker; ✕ → remove; ▲/▼ → move), add it to the layout
  and the list. Keep the `round_{n}` default-name behaviour.
- Reorder: `_on_round_up`/`_on_round_down`/`_swap_rounds` operate on the list +
  relayout (read-all-dicts → rebuild, or a direct card swap that preserves widget
  state). Reorder must renumber the card headers (`N · name`) but not the names.
- `_read_round_row`/`_write_round_row` collapse into `card.to_dict()` /
  `card.from_dict()`; delete the `_ROUND_COL_*` constants and the per-row gating
  methods now living on the card (`_update_method_columns_enabled`,
  `_update_algo_columns_enabled`, `_update_cnr_columns_enabled`,
  `_on_method_changed`, `_dmin_minimum_for`, `_is_adaptive_row`,
  `_is_auto_extract_row`, `_is_alc_row`, `_on_round_item_changed`).
- `_round_names_from_table` → iterate cards; `_rounds_from_table` →
  `_rounds_from_cards`, iterating `card.to_dict()` with the **same** downstream
  `ThresholdingRound` construction minus the `_METHOD_ADAPTIVE` branch (see U3).
- **Pixel-size pre-flight stays unchanged.** It already iterates the *built*
  `rounds` list — `r.adaptive_clip.d_min_unit == "um"`, `r.auto_extract`'s µm
  smallest, and the method-agnostic `r.min_particle_size > 0 and
  r.min_particle_size_unit == "um2"` (which fires for **any** method, Grouped Otsu
  included). It never calls `_is_adaptive_row`/`_is_auto_extract_row`. So the only
  edit it needs is the `_rounds_from_table` → `_rounds_from_cards` rename that
  feeds it; do **not** rewire it onto a card method predicate (that would drop
  unit-awareness and both over-block px ALC rounds and miss the µm²-Min-size-on-
  Grouped-Otsu case).
- **Repoint the round-count guards.** `_update_start_enabled` and
  `_try_build_config` both gate on `self._rounds_table.rowCount()` — replace with
  `len(self._round_cards)`. This preserves the existing zero-round Start block
  (R8). Also enumerate every other `rowCount()` / `range(rowCount())` site
  (`_round_names_from_table`, `_rounds_from_cards`, reorder bounds) in the sweep.
- **Empty state (R8):** when the card list is empty, show a placeholder label
  ("No rounds yet — click Add round to begin") in the scroll area; Start stays
  disabled via the count guard above.
- **Reorder boundary controls:** disable ▲ on the first card and ▼ on the last so
  a boundary click is visibly inert, not a silent no-op.
- Column picker: keep calling `_refresh_column_picker` on the same triggers (add,
  remove, reorder, channel change, name change) — now driven by card signals.

**Patterns to follow:**
- The existing `_swap_rounds` read-all-dicts → rebuild approach (preserves state
  through reorder without per-widget copying).
- `_MaskGroupPanel`'s "append another panel to the vertical layout" add-group
  flow in the same file.

**Test scenarios:**
- Happy path: adding two rounds yields two cards; `_rounds_from_cards` builds two
  `ThresholdingRound`s with the expected channel/metric/method.
- Happy path: a Grouped-Otsu card and an Adaptive-Local-Clipping card build the
  same `ThresholdingRound`s (algorithm/gmm/kmeans vs `auto_extract` sentinel +
  CNR + min-size) the table produced before this plan for the same inputs.
- Edge case: reorder (↓ on the first card) swaps round order in
  `_rounds_from_cards` output and preserves every field of both rounds; the card
  headers renumber but the round names do not.
- Edge case: remove (✕) drops the correct card and the column picker refreshes.
- Edge case: a duplicate or regex-invalid round name is surfaced the same way it
  is today (validation styling + run blocked with the row/round-numbered error).
- Integration: a µm Smallest-Ø ALC round with a dataset lacking `pixel_size_um`
  still blocks the run via the unchanged pre-flight (iterating the built rounds).
- Integration: a **Grouped Otsu** round carrying a µm² Min. Particle Area on a
  dataset lacking `pixel_size_um` also blocks the run — the method-agnostic
  µm²-Min-size pre-flight branch must survive (guards against the R7 regression
  the review caught).
- Edge case: a px-unit ALC round (or Smallest = 0 auto-detect) does NOT block the
  run — no pixel size needed.
- Edge case: removing the last card leaves the empty-state placeholder and Start
  stays disabled (the zero-round guard, now `len(self._round_cards) == 0`).
- Edge case: ▲ on the first card and ▼ on the last card are disabled.
- Integration: the built `ThresholdingRound` list feeds `WorkflowConfig`
  unchanged — an existing end-to-end config test still passes with cards.

**Verification:**
- The rounds group fits within the dialog width with no horizontal scroll; add/
  remove/reorder work per-card; the produced `ThresholdingRound` list matches the
  pre-refactor output for equivalent input.

---

- U3. **Prune the σ-clipping method from the dialog and its build path**

**Goal:** Remove the last dialog-side references to Adaptive σ-clipping so R3 is
fully met and no dead code names a method the UI no longer offers.

**Requirements:** R3, R6

**Dependencies:** U1, U2

**Files:**
- Modify: `src/percell4/gui/workflows/single_cell/config_dialog.py`
- Test: `tests/test_gui_workflows/test_config_dialog.py`

**Approach:**
- Delete the `_METHOD_ADAPTIVE` constant and its explanatory comment block, the
  `if method == _METHOD_ADAPTIVE:` branch (the `AdaptiveClipSettings`
  construction) in `_rounds_from_cards`, and any remaining `_dmin_minimum_for`
  adaptive floor / `_is_adaptive_row` traces not already removed in U2.
- Leave the imports of `AdaptiveClipSettings` only if still referenced; otherwise
  remove the now-unused import. Confirm `AutoExtractSettings`, `CnrClassifySettings`
  imports stay.
- Do **not** touch `src/percell4/workflows/models.py`, `phases.py`,
  `artifacts.py`, `window_bakeoff.py`, `runner.py`, or
  `interfaces/cli/batch_threshold.py` — σ-clipping stays valid there.
- **Two-bucket rule for the `_METHOD_ADAPTIVE` tests — delete only the ones that
  assert σ-clipping-exclusive behaviour; migrate the rest to
  `_METHOD_AUTO_EXTRACT`.** Many tests use `_METHOD_ADAPTIVE` merely as an ALC
  vehicle for behaviour that survives on auto-extract, and deleting them would
  silently drop reorder-survival, µm pre-flight, unit-enable, and CNR-clear
  coverage.
  - **Delete** (σ-clipping-exclusive: `k` / `global` / single-window / adaptive
    d_min floor): `test_adaptive_method_builds_adaptive_round`,
    `test_adaptive_dmin_minimum_is_positive`,
    `test_adaptive_global_sigma_checkbox_builds_round`,
    `test_global_sigma_defaults_off_and_disabled_off_adaptive`,
    `test_adaptive_px_unit_builds_round`. (~5.)
  - **Migrate** to `_METHOD_AUTO_EXTRACT` (surviving behaviour proven via the
    kept method): `test_size_unit_survives_row_swap`,
    `test_size_unit_survives_method_toggle_grey_and_swap`,
    `test_um_unit_round_flagged_by_pixel_size_preflight` (the only µm-positive R7
    case), `test_cnr_cleared_when_switching_to_grouped`,
    `test_cnr_forced_cleared_when_split_unchecked`,
    `test_size_unit_enabled_only_on_alc_rows`, and any other
    `setCurrentText(_METHOD_ADAPTIVE)` case not in the delete list.
  - Verify no surviving test references `_METHOD_ADAPTIVE`, `_ROUND_COL_K`, or
    `_ROUND_COL_GLOBAL`. Do not leave any skipped.

**Approach note (grep gate):** after this unit,
`grep -rn "_METHOD_ADAPTIVE\|σ-clipping\|single-window\|_is_adaptive_row\|global_sigma" src/percell4/gui/workflows/single_cell/`
returns nothing.

**Test scenarios:**
- Edge case: the naming-trap intent (batch ≠ GUI) is preserved where it still
  applies — the surviving test asserting that the auto-extract method maps to
  `auto_extract` (not `adaptive_clip`) stays and passes.
- Edge case: `_rounds_from_cards` never produces a round with `adaptive_clip`
  set — a card-driven build only ever sets `auto_extract` (ALC) or neither
  (Grouped Otsu).
- Test expectation: the deleted σ-clipping tests are removed, not skipped; the
  remaining suite has no reference to `_METHOD_ADAPTIVE` or `_ROUND_COL_K` /
  `_ROUND_COL_GLOBAL`.

**Verification:**
- The grep gate above is clean; the domain σ-clipping consumers still compile and
  their tests still pass.

---

- U4. **Migrate the remaining config-dialog tests to card accessors**

**Goal:** The config-dialog test suite drives the new card UI instead of
`_rounds_table.cellWidget(row, _ROUND_COL_*)`, and covers the new reorder/remove
affordances.

**Requirements:** R1, R4, R5

**Dependencies:** U2, U3

**Files:**
- Modify: `tests/test_gui_workflows/test_config_dialog.py`
- Modify (verify only — it exercises the particle Min-area combo, not rounds, so
  it has zero round references): `tests/test_gui_workflows/test_config_dialog_min_area_unit.py`
- Modify (verify only): `tests/test_gui_workflows/test_config_dialog_existing_masks.py`,
  `tests/test_gui/test_config_dialog_segmentation_picker.py`,
  `tests/test_gui/test_dialog_migrations.py`

**Approach:**
- Replace every `dialog._rounds_table.cellWidget(row, _ROUND_COL_X)` access with
  the corresponding `dialog._round_cards[row].<widget>` (or a small card helper
  like `card.set_method(...)`) so tests manipulate the same state through the new
  surface.
- Add coverage for the two new user affordances that the table did not have:
  per-card ✕ remove and per-card ▲/▼ reorder (state preserved).
- The min-area-unit and existing-mask / segmentation-picker / migrations tests
  that only *touch* the rounds incidentally (e.g. add one round, then test
  another group) need the smallest possible edit — just the add-round + field-set
  calls repointed at cards. Verify they pass unchanged where they don't touch
  round internals.

**Execution note:** GUI tests run on CI as the canonical gate, but this suite
runs locally when invoked per-file (the mixed-Qt segfault only bites the whole
`tests/test_gui*` directory at once). Validate per-file locally, then rely on CI
for the full sweep.

**Test scenarios:**
- Happy path: the migrated build-a-round test constructs the expected
  `ThresholdingRound` through card accessors.
- Edge case: a new test removes the middle of three cards and asserts the
  remaining two rounds and their order.
- Edge case: a new test reorders two cards and asserts both the new order and
  that every field of each round survived the move.
- Integration: the existing-mask, segmentation-picker, and migrations suites pass
  with only mechanical add-round repointing.

**Verification:**
- `test_config_dialog.py` and `test_config_dialog_min_area_unit.py` pass with no
  reference to `_rounds_table` or `_ROUND_COL_*`; the incidental suites pass.

---

- U5. **Sync the workflow docs to the card-based editor**

**Goal:** No active doc describes the rounds table or the σ-clipping method.

**Requirements:** R1, R3

**Dependencies:** U2, U3

**Files:**
- Modify: `src/percell4/gui/workflows/CLAUDE.md`
- Modify (if present): `CHANGELOG.md`

**Approach:**
- Rewrite the `config_dialog.py` bullet in `src/percell4/gui/workflows/CLAUDE.md`:
  it currently describes a "thresholding rounds table" with a per-row Method combo
  offering three methods and column-greying. Replace with the card-list editor
  offering two methods (Grouped Otsu, Adaptive Local Clipping two-pass), with the
  σ-clipping paragraph removed. Keep the accurate description of the ALC round's
  `d_min`/CNR/min-size semantics — those are unchanged, only their presentation
  is.
- Add a CHANGELOG entry under Unreleased → Changed: the Thresholding Rounds
  editor is now a per-round card list, and the Adaptive σ-clipping method is no
  longer offered in the workflow dialog (batch CLI unaffected).
- Note (per the last plan's finding) that `CLAUDE.md` files are gitignored in
  this repo, so that edit is local-only; the CHANGELOG entry is the tracked
  record.

**Test scenarios:**
- Test expectation: none — documentation only.

**Verification:**
- `grep -rn "σ-clipping\|single-window\|rounds table" src/percell4/gui/workflows/CLAUDE.md`
  is clean; the CHANGELOG names the layout change and the method removal.

---

## System-Wide Impact

- **Interaction graph:** The rounds group is a leaf editor within the dialog. Its
  only outward couplings are the CSV column picker (`_refresh_column_picker`, on
  name/channel/add/remove/reorder), the pixel-size pre-flight, and the final
  `_rounds_from_*` build. All three are preserved by keeping the value-dict
  contract and re-emitting the same refresh triggers from card signals.
- **Error propagation:** Per-round validation (name regex, channel-in-
  intersection, `ThresholdingRound` `ValueError`s) keeps its round-numbered
  messages; the build loop still raises with the round index so the user can find
  the offending card.
- **State lifecycle risks:** Reorder is the one place field state can be dropped.
  The plan keeps the read-all-dicts → rebuild (or explicit card-swap) approach so
  every field survives a move — the highest-value reorder test in U2/U4.
- **API surface parity:** The batch CLI (`batch_threshold.py`) is the *other*
  surface that builds thresholding rounds. It is intentionally left offering
  σ-clipping (it can express rounds the GUI dialog cannot). This divergence is
  accepted and mirrors the existing GUI-vs-batch smallest-particle divergence
  from plan `2026-07-22-001`.
- **Integration coverage:** The card `to_dict()` → `_rounds_from_cards` →
  `ThresholdingRound` → `WorkflowConfig` chain is where a unit test on the card
  alone would not prove correctness; U2 carries an end-to-end config test.
- **Unchanged invariants:** `AdaptiveClipSettings`, `ThresholdingRound.adaptive_clip`,
  `AutoExtractSettings`, `CnrClassifySettings`, and the whole
  `src/percell4/workflows/` core keep their signatures, defaults, and behaviour.
  A config built from a Grouped-Otsu or ALC round is byte-identical to today's.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Reorder silently drops a field (the classic table-rebuild bug) | Keep the read-all-dicts → rebuild contract; U2/U4 carry an explicit "reorder preserves every field" test. |
| The card build diverges from the table build for some field, changing the produced `ThresholdingRound` | Preserve the exact value-dict keys and reuse the downstream `ThresholdingRound` construction verbatim (minus the σ-clipping branch); U2 asserts card-vs-prior equivalence. |
| Large test-migration surface (~272 `_rounds_table`/`_ROUND_COL` references: ~163 in `config_dialog.py`, ~109 in `test_config_dialog.py`) is error-prone | Contain it: only `config_dialog.py` + `test_config_dialog.py` change substantively; the other suites get mechanical add-round repointing or none. The value-dict seam means non-test code barely moves. |
| Removing σ-clipping breaks a non-dialog consumer | Verified dialog-only: `AdaptiveClipSettings` stays; grep-gate in U3 confirms no dialog references remain, and the domain/CLI suites are untouched. |
| `QScrollArea` sizing makes the dialog too tall or cards cramped | Tune against the rendered dialog (deferred); keep a group-box minimum height and internal scroll. |

---

## Documentation / Operational Notes

- No migration, no persisted-state change. Saved `run_config.json` files with
  σ-clipping rounds still run via the runner/CLI.
- Per the CI/local-GUI-test note: validate GUI tests per-file locally; CI runs
  the full `tests/test_gui*` sweep as the gate.
- The `src/percell4/gui/workflows/CLAUDE.md` edit is local-only (gitignored); the
  CHANGELOG carries the tracked record.

---

## Sources & References

- Related code: `src/percell4/gui/workflows/single_cell/config_dialog.py`
  (`_build_rounds_group`, `_on_add_round`, `_read_round_row`, `_write_round_row`,
  `_rounds_from_table`, `_swap_rounds`, `_update_method_columns_enabled`,
  `_MaskGroupPanel` / `_SegGroupPanel`),
  `src/percell4/gui/_adaptive_clip_settings.py`
- Related domain (unchanged, σ-clipping stays): `src/percell4/workflows/models.py`,
  `phases.py`, `artifacts.py`, `interfaces/cli/batch_threshold.py`
- Related prior plan: `docs/plans/2026-07-22-001-refactor-ui-cleanup-alc-cnr-plan.md`
  (label vocabulary, GUI-only-removal pattern)
