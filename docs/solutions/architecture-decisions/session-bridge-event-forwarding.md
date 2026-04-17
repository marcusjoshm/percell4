---
title: "New Session events must be bridged through CellDataModel"
category: architecture-decisions
tags: [session, celldata-model, qt-signals, state-change, event-bridge, hexagonal-architecture]
module: model, application/session
symptom: "Changing active channel in Data tab combo did not update Segmentation or Analysis panel channel labels until clicking the napari viewer window"
root_cause: "Session emitted ACTIVE_CHANNEL_CHANGED but CellDataModel never subscribed to it or forwarded it as a StateChange. Panels subscribing to state_changed only saw channel updates on dataset load (change.data), not on user-initiated channel switches."
severity: medium
date: 2026-04-17
---

# New Session Events Must Be Bridged Through CellDataModel

## Problem

After adding `active_channel` to the Session with an `ACTIVE_CHANNEL_CHANGED` event, changing the channel in the Data tab's QComboBox correctly updated `session.active_channel` but panels (Segmentation, Analysis) did not react. Their channel labels stayed stale until an unrelated `state_changed` signal fired (e.g., clicking in napari).

## Root Cause

The hex architecture has two event systems running in parallel:

1. **Session events** (pure Python, `Event` enum, `subscribe()` pattern) — used by the application layer and peer views
2. **CellDataModel `state_changed` signal** (Qt `Signal(object)`, carries `StateChange` dataclass) — used by legacy panels and the launcher

CellDataModel acts as a bridge: it subscribes to Session events and re-emits them as Qt signals. But when `ACTIVE_CHANNEL_CHANGED` was added to the Session, the bridge was not updated. The event fired in Session but never reached Qt-subscribed panels.

```
Session.set_active_channel("GFP")
  → Session._emit(Event.ACTIVE_CHANNEL_CHANGED)
    → [nobody listening in CellDataModel]  ← BUG: bridge missing
    → Panels never see the change
```

## Solution

Three changes, each ~2 lines:

### 1. Add `channel` field to `StateChange` (model.py)

```python
@dataclass
class StateChange:
    data: bool = False
    selection: bool = False
    filter: bool = False
    segmentation: bool = False
    mask: bool = False
    channel: bool = False       # NEW
```

### 2. Subscribe + forward in CellDataModel (model.py)

```python
# In __init__:
self._session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, self._on_channel_changed)

# New handler:
def _on_channel_changed(self) -> None:
    if not self._wiring_session:
        self.state_changed.emit(StateChange(channel=True))
```

### 3. Respond in panels

```python
# In any panel's _on_state_changed:
def _on_state_changed(self, change) -> None:
    if change.data or change.channel:  # ADD change.channel
        self.update_channel_label()
```

## Prevention Rules

When adding a new event to Session:

1. Add the `Event` enum member to `application/session.py`
2. Add the corresponding `bool` field to `StateChange` in `model.py`
3. Subscribe to the new event in `CellDataModel.__init__`
4. Add a `_on_*_changed` handler that emits `StateChange` with the new field
5. Update all panel `_on_state_changed` methods that should respond

**Grep check after adding a Session event:**
```bash
# Verify the bridge exists:
grep "ACTIVE_CHANNEL_CHANGED" src/percell4/model.py
# Should show both subscribe() and handler

# Verify StateChange has the field:
grep "channel" src/percell4/model.py
# Should show the dataclass field
```

## Why Two Event Systems Exist

The Session uses pure Python observers (no Qt dependency) so that `application/` and `domain/` remain Qt-free (enforced by import-linter). The CellDataModel bridge exists because legacy panels, the launcher, and workflow runners subscribe to Qt signals. This dual system is transitional — as panels migrate to Session-direct subscription, the bridge becomes unnecessary.

## Related Documentation

- `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md` — the panel extraction pattern that depends on state_changed
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` — similar class of bug where an event path was missing
- `docs/brainstorms/2026-04-17-channel-selection-session-brainstorm.md` — the feature that exposed this gap
