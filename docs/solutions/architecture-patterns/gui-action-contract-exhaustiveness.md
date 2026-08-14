---
title: "GUI Action contracts are exhaustive — buttons do exactly what their label says"
date: 2026-05-01
category: architecture-patterns
module: percell4.interfaces.gui, percell4.gui
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/interfaces/gui/peer_views/phasor_plot.py
applies_to:
  - "src/percell4/interfaces/gui/**/*.py"
  - "src/percell4/gui/**/*.py"
duplicates_at: []
status: pre_canonical
tags:
  - gui
  - state-ownership
  - action-contract
  - selector-creator-action
  - audit
related_components: [gui, application]
symptoms:
  - "A button silently mutates state outside its advertised behavior — for example, the phasor Remove ROI button silently cleared session.active_mask, which auto-unchecked Filter by active mask and refused re-engagement."
  - "Users see different behavior from the same button depending on click order or context."
  - "'Fixing' a feature requires clicking unrelated buttons (e.g., Clear Selection enables Multi-select via undocumented side effect)."
---

# Action contracts are exhaustive

> **Status: pre_canonical.** Codifies the post-audit rule. Detection is mechanical (grep against the audit's mutation graph); promotion to canonical happens after one or two more Action audits land cleanly.

## Rule

Every interactive UI element belongs to exactly one of three classes:

| Class        | Reads session | Writes session | Writes new resources |
|--------------|---------------|---------------|----------------------|
| **Selector** | yes           | yes (`active_*`, `filter_ids`, `selection`) | no |
| **Creator**  | yes           | yes (auto-selects newly written resource) | yes |
| **Action**   | yes           | **no**        | no                   |

An **Action** does what its label says. Hidden side effects on session state, viewer state, or sibling-window state are forbidden. If a button needs to update other widgets, it does so via the `state_changed` subscriber chain on whatever it legitimately writes — never by reaching across module boundaries to mutate state.

The five session selection fields are: `active_channel`, `active_segmentation`, `active_mask`, `filter_ids`, `selection`.

## Canonical example — phasor Remove ROI

`src/percell4/interfaces/gui/peer_views/phasor_plot.py` — the Remove button (`_on_remove_roi`) has a strict two-part contract:

1. Remove the ROI graphic from the histogram.
2. Remove the `_phasor_roi_preview` napari layer (downstream of the `preview_mask_ready` signal chain).

Nothing else. The Selected ROI panel widgets and status bar are subscribers; they rebind via `_on_roi_list_selection` (when `_selected_roi_index is None`) and via `_refresh_histogram` (when the last ROI is removed) — not by Remove itself reaching into them.

## Anti-pattern (the Bug A regression)

Pre-fix `_on_remove_roi` contained:

```python
self._session.set_active_mask(None)
```

This single off-label session write cascaded through the active-mask subscriber chain, auto-unchecked `Filter by active mask`, and left the histogram visually mask-filtered while the checkbox said otherwise. The Selected ROI panel and status bar were independently stale because their rebind logic did not observe ROI removal.

## Detection

Mechanical grep:

```bash
grep -rn "session\.set_active_\|session\.set_filter\|session\.set_selection\|data_model\.set_active_\|data_model\.set_filter\|data_model\.set_selection" src/percell4/
```

Every hit must trace to a Selector or Creator, never an Action. The audit produces `docs/audits/session-mutation-graph.md` as the running registry; widgets that violate the rule are listed there with their fix unit.

## When to apply

- Adding a new button, menu entry, keystroke handler, or callback in any GUI file.
- Reviewing a PR that touches `src/percell4/interfaces/gui/` or `src/percell4/gui/`.
- Investigating any "this button has different behavior depending on context" bug.

## Related

- `docs/solutions/architecture-patterns/creator-contract-four-step-sequence-2026-05-18.md` — companion: the **Creator** class's four-step contract (store, viewer, refresh, set_active) and the Qt-free-use-case / Qt-aware-caller split. Read after this doc for the per-class detail.
- `docs/audits/gui-element-classification.yaml` — full inventory.
- `docs/audits/session-mutation-graph.md` — every writer of the five session fields.
- `docs/audits/subscriber-rebind-matrix.md` — every consumer of session-derived state.
- `docs/solutions/architecture-patterns/keystroke-binding-on-napari-viewer.md` — companion for keystroke routing.
- `docs/solutions/architecture-patterns/session-to-napari-one-way-push.md` — companion for napari coupling.
