---
title: Phasor plot — Clear within ROI (manual exclusion filter)
status: open
created: 2026-05-04
type: feature-requirements
related:
  - docs/brainstorms/2026-04-30-phasor-mask-filter-requirements.md
  - docs/brainstorms/2026-04-17-phasor-roi-separate-masks-brainstorm.md
  - docs/brainstorms/2026-03-27-cross-window-selection-filtering-brainstorm.md
---

# Phasor plot — Clear within ROI (manual exclusion filter)

## Problem

The phasor plot frequently contains small off-cluster populations (e.g., lysosomes appearing as a faint cluster to the right of the dominant cytoplasmic cluster) that the user wants excluded from downstream analysis. Today there is no way to subtract a region of phasor space from the visible histogram. The only available tools are inclusionary: draw an ROI and emit it as a mask via "Apply Visible as Mask". A user who wants "everything *except* the lysosome cluster" has to either (a) draw an ROI that carefully traces around the unwanted cluster — fiddly and lossy — or (b) accept the contamination in the resulting mask.

## User outcome

The user draws a normal phasor ROI over the unwanted cluster, clicks **Clear within selected ROI**, and the pixels inside that ROI vanish from the histogram. The selected ROI is consumed by the action (removed from the ROI list). The cleared pixels are also excluded from any subsequent **Apply Visible as Mask** output, so the resulting mask cleanly captures "main cluster minus lysosomes". A single **Reset cleared** button restores all cleared pixels.

The action is non-destructive: the underlying `(g, s)` arrays are never modified. The cleared mask is purely a render/filter overlay that resets when the dataset changes or the user clicks Reset cleared.

## Requirements

### R1. Cumulative cleared-pixel bitmap, per-session

A new local `_cleared_mask` of shape `(H, W)` (or flat `(H*W,)`) lives in `PhasorPlotWindow`, initialized to all-False. Each successful Clear-within-ROI ORs the ROI's inside-mask into `_cleared_mask`. The cleared mask is **transient state**: it lives only in the running window, resets on dataset change (alongside the existing checkbox resets), and is never persisted to `.h5` or to the Save ROIs JSON.

### R2. Toolbar: two new buttons

The phasor plot toolbar gains two buttons next to the existing ROI controls:

```
[ Add ROI ]  [ Remove ]  [ Clear within selected ROI ]  [ Reset cleared ]
```

- **Clear within selected ROI** — enabled only when an ROI is selected in the ROI list. Disabled (greyed out) otherwise. Clicking it computes the selected ROI's inside-mask via the existing `phasor_roi_to_mask()` (`src/percell4/domain/flim/phasor.py:157`), ORs it into `_cleared_mask`, and removes the consumed ROI from `_roi_widgets`.
- **Reset cleared** — enabled only when `_cleared_mask` has any True pixels. Disabled when nothing is cleared. Clicking it sets `_cleared_mask` back to all-False.

Button enable/disable state updates reactively whenever the ROI selection changes or the cleared mask changes.

### R3. Composition with existing filters (AND)

`_cleared_mask` joins the existing AND chain in `_compute_visible_valid_2d()` and `compute_valid_phasor_pixels()` (`src/percell4/interfaces/gui/peer_views/phasor_plot.py`) as an *inverted* term:

```
visible = valid AND cell_selection AND active_mask AND wavelet_filter AND (NOT cleared_mask)
```

This single integration point ensures the cleared mask affects every downstream consumer consistently:
- The rendered 2D histogram (lysosome cluster vanishes).
- The "Phasor: N valid pixels" status counter (drops by the cleared count).
- The output of **Apply Visible as Mask** for any remaining ROI (cleared pixels excluded from emitted masks).

No new pipeline; one boolean intersection extends the existing one.

### R4. Reset behavior

A single **Reset cleared** button wipes the entire `_cleared_mask` in one click. There is **no per-clear undo and no history stack** — if the user clears too aggressively, they reset and start over. The cleared mask also auto-resets on dataset change, mirroring the lifecycle of the existing `_filtered_check` and `_mask_filter_check` states (`_on_dataset_changed()` in `phasor_plot.py`).

### R5. No visual indicator after clear

Cleared pixels disappear from the plot with no on-plot decoration (no dashed outline, no hatched fill, no ghost marker). The only feedback is the histogram itself and the "valid pixels" counter dropping. This matches the user's stated preference for a clean visual after exclusion.

### R6. Consumption of the selected ROI is final (within the session)

Clicking Clear within selected ROI **removes** the selected ROI from the ROI list. The ROI's parameters (center, radii, angle) are not retained anywhere — only its baked-in pixel footprint inside `_cleared_mask`. Re-selecting "the same" cluster requires drawing a fresh ROI. This is intentional: the user picked the one-shot model precisely to avoid managing a parallel list of "exclusion ROIs".

### R7. Performance

Clear-within-ROI is one ellipse-test (already optimized in `phasor_roi_to_mask()`) plus one boolean OR on a flat `(H*W,)` array. The histogram refresh path already runs a similar AND chain on every refresh; adding one more boolean term is negligible. Reuse the existing `_filter_timer` debounce.

## UI

```
┌─ Phasor Plot toolbar ──────────────────────────────────────────────┐
│ Harmonic [1▼]  ☐ Filtered  ☑ Filter by active mask  Save .SVG     │
│                                                                    │
│ ROIs:  [Add ROI]  [Remove]  [Clear within selected ROI]  [Reset cleared] │
└────────────────────────────────────────────────────────────────────┘
```

States:
- No ROI selected → **Clear within selected ROI** is disabled.
- ROI selected, `_cleared_mask` empty → **Clear within selected ROI** enabled, **Reset cleared** disabled.
- After one clear → ROI list shrinks by one, `_cleared_mask` has true pixels, **Reset cleared** enabled.

Status bar continues to show `Phasor: N valid pixels`, where N already reflects the cleared mask via the AND chain. No new status text required (deliberately omitted per R5).

## Acceptance criteria

A reviewer can verify the feature by:

1. Open a dataset whose phasor plot shows a main cluster and a clearly separable secondary cluster (e.g., lysosomes to the right of cytoplasm).
2. Verify both new buttons start disabled.
3. Click **Add ROI**. Verify **Clear within selected ROI** becomes enabled (ROI is auto-selected on creation).
4. Drag/resize the ROI to enclose the secondary cluster. Verify **Clear within selected ROI** stays enabled.
5. Click **Clear within selected ROI**. Verify:
   - The secondary cluster disappears from the histogram.
   - The ROI is removed from the ROI list.
   - The "valid pixels" counter drops.
   - **Reset cleared** becomes enabled.
   - **Clear within selected ROI** returns to disabled (no ROI is now selected).
6. Add a second ROI over the remaining main cluster. Click **Apply Visible as Mask**. Verify the resulting mask includes only main-cluster pixels — none from the previously cleared secondary cluster region.
7. Click **Reset cleared**. Verify the secondary cluster reappears, the valid-pixel counter rises back to its pre-clear value, and **Reset cleared** disables itself.
8. Toggle **Filtered** (wavelet) and **Filter by active mask** with cleared pixels in place. Verify the cleared mask continues to exclude the secondary cluster regardless of the other filter states.
9. Apply a cell-selection filter (`session.filter_ids`) with cleared pixels in place. Verify the histogram shows pixels that satisfy: in selected cells AND not in cleared region.
10. Switch to a different dataset. Verify `_cleared_mask` resets (secondary cluster visible again on the new dataset, **Reset cleared** disabled).
11. Click **Clear within selected ROI** with no selection. Verify the button cannot be clicked (greyed out) — no error, no silent no-op.

## Scope boundaries

### Deferred for later

- **Persistence across sessions** — saving cleared pixels to `.h5` or to the ROI JSON. Per-session ephemeral is sufficient for the immediate workflow; persistence would be a separate, incrementally-built feature.
- **Per-clear undo / history stack** — a single global Reset is sufficient. If users repeatedly bemoan over-clearing during real use, an Undo stack can be added later without architectural change (just a list of past masks).
- **Visual indicator of cleared regions** — explicitly out of scope per R5. Could be revisited if users ever lose track of what they've cleared.
- **Non-ellipse ROI shapes for clearing** (freehand, rectangle, polygon) — uses whatever ROI shape the existing system supports. New shapes would benefit clearing too but are a separate feature.
- **Multi-ROI batch clear** ("clear within all selected ROIs at once") — single-selection model is fine; the user can repeat.
- **Apply cleared filter to non-phasor windows** — out of scope. The cleared mask is a phasor-display concept; other windows have their own filter UIs.

### Outside this product's identity

- **Mutating the underlying `(g, s)` arrays** — must never happen. The cleared mask is a filter, not a data edit.
- **Promoting cleared regions into a session field** — `session.active_*` / `filter_ids` / `selection` are owned by Selectors and Creators per the GUI state ownership rules in `CLAUDE.md`. The cleared mask is local UI state; it does not belong in the session.
- **Treating clearing as a "kind of ROI"** that lives in a parallel list — explicitly rejected during brainstorming. The user wants clearing to be a one-shot pixel-bitmap operation, not a managed exclusion-ROI collection.

## Dependencies and assumptions

- `phasor_roi_to_mask()` (`src/percell4/domain/flim/phasor.py:157`) returns a clean `(H, W)` boolean inside-mask for any `PhasorROI` regardless of its angle/stretch/shift parameters. Already verified by current Apply Visible as Mask flow.
- `_compute_visible_valid_2d()` and `compute_valid_phasor_pixels()` are the canonical single integration points for the AND filter chain. Adding a `(NOT cleared_mask)` term here propagates correctly to every downstream consumer (histogram, valid-pixel counter, mask emission). Confirmed via the architecture report on the current filtering layers.
- ROI selection state is already tracked by `phasor_plot.py` (the "Selected ROI" panel reflects it). Button enablement can subscribe to that selection signal without new wiring.
- The existing `_on_dataset_changed()` reset hook is the right place to also reset `_cleared_mask`. Same lifecycle as `_filtered_check` and `_mask_filter_check`.

## Files likely touched (planning input, not implementation design)

- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — add two toolbar buttons; add `_cleared_mask` field; wire enable/disable to ROI selection and cleared-mask state; extend `_compute_visible_valid_2d()` with the `(NOT cleared_mask)` term; reset in `_on_dataset_changed()`.
- Possibly `src/percell4/domain/flim/phasor.py` — only if `compute_valid_phasor_pixels()` needs a new optional `cleared_mask` parameter to keep the AND chain composed in one place. Otherwise unchanged.
- Tests under `tests/` for: empty cleared mask = identity behavior; one clear excludes those pixels; reset restores them; clear interacts correctly with each other filter (cell selection, active mask, wavelet); Apply Visible as Mask output excludes cleared pixels; dataset change auto-resets.

Detailed file changes and sequencing belong in `/ce-plan`.

## Outstanding questions

None blocking. Open to revisit during planning:

- Exact button labels — "Clear within selected ROI" is descriptive but verbose; "Subtract ROI" or "Exclude ROI" are shorter alternatives if toolbar space is tight.
- Whether the **Reset cleared** button should ask for confirmation before wiping a large cleared mask. Probably not — destructive-action confirmations are friction the user already rejected by choosing "no undo, no history". Worth a one-line check during planning.
