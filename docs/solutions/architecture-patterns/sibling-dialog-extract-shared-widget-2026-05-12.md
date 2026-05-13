---
title: Extract shared widgets when building sibling dialogs
date: 2026-05-12
category: architecture-patterns
module: gui
problem_type: architecture_pattern
component: development_workflow
severity: medium
applies_when:
  - building a batch / multi-target version of an existing single-target dialog
  - building any sibling dialog that performs the same domain operation as an existing one
  - a plan or brainstorm explicitly says "copy the widget construction code from <existing dialog>"
  - reviewing a PR where a new dialog rebuilds widgets from dataclass field values
tags:
  - dialog
  - qt
  - widget-extraction
  - drift
  - sibling-dialog
  - shared-widget
---

# Extract shared widgets when building sibling dialogs

## Context

When a new dialog performs the same domain operation as an existing dialog
(e.g. a batch version of a single-dataset workflow, an admin-mode variant
of a user-mode dialog), the natural temptation is to read the canonical
dataclasses (`TileConfig`, `FlimConfig`, etc.) and build the widgets fresh
from those field values. *Don't.* The canonical UI lives in the existing
dialog's widget construction, not in the dataclass field set — item lists,
labels, item-data carrier values, default selections, and event-signal
wiring all carry intent the dataclasses do not encode.

PerCell4's `feat/batch-tcspc-append` (PR #9) hit this pattern at full
force. The plan said *"default to copy the widget construction code from
`add_layer_dialog.py`"* — but the implementation rebuilt Section 5 from
`TileConfig` / `FlimConfig` fields. Reviewers caught four consecutive
drift bugs in four review rounds before extraction finally closed the
class of mistake.

## Guidance

When you build a new dialog that overlaps a widget set with an existing
dialog, follow this order:

1. **Read the existing dialog's widget construction site end-to-end**
   (every `addItem`, every label string, every `itemData` value, every
   default `setCurrentIndex`, every `addRow` label, every `valueChanged`
   wire). The canonical knowledge is here, not in the dataclass.
2. **Decide once: extract or duplicate.** Extract by default. Pure
   "duplicate verbatim" is fine for one-off shims but *not* when the
   widget set will grow or be touched by ongoing maintenance.
3. **If extracting**: lift the widget construction into a small
   `QWidget` subclass in a shared module, expose only the accessors the
   callers need (`tile_config()`, `flim_config()`, etc.), and emit one
   `changed` signal so callers can invalidate downstream state. Both
   dialogs then consume the same widget — every future fix propagates
   automatically.
4. **If duplicating**: copy the entire `_build_section_X` block
   character-for-character. Don't paraphrase, don't simplify, don't
   reorder. Add a regression test that snapshots the canonical item
   lists / itemData / defaults from both dialogs and asserts equality,
   so drift fails CI.
5. **Migrate the original dialog to consume the shared widget as a
   follow-up, not a prerequisite.** Bundling the migration with the
   new feature is scope creep; leaving it as a tracked todo keeps the
   PR focused while still committing to the eventual deduplication.

PerCell4-specific: when building any new TCSPC append surface, the
canonical widget set is `StitchingFlimForm` at
`src/percell4/gui/_stitching_flim_form.py`. Consume it; do not
reimplement.

## Why This Matters

Every drift between sibling dialogs is invisible until a user hits the
specific code path the divergence affects. In PR #9 each drift looked
small in isolation:

| Drift | Symptom | Root cause |
|---|---|---|
| Origin combo had 4 items, not 8 | `top_left` / `bottom_right` orientations missing | rebuilt from `TileConfig.order` valid values arbitrarily filtered |
| Rotate/flip combos read by `currentIndex()` | Worked by coincidence; fragile to reordering | ignored existing `itemData(...)` carrier convention |
| `CrossFormatRule` was `BaseStemRule()` alone | Zero bindings for semantic channel names | ignored `build_rule_from_preset()` `CompositeRule` precedent |
| `bin_dtype` defaulted to `uint16` | Stitch errors on every real LAS X uint32 export | rebuilt from `FlimConfig.bin_dtype` default — but compress_dialog hardcoded `uint32` first in the combo for exactly this reason |

Each was a separate review round. The cumulative cost of four review
rounds dwarfs the cost of a 30-minute extraction up front. The
extraction step is also a forcing function: it makes you read the
existing widget construction completely, which is exactly the activity
that prevents drift.

Once extracted, the new widget is a single update point. PR #9's
`StitchingFlimForm` now owns: stitching grid rows/cols/pattern/start
(8-item origin combo), rotation/flip with `itemData` carriers (0/1/2/3
for k, -1/0/1 for axis), and the checkable `FLIM .bin Parameters`
group with `uint32` first in the dtype list, `YXT` first in
dim-order, and `setSpecialValueText("Auto-detect")` on header bytes.
Any future fix lands once.

## When to Apply

- A new dialog or panel will render any widget the existing dialog
  already renders for the same domain operation.
- A plan or brainstorm contains language like "copy the widgets" or
  "mirror the existing dialog" — that's the signal to extract instead.
- The new dialog will be maintained alongside the existing one (i.e.
  not a throwaway debugging panel).
- A reviewer asks "why is this different from the other dialog?" — the
  answer should be a deliberate design decision, never an oversight.

## Examples

**Wrong (rebuilds from the dataclass; produced the PR #9 drift):**

```python
# in batch_tcspc_dialog.py, before extraction
self._order_combo = QComboBox()
self._order_combo.addItems(
    ["right_down", "right_up", "left_down", "left_up"]  # ⚠ only 4 of 8
)

self._rotate_combo = QComboBox()
self._rotate_combo.addItems(
    ["0°", "90° CCW", "180°", "270° CCW (90° CW)"]  # ⚠ different labels
)                                                    # ⚠ no itemData

# ⚠ read by index, not by itemData
rotate_k = self._rotate_combo.currentIndex()
```

**Right (consumes the shared widget; drift impossible):**

```python
# in batch_tcspc_dialog.py, after extraction
from percell4.gui._stitching_flim_form import StitchingFlimForm

self._stitching_form = StitchingFlimForm()
self._stitching_form.changed.connect(self._invalidate_run)
layout.addWidget(self._stitching_form)

# At Run time:
tile_config = self._stitching_form.tile_config()
rotate_k    = self._stitching_form.rotation_k()
flip_axis   = self._stitching_form.flip_axis()
flim_config = self._stitching_form.flim_config(frequency_mhz=80.0)
```

**Right (regression test pins canonical lists when duplication is
unavoidable):**

```python
def test_stitching_combos_match_existing_dialog_conventions(qtbot):
    dlg = BatchTCSPCDialog()
    qtbot.addWidget(dlg)
    form = dlg._stitching_form
    origin_items = [
        form.stitch_order.itemText(i) for i in range(form.stitch_order.count())
    ]
    assert origin_items == [
        "right_down", "right_up", "left_down", "left_up",
        "top_left", "top_right", "bottom_left", "bottom_right",
    ]
```

## Related

- `src/percell4/gui/_stitching_flim_form.py` — the shared widget extracted in PR #9
- `src/percell4/gui/batch_tcspc_dialog.py` — first consumer of `StitchingFlimForm`
- `src/percell4/gui/add_layer_dialog.py:845-975` — original canonical widget construction (still inline; follow-up todo to migrate)
- `todos/037-pending-p2-migrate-add-layer-tcspc-to-stitching-flim-form.md` — tracked follow-up to migrate the original dialog onto the shared widget
- `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md` — related rule about how new dialogs receive dependencies (callback injection, not `launcher=self`)
- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — sibling lesson about per-input scope collapse in batch flows (different drift class, same family of root cause)
- `docs/brainstorms/2026-05-12-batch-tcspc-append-requirements.md`, `docs/plans/2026-05-12-001-feat-batch-tcspc-append-plan.md` — the requirements + plan whose "default to copy" guidance turned out to be insufficient
