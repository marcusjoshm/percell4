---
title: "Wrap tall Qt dialog content in QScrollArea + cap height to screen"
date: 2026-04-30
category: ui-bugs
module: percell4.gui
problem_type: convention
component: tooling
canonical_source: src/percell4/gui/_dialog_utils.py
applies_to:
  - "src/percell4/gui/*Dialog.py"
  - "src/percell4/gui/*_dialog.py"
  - "src/percell4/gui/workflows/**/config_dialog.py"
duplicates_at:
  - {path: "src/percell4/gui/add_layer_dialog.py", note: "TWO scroll wrappers (Discover TIFFs tab line 172, TCSPC tab line 806) + screen-bounded resize (lines 71-77) — currently the most complete pattern"}
  - {path: "src/percell4/gui/export_images_dialog.py", note: "DRIFT: no QScrollArea — checkbox lists for channels/segs/masks could overflow on small screens or many layers"}
status: pre_canonical
tags:
  - qt
  - dialog
  - qscrollarea
  - screen-size
  - convention
related_components: [tooling, frontend_stimulus]
symptoms:
  - "Dialog runs off the bottom of the screen on smaller monitors / when the user has many channels / many thresholding rounds."
  - "User repeatedly instructs 'wrap this in a scroll area' across Thread 1 (commit 6adde5a) and prior dialogs."
---

# Wrap tall Qt dialog content in QScrollArea + cap height to screen

> **Status: pre_canonical.** Four dialogs implement variants of this pattern; none is the named single source. Pick a canonical helper before retiring drift.

## Convention

Any Qt `QDialog` that can grow taller than the user's screen MUST:

1. Wrap its primary content widget in a `QScrollArea` with `setWidgetResizable(True)` and `setFrameShape(QScrollArea.NoFrame)`.
2. Cap the dialog's max height to a fraction (≤ 0.9) of the parent's `screen().availableGeometry().height()`. (`add_layer_dialog.py:71-77` is the only callsite that does this today.)

Tabbed dialogs apply the wrapper per-tab when the tab content can overflow (`add_layer_dialog.py` does this for both the Discover-TIFFs tab and the TCSPC tab; the Single-TIFF / ROIs / Cellpose tabs are short enough to skip).

## Where it should live

There is currently no shared helper. Candidate canonicals to consolidate around:

- A small `gui/_dialog_utils.py` helper, e.g. `wrap_in_scroll(content_widget) -> QScrollArea` and `cap_to_screen(dialog, fraction=0.9)`.
- Or a `ScrollableDialog` base class subclassing `QDialog`.

Either way, every dialog in `src/percell4/gui/` should consume the same helper rather than re-implementing the QScrollArea import + boilerplate inline.

## Drift sites

See `duplicates_at` frontmatter. Notable:

- **`export_images_dialog.py`** — no scroll wrapper at all. With many channels/segmentations/masks the dialog overflows. The user has not yet hit this in practice but the structural drift is real.
- **`import_dialog.py`, `compress_dialog.py`, `workflows/single_cell/config_dialog.py`** — all implement the QScrollArea wrap inline; convergence on a helper would retire each.
- **`add_layer_dialog.py`** — has both the scroll wrappers AND the screen-bound resize (lines 71-77). It's the only dialog that does the latter; that pattern alone should also be promoted into the helper.

## Relationship to PerCell history

The user has had to issue this instruction ("add a QScrollArea when this gets too tall") explicitly across the project's lifetime. It was never compounded as a learning. Thread 1 (commit `6adde5a`) re-discovered it for the add-layer TCSPC tab.

## TODO before promotion

- Pick canonical helper shape (function pair vs base class).
- Update this doc to point `canonical_source` at the helper module.
- Move `duplicates_at` entries into the matrix YAML as `re_implements` cells.
- Add a unit test that asserts every `*Dialog.py` either uses the helper or has a documented exemption.
