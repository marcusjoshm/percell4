---
audit_unit: U5
date: 2026-05-01
deliverable_of_plan: docs/plans/2026-05-01-refactor-gui-state-handling-audit-plan.md
---

# Keystroke Binding Audit

Every PerCell4 keystroke binding, its scope, preconditions, failure feedback,
and shadowing relationship with napari natives.

## Methodology

Exhaustive grep over `src/percell4/`:

```
grep -rn "setShortcut\|bind_key\|QShortcut\|keyPressEvent\|keymap" src/percell4/
```

Hit count: 8 lines across 3 files.

- `src/percell4/gui/multi_select.py:49` (import only), `:391`, `:393`, `:395`
- `src/percell4/gui/workflows/single_cell/seg_qc.py:38` (import only), `:205`,
  `:207`, `:209`
- `src/percell4/interfaces/gui/main_window.py:129`

No `bind_key`, no `viewer.bind_key`, no `Labels.bind_key`, no
`viewer.keymap`, no custom `keyPressEvent` overrides. PerCell4 ships zero
napari-keymap registrations today. All five active keystroke bindings are
Qt-side (`QAction.setShortcut` x1, `QShortcut` x4 on Multi-select dock,
`QShortcut` x3 on seg-QC dock).

napari natives enumerated from
`.venv/lib/python3.12/site-packages/napari/utils/shortcuts.py` (verified
2026-05-01).

## Summary

- Total PerCell4 keystroke bindings: **8** (1 launcher QAction + 3 Multi-
  select dock QShortcuts + 3 seg-QC dock QShortcuts; the QAction is one
  binding even though it is the focus of Bug B).
- By scope:
  - `Qt.WindowShortcut` (window-scoped) on the launcher: **1** (`M`)
  - `Qt.WindowShortcut` (dock-window-scoped on Multi-select QMainWindow): **3**
  - `Qt.WindowShortcut` (dock-window-scoped on seg-QC QMainWindow): **3**
  - `Qt.ApplicationShortcut`: **0**
  - napari `Labels.class_keymap`: **0** (today; `M` migrates here in U11)
  - napari `viewer.keymap`: **0**
  - napari user keymap: **0**
- Number with napari-native conflict on any keymap level: **3** (`M` on
  Labels; `Esc` on Labels in polygon mode; `Enter` on Labels in polygon
  mode). Of those, the user-reproduced bug class today is **1** (`M`).
  `Esc`/`Enter` are *latent* — they only collide when the napari canvas
  has focus and a Labels layer is active in polygon mode; current PerCell4
  flows do not reach that combination during normal usage, but the
  shadowing surface is identical and is documented for completeness.
- Number satisfying Invariant I3 (exclusive — keystroke claims the event
  even if its handler refuses): **0** today. After U11, **1** (`M`).
- Number satisfying Invariant I4 (visible per-cause feedback on
  precondition failure): **0** today. After U11, **1** (`M`).

## Bindings

| Key | Where (file:line) | Scope | Handler | Preconditions | Feedback | napari-native conflict | I3? | I4? | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `M` | `src/percell4/interfaces/gui/main_window.py:129` (QAction `setShortcut("M")` on `LauncherWindow`) | `Qt.WindowShortcut` on launcher top-level (default for QAction shortcuts) | `LauncherWindow._on_multi_select` (`main_window.py:637-651`) | (a) `get_viewer_window()` returns non-None; (b) inside `launch_multi_select_tool`: a labels layer is active and `is_workflow_locked` is false. None of these are surfaced as separate causes. | Single generic status-bar message: "Multi-select unavailable: no active labels layer or another tool is running" (`main_window.py:648-651`). Coarse — does not distinguish "no labels layer" from "workflow locked". When the napari viewer top-level has focus, the launcher-scoped shortcut never fires and the user sees nothing — napari instead executes its native `napari:new_label` (label id increments silently). | **YES** — `napari:new_label` on `Labels.class_keymap` (`napari/utils/shortcuts.py:43`). Wins over the launcher's `Qt.WindowShortcut` whenever the napari viewer (a separate top-level window) has focus. This is Anchor Bug B. | **NO** — keystroke is shadowed; not exclusive. | **NO** — coarse single-cause message; no visible feedback when shadowed. | **VIOLATION (I3, I4).** Fixed in **U11**: migrate to `Labels.bind_key("M", handler, overwrite=True)`; add per-cause status-bar feedback (no labels layer / workflow locked / viewer not alive); the handler always claims the event. |
| `Ctrl+Return` | `src/percell4/gui/multi_select.py:391-392` (`QShortcut(QKeySequence("Ctrl+Return"), window)`) | `Qt.WindowShortcut` on the Multi-select `QMainWindow` (an independent top-level, `setWindowFlag(Qt.Window)` at `:342`) | `MultiSelectController.accept` | Multi-select dock window must have keyboard focus (Qt routes the WindowShortcut against the active top-level). Click events are interleaved with napari canvas focus during normal use. | None on success (commits selection). On failure to fire (because napari canvas has focus, not the dock), no feedback at all — nothing happens. | **No native conflict** in napari's default shortcut tables (no `Ctrl+Return` / `Ctrl+Enter` registered on Labels, viewer, or user keymap). However, the dock's `Qt.WindowShortcut` is *not* heard when the napari canvas (which lives in a separate top-level QMainWindow) has focus — that is a Qt window-scope issue, not a napari-keymap conflict. Practical effect: the keystroke is silently lost when focus is on napari. | **NO** — keystroke is silently dropped when focus is in the napari window. | **NO** — no feedback on the silent-drop path. | **BORDERLINE.** Documented as a known wart; not in U11's scope. The `(Ctrl+Return)` annotation in the help label (`multi_select.py:358-360`) and the Accept tooltip make the binding's existence visible to users; clicking the Accept button is the always-works fallback. Filed as `todos/030-pending-p2-m-shortcut-unmodified-focus-risk.md` for the broader focus-routing concern. |
| `Ctrl+Enter` | `src/percell4/gui/multi_select.py:393-394` | `Qt.WindowShortcut` on Multi-select dock | `MultiSelectController.accept` (alias for `Ctrl+Return` — keypad Enter compatibility) | Same as `Ctrl+Return`. | Same as `Ctrl+Return`. | None. | NO (focus-scoped). | NO. | Same verdict as `Ctrl+Return`. |
| `Esc` | `src/percell4/gui/multi_select.py:395-396` | `Qt.WindowShortcut` on Multi-select dock | `MultiSelectController.cancel` | Same as `Ctrl+Return`. | None on success (cancels and tears down). On focus-loss path: silent no-op (X-button close at `:399` is the always-works fallback because `_on_close_event` calls `cancel`). | **Latent**: `napari:reset_polygon` on `Labels.class_keymap` (`napari/utils/shortcuts.py:50`) and `napari:finish_drawing_shape` on `Shapes.class_keymap` (`:94`). Multi-select doesn't put napari into polygon mode, so the conflict is not reachable in practice. | NO (focus-scoped). | NO. | **BORDERLINE.** Same focus-routing wart as Ctrl+Return; the X-button gives the user an always-works escape hatch. |
| `Ctrl+Return` | `src/percell4/gui/workflows/single_cell/seg_qc.py:205-206` | `Qt.WindowShortcut` on the seg-QC `QMainWindow` (independent top-level, `Qt.Window` flag at `:177`) | `SegmentationQCController._on_accept_clicked` | seg-QC dock window has keyboard focus. | None on success (advances workflow). On focus-loss: silent no-op. The "Accept & Next" button (visible in the dock) is the always-works fallback. | None. | NO (focus-scoped). | NO. | **BORDERLINE.** Same as Multi-select's Ctrl+Return — fallback button always works. |
| `Ctrl+Enter` | `src/percell4/gui/workflows/single_cell/seg_qc.py:207-208` | `Qt.WindowShortcut` on seg-QC dock | `_on_accept_clicked` (keypad Enter alias) | Same as above. | Same. | None. | NO. | NO. | Same verdict. |
| `Esc` | `src/percell4/gui/workflows/single_cell/seg_qc.py:209-210` | `Qt.WindowShortcut` on seg-QC dock | `_on_cancel_clicked` (with confirmation) | seg-QC dock has focus. | Confirmation dialog on success; nothing on focus-loss path (X-button close at `:213` is the always-works fallback). | **Latent**: `napari:reset_polygon` on `Labels.class_keymap` is reachable in seg-QC because the dock's "Draw New Label" button (`:228-233`) explicitly switches napari into polygon mode. While polygon-drawing is active and the napari canvas has focus, `Esc` will reset the in-progress polygon (a Labels-class binding) instead of cancelling the seg-QC dock. This is intended napari behaviour and the user expects it during polygon drawing; however, an `Esc` press there is silently consumed by napari, which the user may misread as "cancel didn't work." | NO (focus-scoped, with napari shadowing during active polygon draw). | NO (silent shadowing). | **BORDERLINE.** Existing wart; out of scope for the audit's anchor bugs. The X-button confirmation dialog is the always-works cancel path. Filed for future review under the same focus/shadowing thread as Bug B. |

## napari natives in scope

Enumerated from
`.venv/lib/python3.12/site-packages/napari/utils/shortcuts.py:1-117`. Only
keys that PerCell4 binds (or might plausibly bind) are listed; the full
napari table is large.

| Key | napari binding | Layer class / scope | Conflicts with PerCell4 today? |
|---|---|---|---|
| `M` | `napari:new_label` | `Labels.class_keymap` | **YES** — Anchor Bug B; fixed in U11 via `Labels.bind_key`. |
| `Esc` | `napari:reset_polygon` | `Labels.class_keymap` | Latent — only when polygon mode is active. seg-QC reaches this; Multi-select does not. |
| `Esc` | `napari:finish_drawing_shape` | `Shapes.class_keymap` | None (PerCell4 has no Shapes layers). |
| `Enter` | `napari:complete_polygon` | `Labels.class_keymap` | Latent — only with polygon mode. PerCell4's bindings use `Ctrl+Return`/`Ctrl+Enter`, which is *not* napari's `Enter`. |
| `1`–`7`, `E`, `P`, `F`, `L`, `Z`, `B`, `X`, `V` | various Labels mode/action bindings | `Labels.class_keymap` | None — PerCell4 binds none of these today. Listed so future work knows which letters are pre-claimed by napari for Labels layers. |
| `Space`, arrows, `Ctrl+Y`, `Ctrl+R`, `Ctrl+T`, `Ctrl+G`, `Ctrl+E`, `Ctrl+Shift+T`, `Ctrl+Shift+C`, `Ctrl+Alt+/`, `Ctrl+Up/Down`, `Shift+Alt+Up/Down`, `Alt+Up/Down`, `Ctrl+Delete`, `Ctrl+Backspace`, `Shift+V`, `Ctrl+Alt+T` | viewer-level and dim/axis bindings | `Viewer.class_keymap` | None — PerCell4 binds none of these. Listed for future-binding awareness. |

## OQ-4 mechanism — per-key, layered

napari's keymap chain order is verified from
`.venv/lib/python3.12/site-packages/napari/utils/key_bindings.py:367-378`:

```
keymap_chain = [user_keymap, active_layer.keymap,
                active_layer.class_keymap, viewer.keymap,
                viewer.class_keymap]
```

For each PerCell4 keystroke that conflicts with a napari native, choose the
binding level that wins by inserting one level *earlier* in the chain than
the napari native:

- **Layer-class conflict** (e.g., `Labels.class_keymap` for `M`,
  `Esc`-in-polygon-mode, `Enter`-in-polygon-mode) →
  `Labels.bind_key(K, handler, overwrite=True)`. This rewrites the entry
  on `Labels.class_keymap` itself; PerCell4 owns its embedded napari, so
  the rewrite is process-safe. Today: **`M` is the only Labels-class
  binding PerCell4 needs to claim.** U11 wires it.
- **Viewer-level conflict** → `viewer.bind_key(K, handler, overwrite=True)`.
  Today: **none.**
- **Absolute precedence** (need to win regardless of which layer is
  active, including against future plugin-layer class keymaps) →
  `napari.utils.key_bindings.bind_key(K, handler, overwrite=True)` on the
  user keymap (top of the chain). Today: **none.**

`viewer.bind_key("M", overwrite=True)` is **unreachable** while a Labels
layer is active because `Labels.class_keymap` is checked before
`viewer.keymap`. This is why U11 uses `Labels.bind_key` rather than
`viewer.bind_key`.

## Fix references

| Binding | Issue | Fixed in |
|---|---|---|
| `M` | Currently `setShortcut("M")` on a `Qt.WindowShortcut`-scoped launcher QAction; shadowed by napari's `napari:new_label` whenever the napari viewer top-level has focus and a Labels layer is active. | **U11** — `Labels.bind_key("M", handler, overwrite=True)`, plus a new `multi_select_requested` signal on `ViewerWindow`, plus per-cause precondition feedback on the launcher slot. The launcher QAction keeps its menu entry but loses `setShortcut("M")` (the keystroke is now process-wide on Labels layers; the menu invocation continues to route through `_on_multi_select`). |

## Forward note

After **U13** lands,
`docs/solutions/architecture-patterns/napari-modal-tool-overlay-pattern-2026-04-29.md`
guidance #10 — which currently asserts "**M is correct as window-scoped**"
— is **reversed**. The new guidance, captured by U13's net-new canonical
source `docs/solutions/architecture-patterns/keystroke-binding-on-napari-viewer.md`,
will state the OQ-4 mechanism above: per-key, choose the binding level by
which napari keymap the conflict lives on; for Labels-class conflicts use
`Labels.bind_key(K, handler, overwrite=True)`. The reversal is recorded
in this audit so a future reader does not re-derive the obsolete window-
scoped recipe from guidance #10.

## Files this audit lives in

- This document: `docs/audits/keystroke-binding-audit.md`
- Source bindings:
  - `src/percell4/interfaces/gui/main_window.py:129` (M, will be removed in U11)
  - `src/percell4/gui/multi_select.py:391-396` (Ctrl+Return / Ctrl+Enter / Esc)
  - `src/percell4/gui/workflows/single_cell/seg_qc.py:205-210` (Ctrl+Return / Ctrl+Enter / Esc)
- napari natives reference:
  `.venv/lib/python3.12/site-packages/napari/utils/shortcuts.py`
- napari keymap chain reference:
  `.venv/lib/python3.12/site-packages/napari/utils/key_bindings.py:367-378`
- Companion audits:
  - `docs/audits/gui-element-classification.yaml` (cross-references each
    keystroke's owning widget)
  - `docs/audits/session-mutation-graph.md` (U3) — independent of
    keystrokes, but the `M`-handler eventually writes
    `session.filter_ids` via Multi-select Accept; tracked there.
