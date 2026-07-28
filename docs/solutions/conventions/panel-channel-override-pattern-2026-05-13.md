---
title: Per-panel channel-override combo — extract on the fourth site
date: 2026-05-13
status: superseded
superseded_by:
  - docs/plans/2026-05-13-001-feat-session-selection-window-plan.md
  - docs/brainstorms/2026-05-13-session-selection-window-requirements.md
superseded_on: 2026-05-13
category: conventions
module: gui
problem_type: convention
component: development_workflow
severity: medium
applies_when: []
tags:
  - qt
  - channel
  - combo
  - panel
  - shared-helper
  - extract-threshold
  - superseded
---

# Per-panel channel-override combo — extract on the fourth site

> **Superseded — 2026-05-13.** The per-panel channel-override pattern this
> document codified was retired the same day. The three panels it referenced
> (Cellpose, Grouped Segmentation, FLIM) no longer have local `_channel_combo`
> overrides; they read `session.active_channel` directly. The canonical
> Selector for the three active session fields is the always-visible
> `SessionWindow` at
> `src/percell4/interfaces/gui/peer_views/session_window.py`. The
> "fourth site triggers extract" rule no longer applies because there is
> no first/second/third site to extract from. Preserved as historical
> context — do not use this pattern in new work. See the requirements
> doc and plan listed in `superseded_by` for the replacement model.

## Context

PerCell4 has three task panels that let the user pick a per-module
channel override that defaults to `session.active_channel` but does not
write back to the session when changed:

- `src/percell4/gui/grouped_seg_panel.py` — the canonical
  implementation (combo at lines 67-72, `update_channels` at 168-189).
- `src/percell4/gui/segmentation_panel.py` (Cellpose section) —
  copied verbatim in PR #11.
- `src/percell4/interfaces/gui/task_panels/flim_panel.py` — copied
  verbatim in PR #11.

All three share the same widget construction (`QHBoxLayout` with
`QLabel("Channel:")` + `QComboBox`), the same `update_channels` body
(session metadata first, viewer-layer fallback second), and the same
read contract (`combo.currentText() or None`, never writes back to
`session.active_channel`).

Three is the documented extraction threshold. PR #11's plan deferred
extraction with the explicit rule: **the fourth call site triggers
refactor into a shared helper.**

## Guidance

When adding a new task-panel that needs a per-module channel selector,
follow this order:

1. **Don't copy `update_channels` again.** Three copies already exist;
   a fourth pushes the codebase past the extraction threshold this
   convention documents.
2. **Extract first.** Create
   `src/percell4/gui/_channel_combo.py` exporting a small helper
   (matching the `_resource_name_prompt.py` / `_stitching_form.py`
   private-utility convention):

   ```python
   def update_channels_combo(
       combo: QComboBox,
       session,
       viewer_getter,
   ) -> None:
       """Refresh a per-panel channel-override combo from session metadata.

       Session metadata wins; falls back to viewer Image layers when the
       session has no channel_names. Defaults the combo's current text to
       session.active_channel when present.
       """
       ...
   ```

3. **Migrate all three existing sites** in the same PR to consume the
   helper. This is the moment of lowest risk — every consumer is
   identical today, so the migration is a search-and-replace.
4. **Wire the new panel through the launcher's existing
   `_on_layer_selection_changed` callback** at
   `src/percell4/interfaces/gui/main_window.py:619-624`. Don't invent a
   new wire pattern; that callback already drives the existing three
   panels.
5. **Preserve the contract:** combo is a local override only.
   `currentText()` is read at Run time; the combo never writes to
   `session.active_channel`. Re-seed on (a) dataset change via
   `Event.DATASET_CHANGED` subscription (FLIM model) or
   `state_changed.channel` (Cellpose model), and (b) napari
   layer-selection events via the launcher's callback.

## Why This Matters

Each copy is small (22 lines) and trivially correct in isolation, but
the three-way drift class documented in PR #9
(`docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`)
is exactly what bites: any future bug fix or improvement to the
session-metadata vs viewer-layer fallback chain has to be applied three
times, and small differences compound. The grouped panel's
implementation has already been the canonical reference twice; future
fixes there must be propagated mechanically to the other two until
extraction happens.

The "three is the threshold" rule is empirical: at one site, copying
is obviously right; at two sites, copying is still cheap; at three,
you're at the edge of "where did I see this last?" territory. Four is
where the drift cost dominates the extraction cost, full stop.

## When to Apply

- A new task-panel needs a single-channel input.
- A migration from `session.active_channel` direct-read to a per-panel
  override.
- Code review of a PR adding `update_channels` to a new panel — the
  reviewer should ask "should this extract instead?" and the answer
  for the fourth site is yes.

## Examples

**Wrong — fourth copy, drift inevitable:**

```python
# in some new_panel.py
chan_row = QHBoxLayout()
chan_row.addWidget(QLabel("Channel:"))
self._channel_combo = QComboBox()
chan_row.addWidget(self._channel_combo)

def update_channels(self) -> None:
    self._channel_combo.clear()
    session = self.data_model.session
    if session.dataset is not None:
        ch_names = list(session.dataset.metadata.get("channel_names", []))
        for name in ch_names:
            self._channel_combo.addItem(name)
        if session.active_channel:
            self._channel_combo.setCurrentText(session.active_channel)
        if ch_names:
            return
    viewer_win = self._get_viewer_window()
    if viewer_win is None or viewer_win.viewer is None:
        return
    for layer in viewer_win.viewer.layers:
        if layer.__class__.__name__ == "Image":
            self._channel_combo.addItem(layer.name)
```

**Right — extract once, consume four times:**

```python
# in src/percell4/gui/_channel_combo.py
def update_channels_combo(combo, session, viewer_getter):
    ...

# in new_panel.py
self._channel_combo = QComboBox()
chan_row.addWidget(self._channel_combo)

def update_channels(self) -> None:
    update_channels_combo(
        self._channel_combo,
        self.data_model.session,
        self._get_viewer_window,
    )
```

And migrate `grouped_seg_panel.py:168-189`,
`segmentation_panel.py:update_channels`, and
`flim_panel.py:update_channels` to the same helper in the same PR.

## Related

- `src/percell4/gui/grouped_seg_panel.py:67-72, 168-189` — canonical implementation
- `src/percell4/gui/segmentation_panel.py` (Cellpose section, post-PR #11)
- `src/percell4/interfaces/gui/task_panels/flim_panel.py` (post-PR #11)
- `src/percell4/interfaces/gui/main_window.py:619-624, 929-933` — launcher wire
- `docs/plans/2026-05-12-003-feat-channel-override-cellpose-flim-plan.md` — the plan that documented the "fourth site triggers extract" rule
- `docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md` — the parent drift-class learning this convention specializes
- `docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md` — adjacent Qt convention; combos here are *read-only consumers* and don't need signal wiring
