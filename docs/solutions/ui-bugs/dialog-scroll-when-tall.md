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
duplicates_at: []
status: canonical_clean
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

## Convention

Any Qt `QDialog` that can grow taller than the user's screen MUST:

1. Wrap its primary content widget with `wrap_in_scroll(content)` from `percell4.gui._dialog_utils`. The helper returns a `QScrollArea` configured with `setWidgetResizable(True)` and `setFrameShape(QScrollArea.NoFrame)`.
2. Call `cap_to_screen(self)` from the same module after `setMinimumWidth` / `resize`. The default fraction is 0.9 of the parent's `screen().availableGeometry()`; pass a smaller fraction for dialogs that should never approach full screen.

Tabbed dialogs apply the wrapper per-tab when the tab content can overflow. `add_layer_dialog.py` does this for the Discover-TIFFs tab and the TCSPC tab; its Single-TIFF / ROIs / Cellpose tabs are short enough to skip.

## Where it lives

`src/percell4/gui/_dialog_utils.py` — sibling to `gui/theme.py`. Two functions, no class hierarchy:

```python
from percell4.gui._dialog_utils import cap_to_screen, wrap_in_scroll
```

Function-pair shape was chosen over a `ScrollableDialog(QDialog)` base because (a) `add_layer_dialog.py` applies the wrapper per tab — the inner widgets are `QWidget`, not `QDialog`, so a base class can't express tab-level wrapping cleanly; (b) the two halves compose — a dialog that wants only one of them can take it without inheriting the other.

`cap_to_screen` swallows exceptions from `parent.screen()` so test harnesses without a `QApplication` and no-screen edge cases don't crash dialog construction. Capping is best-effort.

## Compliance test

`tests/test_gui/test_dialog_helper_compliance.py` AST-walks every `gui/**/*Dialog.py` and asserts each file either calls `wrap_in_scroll(` or appears in `EXEMPT_DIALOGS` with a one-line reason. New dialogs that grow tall without the helper fail CI.

## Relationship to PerCell history

Before 2026-04-30, the user had to issue this instruction ("add a QScrollArea when this gets too tall") explicitly across the project's lifetime; it was never compounded. The dialog-scroll-helper-rollout thread (closes 2026-04-30) consolidated five dialogs onto the helper and added the compliance test so the convention survives future code.
