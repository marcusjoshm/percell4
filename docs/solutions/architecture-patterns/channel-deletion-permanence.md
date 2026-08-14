---
title: "Permanent channel deletion writes /intensity, updates /metadata, drops derived FLIM artifacts"
date: 2026-04-30
category: architecture-patterns
module: percell4.interfaces.gui.task_panels, percell4.store
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/interfaces/gui/task_panels/data_panel.py
applies_to:
  - "src/percell4/interfaces/gui/task_panels/data_panel.py"
  - "any future channel-deletion entry point"
  - "src/percell4/store.py rename_channel (sibling permanence pattern)"
duplicates_at: []
status: pre_canonical
tags:
  - hdf5
  - channel
  - deletion
  - permanence
  - flim
  - thread-1
related_components: [io, flim, gui]
symptoms:
  - "Pre-Thread-1 Data tab deleted channels in-memory only — napari layer was removed but /intensity, /metadata.channel_names, /decay/<ch>, /phasor/<ch>, FLIM cal attrs were untouched."
  - "Restarting the app re-loaded the channel from disk; deletion did not persist."
---

# Channel-deletion permanence

> **Status: pre_canonical.** Single callsite today (`data_panel.py::_on_delete_channel`); promote to a use case (`use_cases/delete_channel.py`) so future entry points (e.g., compress dialog post-import cleanup, CLI `percell delete-channel`) consume the same logic.

## What "permanent deletion" means

Deleting channel `<name>` from a dataset must, in one transaction:

1. **Remove the slice from `/intensity`** — re-write `/intensity` with the slice indexed by `channel_names.index(name)` removed (or delete the array if it was the last channel; for 2D single-channel data, delete `/intensity` outright).
2. **Update `/metadata`** — write `channel_names` with the entry removed, update `n_channels`.
3. **Drop derived FLIM artifacts** — `/decay/<name>`, `/phasor/<name>`, `/provenance/decay/<name>`. Drop without raising if absent.
4. **Drop FLIM calibration metadata** — `flim_cal_phase_<name>`, `flim_cal_mod_<name>` from `/metadata.attrs`.
5. **Sync the `Session.dataset.metadata` snapshot** — frozen handle, mutable dict; update `channel_names`, `n_channels`, and the FLIM cal keys in-place. (See `docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md` Vector 3 — frozen-handle dict mutation pattern.)
6. **Clear `Session.active_channel`** if it pointed at the deleted name.
7. **Remove the napari layer** matching the channel name.

The current canonical: `src/percell4/interfaces/gui/task_panels/data_panel.py::_on_delete_channel` (lines 421-565).

## Thread 1 commits

- `06221ec`, `1e5b30f`, `60b20bb`, `d6e18de` — incremental fixes that built up the permanence behavior. Pre-Thread-1, the Data tab handler removed the napari layer only; the steps above were missing or partial.

## What was already canonical (and not reused)

The compress-side import path already wrote `/intensity` + `/metadata.channel_names` atomically when channels were initially imported. The deletion path re-discovered that semantics from scratch instead of factoring it into a shared helper. This is a re-implementation pattern the audit lens is meant to catch.

## Where it should live

A pure-Python use case in `src/percell4/application/use_cases/delete_channel.py`:

```python
class DeleteChannel:
    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        ...
    def execute(self, channel_name: str) -> DeleteChannelResult:
        # 7 steps above
```

GUI handler (`data_panel._on_delete_channel`) reduces to: confirm dialog → call use case → refresh napari layers → status message.

## Sibling: channel rename permanence

`store.DatasetStore.rename_channel` (lines 440-469) implements the rename equivalent — moves `/decay/<old> → /decay/<new>` and `/phasor/<old> → /phasor/<new>`, renames `flim_cal_phase_*` / `flim_cal_mod_*` attrs, updates `channel_names`. The deletion path follows the same shape but without the rename target. They should share helpers.

## Reuse rule

> Any future "make channel `<name>` go away" entry point MUST go through the canonical use case (once promoted) or replicate every one of the 7 steps. Skipping `/intensity` slice removal or FLIM cal drop is the bug class Thread 1 surfaced; do NOT re-implement the GUI-layer-only deletion path.
