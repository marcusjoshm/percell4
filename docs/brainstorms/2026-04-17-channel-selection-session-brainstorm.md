---
topic: Channel selection via Session + Data tab
date: 2026-04-17
status: decided
---

# Channel Selection via Session + Data Tab

## What We're Building

Add `active_channel` to the Session (same pattern as `active_segmentation` and `active_mask`) so that analysis operations read the selected channel from the Session instead of reaching into the napari viewer.

The Data tab's channel display (currently a read-only label) becomes a QComboBox that drives `session.set_active_channel()`. Panels that need a different channel for a specific operation can add a local override combo that defaults to the session value.

## Why This Approach

The hex refactor moved segmentation and mask selection into the Session but did not move channel selection. Every operation that needs a channel still reaches into napari (`viewer.layers.selection.active`), which is the exact "viewer as source of truth" anti-pattern the refactor was designed to eliminate.

The gap is visible: 7 operations are napari-coupled for channel selection (threshold, phasor, wavelet, lifetime, segmentation, grouped thresholding, threshold accept). Only the batch workflow and CLI got it right by using plain strings.

## Key Decisions

1. **Single active channel + per-panel override.** Session tracks one `_active_channel`. Individual panels can override with a local dropdown if a specific operation needs a different channel. Most panels just use the global.

2. **First channel is the default.** When a dataset loads, `active_channel` is set to `channel_names[0]` from metadata. No "force selection" step.

3. **One-way sync (Data tab only).** Only the Data tab QComboBox drives the Session. Clicking a channel layer in napari does NOT update the Session's active channel. This avoids napari event complexity and keeps the Session as the single authority.

4. **Use cases read from Session.** `ComputePhasor`, `ApplyWavelet`, `ComputeLifetime`, threshold preview, and segmentation all read `session.active_channel` instead of `get_active_channel()` from the viewer. The channel string is enough to look up data from the repository.

## Scope

### Session changes (~20 lines)
- Add `_active_channel: ChannelName | None` field
- Add `active_channel` property
- Add `set_active_channel(name)` mutator
- Add `ACTIVE_CHANNEL_CHANGED` event
- `set_dataset()` auto-sets active_channel to first channel from metadata
- `clear()` resets active_channel to None

### Data tab changes (~30 lines)
- Promote `_data_channel_label` (QLabel) to `_active_channel_combo` (QComboBox)
- Populate from `handle.metadata["channel_names"]` on dataset load
- Wire `currentTextChanged` to `session.set_active_channel()`
- Subscribe to `ACTIVE_CHANNEL_CHANGED` to stay in sync

### Use case / panel changes (~50 lines total)
- FlimPanel: `_get_active_channel()` reads from `session.active_channel` instead of viewer
- AnalysisPanel: threshold preview reads `session.active_channel`
- SegmentationPanel: reads `session.active_channel` for Cellpose input
- GroupedSegPanel: local channel combo defaults to `session.active_channel` but can be overridden

### LoadDataset use case (~5 lines)
- After setting dataset on session, set active_channel to first channel name

### Tests (~40 lines)
- Session test: set_active_channel emits event, clear resets it
- DataPanel test: combo drives session

## What This Does NOT Do

- Does not add multi-channel selection (pick several channels at once). That's a future feature if measurement needs it.
- Does not sync napari → Session for channel clicks. One-way only.
- Does not change how `MeasureCells` works (it measures ALL channels; no selection needed there).
- Does not touch the batch workflow (it already manages channels correctly via config strings).

## Open Questions

None remaining. All resolved during brainstorm.
