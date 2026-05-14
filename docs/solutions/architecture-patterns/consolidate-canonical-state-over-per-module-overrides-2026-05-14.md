---
title: Consolidate canonical state at the source, don't dedupe per-module overrides
date: 2026-05-14
category: architecture-patterns
module: percell4.interfaces.gui, percell4.gui, percell4.application
problem_type: architecture_pattern
component: development_workflow
severity: high
applies_when:
  - "Multiple (N ≥ 3) modules grow local override widgets for the same canonical state field"
  - "Local override widgets do not write back to the canonical state — they're consumed at action-time only"
  - "Other features in the codebase read the canonical state directly, creating cross-module dependencies that the overrides silently break"
  - "Users report 'two surfaces show different values for the same thing' or 'the default name doesn't match what I just did'"
  - "An existing audit invariant defines who may write the field, but the override pattern operates in the audit's blind spot"
tags:
  - session-state
  - selector-creator-action
  - canonical-state
  - gui-architecture
  - qt
  - drift-class
  - retire-pattern
related_components:
  - "src/percell4/interfaces/gui/peer_views/session_window.py"
  - "src/percell4/application/session.py"
  - "src/percell4/model.py"
---

# Consolidate canonical state at the source, don't dedupe per-module overrides

## Context

PerCell4 hosts a `Session` in `src/percell4/application/session.py` that owns
five canonical selection fields: `active_channel`, `active_segmentation`,
`active_mask`, `filter_ids`, `selection`. The root `CLAUDE.md` ("GUI state
ownership") and audit invariant **I1** in
`docs/audits/gui-element-classification.yaml` say only **Selectors** and
**Creators** may write these fields; everything else is an **Action** that
reads but never writes Session.

In April 2026 a "per-panel channel-override" pattern landed
(`docs/brainstorms/2026-04-17-channel-selection-session-brainstorm.md`).
Three task panels — `src/percell4/gui/segmentation_panel.py`,
`src/percell4/gui/grouped_seg_panel.py`, and
`src/percell4/interfaces/gui/task_panels/flim_panel.py` — each grew a local
`_channel_combo` that defaulted from `session.active_channel`, was consumed
at Run time as `combo.currentText() or None`, and crucially **never wrote
back to Session**. PR #11 copied the pattern verbatim across the two
non-canonical panels.

The drift signal was loud. By PR #11 there were three near-identical
`update_channels` bodies (session-metadata first, viewer-layer fallback
second). On the morning of 2026-05-13 a learning was written —
[`docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md`](../conventions/panel-channel-override-pattern-2026-05-13.md)
— codifying a "fourth site triggers extract into `_channel_combo.py`" rule.

That afternoon user testing surfaced three failure modes the convention
couldn't fix:

1. **Divergent truth.** FLIM panel override = `mNG`, Data tab still =
   `CA-SiR`. The user could not tell which selection was "real."
2. **Cross-feature leakage.** Phasor Window's "save mask as" default builds
   its name from `session.active_channel`, which was stale relative to the
   FLIM panel override that actually produced the phasor. The mask name
   *lied about what produced it*.
3. **Selection friction beyond channel.** `active_mask` and
   `active_segmentation` had no panel-level affordance at all; switching
   them required Data-tab navigation.

A `/ce-brainstorm`
([`docs/brainstorms/2026-05-13-session-selection-window-requirements.md`](../../brainstorms/2026-05-13-session-selection-window-requirements.md))
reframed the question: *the override pattern itself was the bug*.
Consolidating at the canonical surface — one always-visible `SessionWindow`
owning the three Selectors — was simpler than any extraction. PR #12
(squash `8547629`) shipped it and the convention doc was marked
**superseded** the same day.

The audit's open question OQ-3 ("how do per-module channel/segmentation/mask
Selector dropdowns synchronize with Data-tab Selectors?") was closed by
eliminating per-module Selectors entirely (session history).

## Guidance

When **N modules grow local overrides for the same canonical state**, ask
one question before extracting:

> *Is the right fix to consolidate the overrides (extract a shared helper),
> or to retire the override pattern entirely (move to a canonical
> surface)?*

Favor **canonical-surface consolidation** when any of these hold:

- The local overrides **do not write back** to the canonical state.
- The canonical state is the **source of truth for downstream features**
  (other modules read `session.active_*` directly).
- Cross-module workflows depend on the canonical state agreeing with what
  each module just did.
- The project's audit already names "who may write this field" (PerCell4:
  invariant I1 + the Selector/Creator/Action taxonomy in
  `docs/audits/gui-element-classification.yaml`). Re-reading the invariant
  collapses the decision.

A local-override combo that never writes Session is an **Action
masquerading as a Selector**. That masquerade is the design defect.
Either it should write Session (becoming a real Selector — at which point N
copies of "I'm also a Selector for active_channel" is an I1 problem of a
different shape), or there should only be one Selector for that field.
PerCell4 chose the latter.

**Pattern contrast:**

```python
# WRONG — per-panel local override that doesn't write back.
# Each module grows its own _channel_combo. Run-time reads from the combo,
# Session is never updated. Three modules == three sources of truth.
self._channel_combo = QComboBox()
def update_channels(self):
    self._channel_combo.clear()
    for name in session.dataset.metadata["channel_names"]:
        self._channel_combo.addItem(name)
    if session.active_channel:
        self._channel_combo.setCurrentText(session.active_channel)
# ...later, at Run time:
channel = self._channel_combo.currentText() or None  # never writes session
```

```python
# RIGHT — one canonical Selector site, all modules read Session directly.
# In SessionWindow (the canonical Selector surface):
self._channel_combo.currentTextChanged.connect(self._on_channel_combo_changed)
def _on_channel_combo_changed(self, text):
    if self._loading: return
    self._session.set_active_channel(text or None)

# In every module (FLIM, Cellpose, Grouped Seg):
channel = self.data_model.session.active_channel
```

## Why This Matters

The override pattern manufactured divergent state the user had to track in
their head. The "two surfaces show different values for the same thing"
failure mode is not a UI rough edge; it is the predicted output of a
design where N widgets each hold a private copy of a field that has a
canonical owner.

Downstream features that read the canonical state (Phasor Window's
mask-naming default reads `session.active_channel`) cannot see the
private override, so they produce **silently wrong** outputs — a mask
named for the wrong channel is worse than a crash because it propagates
into saved data.

The "local override" surface area is **deceptively cheap**. Each copy is
22 lines and trivially correct in isolation. The drift cost only becomes
visible when the user observes downstream confusion — by which point the
pattern is entrenched in three modules and a written convention.

Most pointedly: the previously written drift-class learning
([`panel-channel-override-pattern-2026-05-13.md`](../conventions/panel-channel-override-pattern-2026-05-13.md),
lifespan ≈ 8 hours) codified a workaround as a convention. When you find
yourself writing a "fourth site triggers refactor" rule for a
copy-pasted pattern, the prompt should be **"is the design correct?"**
before **"where do I extract?"**. Drift-class learnings are signals to
re-examine the design, not just refactor the copies.

This aligns with the user's persisted preference for lean docs and
aggressive archiving rather than coexisting contradictory rules
(auto memory [claude]).

## When to Apply

Reach for canonical-surface consolidation (not extraction) when:

- **Multiple modules read the same canonical state field**, especially via
  a local override widget.
- **Local overrides don't write back** to the canonical state — they're
  consumed at action-time only.
- **Other features read the canonical state directly**, creating
  cross-module dependencies that the override silently breaks.
- **Existing audit invariants already define "who can write"** the field.
  The invariant tells you whether extracting (N Selectors) or
  consolidating (one Selector) is consistent with the project's stated
  state-handling model.
- **The user can point to specific user-facing confusion** ("two surfaces
  show different values"; "the default name doesn't match what I just
  did"). User-observed divergence is the highest-quality signal that the
  override pattern is the problem.

Conversely, **extract-a-helper is the right move** when the duplicated
code is genuinely Action-shaped (it doesn't claim ownership of canonical
state) and the duplication is purely structural. See the sibling rule at
[`sibling-dialog-extract-shared-widget-2026-05-12.md`](sibling-dialog-extract-shared-widget-2026-05-12.md),
which applies when canonical state lives in a dataclass passed to a
dialog, not in a shared `Session`.

## Examples

**BEFORE — three panels with private overrides + Data-tab Selectors.**
Each of `src/percell4/gui/segmentation_panel.py`,
`src/percell4/gui/grouped_seg_panel.py`, and
`src/percell4/interfaces/gui/task_panels/flim_panel.py` had its own
`_channel_combo` + `update_channels` body. The Launcher's
`_on_layer_selection_changed` callback rebound all three on napari
layer-list events. At Run time each panel read
`combo.currentText() or None` and never wrote Session. The Data tab also
had three "active" combos that *were* Selectors. Four surfaces, one
canonical field, no single source of truth.

**AFTER — one canonical Selector site.**
`src/percell4/interfaces/gui/peer_views/session_window.py` is a wide,
always-on-top, always-visible top-level window that owns three combos.
Each combo wires
`currentTextChanged → session.set_active_channel | set_active_segmentation | set_active_mask`
(and subscribes to the corresponding Session events for echo-safe
round-trip). Module panels were rewritten to read Session directly:

- `flim_panel.py` — `_get_active_channel` now returns
  `self.data_model.session.active_channel`.
- `segmentation_panel.py` — `_on_run_cellpose` reads
  `channel_name = self.data_model.session.active_channel`.
- `grouped_seg_panel.py` — `_on_run` reads
  `channel = self.data_model.session.active_channel`.

The Data tab's three "active" Selector combos were removed (Plan R10–R11);
the tab keeps only resource-management widgets. Audit OQ-3 closed:
invariant I1 holds without the panel-override carve-out, and
`panel-channel-override-pattern-2026-05-13.md` was marked `superseded` with
back-references to PR #12's requirements and plan.

The bug fix was a free consequence — the Phasor Window's
mask-naming default now always agrees with the channel that produced the
phasor, because `session.active_channel` is the only place that value
lives.

## Related

- [`docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md`](../conventions/panel-channel-override-pattern-2026-05-13.md)
  — historical counter-example. The "fourth-site extract" rule that this
  learning explicitly retracts. Already marked superseded with frontmatter
  back-references.
- [`docs/solutions/architecture-patterns/sibling-dialog-extract-shared-widget-2026-05-12.md`](sibling-dialog-extract-shared-widget-2026-05-12.md)
  — sibling rule. Applies when sibling dialogs render the same domain
  widgets and canonical state lives in a dataclass (not Session). Not
  contradicted; the two rules are gated on different signals — extract
  when there is no canonical surface, consolidate when there is.
- [`docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md`](gui-action-contract-exhaustiveness.md)
  — defines the Selector / Creator / Action taxonomy that `SessionWindow`
  instantiates.
- [`docs/solutions/architecture-patterns/session-to-napari-one-way-push.md`](session-to-napari-one-way-push.md)
  — companion rule on canonical-state ownership; Session is the canonical
  truth, view-layer surfaces (here: napari) may not write back.
- [`docs/brainstorms/2026-05-13-session-selection-window-requirements.md`](../../brainstorms/2026-05-13-session-selection-window-requirements.md)
  — the requirements doc that argued for consolidation.
- [`docs/plans/2026-05-13-001-feat-session-selection-window-plan.md`](../../plans/2026-05-13-001-feat-session-selection-window-plan.md)
  — the 5-unit plan that delivered PR #12.
- PR #12 (squash `8547629` on `main`, merged 2026-05-14).
