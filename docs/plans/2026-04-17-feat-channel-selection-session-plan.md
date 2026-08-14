---
title: "feat: Add channel selection to Session + Data tab"
type: feat
date: 2026-04-17
brainstorm: docs/brainstorms/2026-04-17-channel-selection-session-brainstorm.md
---

# feat: Add channel selection to Session + Data tab

## Overview

Add `active_channel` to the Session (same pattern as `active_segmentation` and `active_mask`). Promote the Data tab's read-only channel label to a QComboBox. Use cases read `session.active_channel` instead of reaching into the napari viewer. ~145 lines across 6 files.

## Problem

7 operations reach into the napari viewer to get the active channel (`viewer.layers.selection.active`). This is the "viewer as source of truth" anti-pattern the hex refactor was designed to eliminate. Segmentation and mask selection were moved to the Session; channels were not.

## Implementation

### 1. Session: add active_channel (~20 lines)

`src/percell4/application/session.py`

```python
# New Event member (line ~25)
ACTIVE_CHANNEL_CHANGED = auto()

# New field (line ~48)
_active_channel: ChannelName | None = field(default=None, repr=False)

# New property (after active_mask property, ~line 93)
@property
def active_channel(self) -> ChannelName | None:
    return self._active_channel

# New mutator (after set_active_mask, ~line 166)
def set_active_channel(self, name: ChannelName | None) -> None:
    if name == self._active_channel:
        return
    self._active_channel = name
    self._emit(Event.ACTIVE_CHANNEL_CHANGED)

# Update set_dataset (~line 120): auto-select first channel
def set_dataset(self, handle: DatasetHandle | None) -> None:
    self._dataset = handle
    self._active_segmentation = None
    self._active_mask = None
    self._active_channel = None  # ADD
    # ... existing reset code ...
    self._emit(Event.DATASET_CHANGED)
    # Auto-select first channel from metadata
    if handle is not None:
        ch_names = handle.metadata.get("channel_names", [])
        if ch_names:
            self.set_active_channel(ch_names[0])

# Update clear (~line 168)
def clear(self) -> None:
    # ... existing reset ...
    self._active_channel = None  # ADD
```

### 2. Data tab: channel QComboBox (~30 lines)

`src/percell4/interfaces/gui/task_panels/data_panel.py`

Replace the read-only `_data_channel_label` (QLabel) with `_active_channel_combo` (QComboBox):

```python
# In _build_ui, replace lines 70-78:
chan_row = QHBoxLayout()
chan_row.addWidget(QLabel("Active Channel:"))
self._active_channel_combo = QComboBox()
self._active_channel_combo.setPlaceholderText("None")
self._active_channel_combo.currentTextChanged.connect(
    self._on_active_channel_combo_changed
)
chan_row.addWidget(self._active_channel_combo)
layers_layout.addLayout(chan_row)

# New handler:
def _on_active_channel_combo_changed(self, name: str) -> None:
    if name:
        self.data_model.session.set_active_channel(name)

# In _on_state_changed, add:
if change.dataset:  # or subscribe to DATASET_CHANGED
    self._populate_channel_combo()

# New method:
def _populate_channel_combo(self) -> None:
    self._active_channel_combo.blockSignals(True)
    self._active_channel_combo.clear()
    session = self.data_model.session
    if session.dataset is not None:
        ch_names = session.dataset.metadata.get("channel_names", [])
        for name in ch_names:
            self._active_channel_combo.addItem(name)
        if session.active_channel:
            self._active_channel_combo.setCurrentText(session.active_channel)
    self._active_channel_combo.blockSignals(False)

# Update clear_ui:
def clear_ui(self) -> None:
    # ... existing clears ...
    self._active_channel_combo.blockSignals(True)
    self._active_channel_combo.clear()
    self._active_channel_combo.blockSignals(False)

# Delete update_channel_label() — no longer needed
```

### 3. FlimPanel: read from Session (~10 lines)

`src/percell4/interfaces/gui/task_panels/flim_panel.py`

Replace `_get_active_channel()` viewer lookup with Session read:

```python
def _get_active_channel(self) -> str | None:
    return self.data_model.session.active_channel
```

Delete the `get_viewer_window` callback usage for channel detection. The `get_viewer_window` callback is still needed for adding lifetime layers to the viewer.

### 4. AnalysisPanel: read from Session (~10 lines)

`src/percell4/interfaces/gui/task_panels/analysis_panel.py`

In `_on_threshold_preview`, replace:
```python
# BEFORE: reach into viewer
active = viewer_win.viewer.layers.selection.active
if active is None or active.__class__.__name__ != "Image":
    ...
image = active.data.astype(np.float32)
channel_name = active.name
```
With:
```python
# AFTER: read from session, get data from repo
channel_name = self.data_model.session.active_channel
if not channel_name:
    self._show_status("Select a channel in the Data tab first")
    return
repo = self._get_repo()
handle = self.data_model.session.dataset
images = repo.read_channel_images(handle)
if channel_name not in images:
    self._show_status(f"Channel '{channel_name}' not found")
    return
image = images[channel_name]
```

### 5. LoadDataset use case: set first channel (~3 lines)

`src/percell4/application/use_cases/load_dataset.py`

After `self._session.set_dataset(handle)`, the Session's `set_dataset` method now auto-selects the first channel. No changes needed in the use case itself — the Session handles it.

### 6. Main window: remove channel label update (~10 lines)

`src/percell4/interfaces/gui/main_window.py`

- Remove or simplify `_update_active_channel_label()` — the Data tab combo now owns this
- Remove channel label update calls from `_wire_viewer_layer_selection` and `_populate_viewer_from_store`

### 7. CellDataModel bridge: forward channel event (~5 lines)

`src/percell4/model.py`

Add `ACTIVE_CHANNEL_CHANGED` to the bridge so the `state_changed` signal carries channel changes for any remaining subscribers.

### 8. Tests (~40 lines)

`tests/test_session.py`

```python
class TestSessionActiveChannel:
    def test_set_active_channel_emits_event(self, session):
        events = []
        session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, lambda: events.append(1))
        session.set_active_channel("GFP")
        assert len(events) == 1
        assert session.active_channel == "GFP"

    def test_same_channel_no_event(self, session):
        session.set_active_channel("GFP")
        events = []
        session.subscribe(Event.ACTIVE_CHANNEL_CHANGED, lambda: events.append(1))
        session.set_active_channel("GFP")
        assert len(events) == 0

    def test_set_dataset_auto_selects_first_channel(self, session):
        handle = DatasetHandle(path=Path("/tmp/test.h5"), metadata={"channel_names": ["GFP", "DAPI"]})
        session.set_dataset(handle)
        assert session.active_channel == "GFP"

    def test_clear_resets_channel(self, session):
        session.set_active_channel("GFP")
        session.clear()
        assert session.active_channel is None
```

## Acceptance Criteria

- [x] `session.active_channel` returns the selected channel name
- [x] `session.set_active_channel()` emits `ACTIVE_CHANNEL_CHANGED` event
- [x] Loading a dataset auto-selects the first channel
- [x] Data tab shows a channel QComboBox (not a read-only label)
- [x] Changing the Data tab combo updates `session.active_channel`
- [x] FlimPanel reads channel from Session, not viewer
- [x] AnalysisPanel threshold preview reads channel from Session, not viewer
- [x] CLI pipeline still works (doesn't depend on the new Session state)
- [x] All existing tests pass

## References

- Brainstorm: `docs/brainstorms/2026-04-17-channel-selection-session-brainstorm.md`
- Pattern to follow: `session.set_active_segmentation` / `set_active_mask` in `application/session.py:156-166`
- Data tab combo pattern: `data_panel.py:80-88` (active seg combo)
- Signal blocking pattern: `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` (combo repopulation must use `blockSignals`)
