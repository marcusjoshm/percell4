---
title: "Keystroke binding on the napari viewer — bind on the keymap level above the conflict"
date: 2026-05-01
category: architecture-patterns
module: percell4.gui.viewer, percell4.interfaces.gui.main_window
problem_type: architecture_pattern
component: tooling
canonical_source: src/percell4/gui/viewer.py
applies_to:
  - "src/percell4/gui/viewer.py"
  - "src/percell4/interfaces/gui/main_window.py"
duplicates_at: []
status: pre_canonical
tags:
  - napari
  - qt
  - keystroke
  - bind_key
  - keymap-chain
  - labels-class-keymap
  - multi-select
related_components: [gui, viewer]
symptoms:
  - "A PerCell4 keystroke (e.g., M to open Multi-select) silently fires a napari native action instead (e.g., napari:new_label increments the selected_label)."
  - "viewer.bind_key('M', overwrite=True) does not override the native — because Labels.class_keymap is checked before viewer.keymap in the chain."
  - "Even Labels.bind_key gets clobbered when napari's action_manager re-binds the native at every Labels-layer add."
---

# Keystroke binding on the napari viewer

> **Status: pre_canonical.** Single-keystroke instance today (`M`); promote after one more PerCell4 keystroke is rebound via this pattern.

## Rule

To win a key over a napari native, bind on the keymap level **above** the level where the native lives in napari's chain.

napari's keymap chain order (verified at `napari/utils/key_bindings.py`):

```
[user_keymap, active_layer.keymap, active_layer.class_keymap, viewer.keymap, viewer.class_keymap]
```

Native bindings live at various levels:
- `napari:new_label` (M) — `Labels.class_keymap` (registered via `napari/utils/shortcuts.py:43`).
- Most viewer-level natives — `Viewer.class_keymap`.

To override, bind on the chain entry above the native: `Labels.bind_key` for layer-class natives, `viewer.bind_key` for viewer-level natives, or `napari.utils.key_bindings.bind_key` on the user keymap for absolute precedence.

## The action_manager re-bind pitfall

napari's `action_manager.bind_shortcut` re-binds class natives with `overwrite=True` whenever the action is registered, which happens at every Labels-layer add. A class-level `Labels.bind_key("M", handler, overwrite=True)` registered once at import time gets clobbered the next time a Labels layer is added.

**Defense-in-depth fix:** also call `layer.bind_key("M", handler, overwrite=True)` per Labels-layer instance at add time. Layer-instance keymaps win over class keymaps in the chain, so the per-instance binding survives `action_manager`'s re-bind.

## Canonical example — `M` opens Multi-select

`src/percell4/gui/viewer.py`:

- Once-per-process registration on `Labels.class_keymap` and `Viewer.class_keymap`, guarded by a module-level `_M_BIND_KEY_REGISTERED` flag.
- Per-instance registration on every `Labels` layer added through PerCell4's add helpers.
- A `multi_select_requested = Signal()` on `ViewerWindow` decouples the bind_key handler from the launcher (the viewer does not hold a launcher reference). The launcher subscribes via `subscribe_multi_select_requested` and runs its existing `_on_multi_select` slot.
- Active-`ViewerWindow` resolution: a module-level `WeakSet` of live `ViewerWindow` instances. The handler iterates the set to find the window owning the firing layer. (`napari.layers.Labels` does not expose a viewer back-reference reliably.)

## Precondition feedback (Invariant I4)

The handler always claims the event (returning nothing from a bind_key callback consumes the keystroke). Failure modes do not let the keystroke fall through to the native — they instead surface a per-cause status message:

- "Multi-select unavailable: no labels layer"
- "Multi-select unavailable: another tool is running"
- "Multi-select unavailable: viewer not ready"

The cause string is returned by `ViewerWindow.launch_multi_select_tool` as an enum-like string (`"ok" | "no_labels_layer" | "workflow_locked" | "viewer_not_alive"`); the launcher's `_on_multi_select` translates it to the corresponding status message.

## Anti-pattern

A QAction with `setShortcut("M")` on a non-focused window. When the napari viewer holds focus, Qt routes `M` to napari's keymap chain, the QAction never fires, and napari's native runs — silently incrementing the selected label.

## Detection

```bash
grep -rn "setShortcut\|bind_key\|QShortcut\|keyPressEvent\|keymap" src/percell4/
```

Every PerCell4 keystroke that conflicts with a napari native must bind on the level above the conflict. Dock-window-scoped Qt shortcuts (`Ctrl+Return`, `Esc`) are fine when the dock window has focus — they do not race napari's chain.

## Related

- `docs/audits/keystroke-binding-audit.md` — every PerCell4 binding with shadowing analysis.
- `docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md` — companion (modal-tool lifecycle, install/uninstall ordering).
- `docs/solutions/architecture-patterns/gui-action-contract-exhaustiveness.md` — the Selector/Creator/Action taxonomy that governs which keystrokes write session.
