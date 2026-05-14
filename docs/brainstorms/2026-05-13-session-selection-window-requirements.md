---
date: 2026-05-13
topic: session-selection-window
status: requirements
---

# Always-Visible Session-Selection Window

## Problem Frame

Today the canonical Selectors for `session.active_channel`, `session.active_segmentation`, and `session.active_mask` live on the Data tab of the Launcher window. Three task panels (FLIM, Cellpose, Grouped Segmentation) have also grown a per-panel *channel-override* dropdown that does NOT write back to Session — it is a local read-only override consumed at Run time.

Real-world use surfaces three failure modes:

1. **Divergent truth.** A user opens the FLIM panel, picks `mNG` in the panel override, then computes the phasor. The Data tab still says `CA-SiR`. The user cannot tell which selection is "real."
2. **Cross-feature leakage.** Module overrides are invisible outside the module that owns them. Concrete repro: FLIM panel override = `mNG` → compute phasor → in the Phasor Window, save mask as. The default mask name is built from `session.active_channel` which is still `CA-SiR`. The mask name lies about what produced it.
3. **Selection friction beyond channel.** `active_mask` and `active_segmentation` have no panel-level affordance at all. To switch them, the user must navigate to the Data tab. Acceptable when Launcher and Phasor Window can sit side-by-side; bad on smaller screens.

These are direct consequences of the panel-override pattern decided in `docs/brainstorms/2026-04-17-channel-selection-session-brainstorm.md` and partially implemented through PR #11. The GUI state-handling audit already flagged this synchronization story as unresolved (`docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md` OQ-3).

This brainstorm retires the panel-override model and replaces it with a single canonical Session-selection surface that is always reachable without tab navigation.

---

## Requirements

**Session-selection window**
- R1. A dedicated top-level Qt window — the **Session window** — owns the canonical Selectors for `session.active_channel`, `session.active_segmentation`, and `session.active_mask`. It is a sibling of the Launcher, Phasor Window, and Viewer, opened automatically on application launch.
- R2. The window is wide and short — designed to sit pinned at the top edge of the screen, spanning enough width that the three combos sit in a single horizontal row alongside a dataset-name header and the "Pin on top" toggle.
- R3. The window stays above other PerCell4 windows by default (Qt `WindowStaysOnTopHint`). A visible "Pin on top" toggle in the window lets the user disable this behavior for the current session; the setting is remembered across launches.
- R4. The three combos are populated from Session — channels from `session.dataset.metadata["channel_names"]`, segmentations and masks from the Session's resource lists. The window subscribes to `state_changed` and `Event.DATASET_CHANGED` so its combos always reflect Session truth.
- R5. Changing any of the three combos calls `session.set_active_channel | set_active_segmentation | set_active_mask`. The window is the canonical Selector site under audit invariant I1.
- R6. The window remembers its last geometry (size + screen position) between launches.

**Module behavior**
- R7. The per-panel `_channel_combo` widgets in `src/percell4/gui/segmentation_panel.py`, `src/percell4/gui/grouped_seg_panel.py`, and `src/percell4/interfaces/gui/task_panels/flim_panel.py` are removed. Each panel reads `session.active_channel` directly at Run time.
- R8. No module introduces its own active-selection override for channel, mask, or segmentation. The Session window is the only surface that can mutate the three active fields.
- R9. The Phasor Window's "save mask as" default name continues to derive from `session.active_channel`, which now always reflects what was used to produce the phasor.

**Data tab**
- R10. The three "active" combos at the top of the Data tab (`_active_channel_combo`, `_active_seg_combo`, `_active_mask_combo`) are removed.
- R11. The Data tab retains all management widgets — rename combos, delete buttons, list views — and the dataset metadata display. Its remaining role is *managing* resources, not *selecting* the active one.

**Compatibility**
- R12. Lifecycle behavior of `Session.set_dataset` is unchanged: first available channel, first segmentation, and first mask are auto-selected on dataset load. The Session window reflects those defaults on open.
- R13. Creator behavior is unchanged: when a Creator writes a new mask/segmentation, it auto-selects that resource. The Session window makes flipping back to the previous selection trivial.

---

## Success Criteria

- **User outcome.** A user can switch active channel, mask, or segmentation from any window state without navigating to the Data tab. Each switch propagates immediately to every consumer (FLIM panel, Phasor Window, mask-naming, etc.) and is reflected back in the Session window.
- **Failure-mode retirement.** The three failure modes in the problem frame are unreproducible:
  - No surface in the app shows a different active channel than another surface at the same instant.
  - The Phasor Window's default mask name never disagrees with the channel that produced the phasor.
  - Switching active mask or segmentation never requires tab navigation.
- **Audit handoff.** OQ-3 in the GUI state-handling audit is resolved by R7/R8: there are no module-level Selectors for `active_channel | active_segmentation | active_mask`. The audit's I1 invariant holds without the panel-override carve-out.
- **Downstream agent handoff.** `docs/audits/gui-element-classification.yaml`, `docs/audits/session-mutation-graph.md`, and the per-module panel CLAUDE.md files reflect the new model. The superseded learning at `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md` is marked superseded with a back-reference to this requirements document.

---

## Scope Boundaries

- **No transient "use this just for this run" override.** Considered and rejected during brainstorm. Adds two-source-of-truth complexity that the new model exists to retire.
- **No per-module independent state.** The Session window is the canonical Selector. Modules read from Session; they do not own their own selection.
- **`filter_ids` and `selection` are not in the Session window.** They are operational state written by phasor ROI filters and selection workflows, not user-pickable from a dropdown.
- **napari layer-list clicks remain forbidden as Session writers** (unchanged from current CLAUDE.md rule).
- **Audit OQ-1, OQ-2, OQ-4 are not addressed here.** Those remain open in the GUI state-handling audit.
- **Multi-channel selection (pick several channels at once) is not introduced.** Single active channel only, same as today.
- **The Data tab management widgets are not redesigned.** They stay as-is; only the active Selectors leave.

---

## Key Decisions

- **One canonical truth, no overrides.** The April 2026 "per-panel override that doesn't write back" model is retired. Rationale: the user-observed failure modes are direct consequences of that pattern; no observed workflow needs per-module deviation that couldn't be served by a fast canonical switch.
- **Top-level window, not toolbar.** Considered embedding as a Launcher toolbar but rejected: a wide always-on-top window lets the user dock it visually at the top of the screen across monitor layouts, and is independent of which window currently has focus.
- **Pin on top is user-toggleable but on by default.** Always-on-top is the friction-killer; the toggle is an escape hatch for occasional "I need to see what's behind it" cases.
- **Wide horizontal layout.** Three combos + dataset name + pin toggle in one row. Minimal vertical footprint means the window covers almost nothing at the screen's top edge.

---

## Dependencies / Assumptions

- `Qt.WindowStaysOnTopHint` works on macOS (PerCell4's primary platform) within the application and across most other applications. *Standard Qt behavior; verified by widespread use; minor macOS-specific nuances around minimize/restore that the planning phase should test.*
- `Session.set_active_channel | set_active_segmentation | set_active_mask` emit appropriate `state_changed` flags today. *Verified at `src/percell4/application/session.py:202-217`.*
- All three module panels currently read `session.active_channel` via either their `_channel_combo` (override path) or directly. After R7 they read `session.active_channel` directly — the Run-time read site already exists in every panel. *Verified for FLIM (`flim_panel.py:_get_active_channel`) and segmentation panels.*
- The Phasor Window's mask-naming default reads `session.active_channel`. *Inferred from problem-frame failure mode reported by the user; planning should verify the exact read site in `src/percell4/interfaces/gui/peer_views/phasor_plot.py`.*
- The Launcher's `_on_layer_selection_changed` callback at `src/percell4/interfaces/gui/main_window.py:619-624` currently drives the per-panel `update_channels` rebind. After R7 there are no panel combos to rebind, so this callback's responsibilities collapse to driving the Session window's combos (or is dropped entirely if the window subscribes directly to `Event.DATASET_CHANGED`). Planning resolves which.

---

## Outstanding Questions

### Deferred to Planning

- [Affects R6][Technical] Where is the right place to persist Session window geometry — `QSettings` per the Qt convention, or a project-local config file? PerCell4 has no precedent for either today; planning picks one.
- [Affects R10][Technical] Should the Data tab's three Selector rows be deleted outright or replaced with a non-interactive "current selection" read-out as a discoverability bridge during the transition? Planning decides based on how disruptive removal is.
- [Affects R7][Technical] Does `src/percell4/gui/_channel_combo.py` (the helper anticipated by the now-superseded panel-channel-override-pattern learning) need to be created at all, or is its non-creation the correct outcome? Answer: probably not created — the helper's purpose was to dedupe an override pattern this brainstorm retires.
- [Affects R3][Needs research] On macOS, does `WindowStaysOnTopHint` survive Mission Control / virtual desktop transitions cleanly for a non-modal window? Planning should test on the target platform.

---

## Next Steps

-> `/ce-plan` for structured implementation planning. Suggested sequencing for the plan: (1) Build the Session window with the three Selectors and pin-on-top toggle; (2) Remove the three panel `_channel_combo` widgets and rewire panels to read `session.active_channel` directly; (3) Remove the three Data-tab "active" combos; (4) Update audit artifacts (`gui-element-classification.yaml`, `session-mutation-graph.md`) and supersede `docs/solutions/conventions/panel-channel-override-pattern-2026-05-13.md`.
