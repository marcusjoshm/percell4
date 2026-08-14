---
title: Phasor plot — filter by active mask
status: open
created: 2026-04-30
type: feature-requirements
related:
  - docs/brainstorms/2026-03-27-cross-window-selection-filtering-brainstorm.md
  - docs/brainstorms/2026-04-17-phasor-roi-separate-masks-brainstorm.md
  - docs/plans/2026-03-27-feat-cross-window-selection-filtering-plan.md
  - docs/plans/2026-03-27-feat-multi-roi-phasor-masks-plan.md
---

# Phasor plot — filter by active mask

## Problem

The phasor plot histogram already supports filtering to a cell selection (`session.filter_ids` → `np.isin(labels_flat, filter_ids)`). For many FLIM analysis workflows the relevant region of interest is not "these cells" but "these pixels" — e.g., cytoplasm, nucleus, or a manually-painted ROI stored as a binary mask. There is currently no way to restrict the phasor histogram to the pixels of a chosen mask. Users have to either redo segmentation to express the ROI as labeled cells (overkill), or trust visual inspection of an unfiltered phasor that mixes signal from regions they don't care about.

## User outcome

When a mask is active and the user opts in, the phasor plot's intensity-weighted 2D histogram restricts to pixels where `mask == True`. When a cell selection is also active, both filters compose (AND): pixels must be inside the mask AND inside the selected cells' labels. This makes "phasor of cytoplasm pixels in these 5 cells" expressible in one view without redoing segmentation.

## Requirements

### R1. Mask filter source = `session.active_mask`

The phasor mask filter reads from the same `session.active_mask` field already used elsewhere in the app (data tab, viewer, phasor ROI apply). No new session field; no new picker UI in the phasor plot. Whatever mask the rest of the app considers "active" is the candidate filter mask.

### R2. Opt-in toggle

A new "Filter by active mask" checkbox lives in the phasor plot toolbar, next to the existing "Filtered" checkbox.

- Disabled when `session.active_mask is None`.
- Enabled when an active mask exists. Default state: unchecked.
- Re-enables/disables reactively when active_mask changes.
- When checked, the histogram refreshes to apply the mask filter.
- When unchecked, the mask filter is bypassed (existing behavior preserved).

This opt-in defends against the feedback loop where phasor ROI's "Apply Visible as Mask" sets `active_mask` — without opt-in, the phasor would instantly restrict itself to the mask it just produced.

### R3. Composition behavior (AND)

When both the cell-selection filter (`session.filter_ids`) AND the mask filter are active, the histogram retains pixels that are valid AND in the cell-selection AND in the mask. Boolean intersection at the per-pixel-mask layer; no precedence rules between filters.

When only one filter is active, that filter applies alone (today's cell-only behavior is preserved exactly).

When neither is active, no filter applies (today's all-pixels behavior).

### R4. Active mask shape mismatch handling

The mask is stored at full image resolution (H, W). The phasor maps `(g, s)` and labels are also (H, W). All three should align. When a mask shape doesn't match the phasor map shape (e.g., loaded from a different dataset, channel-specific mask vs. single-channel phasor), the filter is silently bypassed and a status-bar message indicates "mask shape mismatch — filter not applied". The checkbox stays checked so the user can fix the underlying mismatch without re-toggling.

### R5. Wavelet "Filtered" toggle interaction

Existing "Filtered" checkbox toggles between unfiltered `(g, s)` and wavelet-filtered `(g_filtered, s_filtered)`. The mask filter applies the same way regardless of which `(g, s)` is being displayed — both share the same shape and same labels.

### R6. Performance

The mask filter step is a single boolean AND on a flat `(H*W,)` array. No noticeable refresh delay beyond today's cell-filter path. Reuse the existing 150 ms debounce timer on `_filter_timer`.

## UI

The phasor plot toolbar gains one checkbox:

```
[ Filtered ]  [ Filter by active mask ]   ...other controls...
```

When `session.active_mask` changes:
- Becomes a non-empty string → checkbox enabled
- Becomes `None` → checkbox disabled and unchecked

Status bar shows a brief message when the filter applies or the shape mismatch fallback engages.

## Acceptance criteria

A reviewer can verify the feature by:

1. Open a dataset with `(g, s)` phasor maps and at least one mask in `/masks/`.
2. Verify the new checkbox is **disabled** when no mask is active.
3. Set the active mask via existing UI (data tab or viewer). Verify the checkbox **enables**.
4. Check the checkbox. Verify the phasor histogram restricts to pixels in the mask (compare visually to the unfiltered version — fewer points, only those inside the mask region).
5. With the checkbox still on, also select a subset of cells via segmentation. Verify the histogram further restricts to pixels that are in BOTH the mask AND the selected cells (intersection).
6. Uncheck the checkbox. Verify cell-selection filter still works alone (existing behavior preserved).
7. Clear the active mask. Verify the checkbox disables and unchecks; no filter applied.
8. Use phasor ROI flow ("Apply Visible as Mask"). Verify the new mask becomes `active_mask` BUT the phasor does not auto-restrict — the user must explicitly check the box. (Confirms no feedback loop.)
9. Toggle "Filtered" wavelet checkbox while mask filter is on. Verify both views (unfiltered + wavelet-filtered) honor the mask filter consistently.

## Scope boundaries

### Deferred for later

- **Multiple simultaneous mask filters** (AND across N masks, OR across N masks) — single active mask is sufficient for the immediate use case.
- **Per-channel masks** — if the user has different masks for different channels, only the active one applies; channel-specific composition is out of scope.
- **Persisted "filter on by default"** preference per dataset — checkbox state resets with the window today; persistence can come later if requested.
- **Filter-by-mask in other windows** (e.g., measurements table, lifetime histogram) — out of scope here. This brainstorm only covers the phasor plot. If the pattern lands well, extending to other windows is straightforward but is a separate feature.

### Outside this product's identity

- Building a new mask creation UI inside the phasor plot. Mask creation already lives elsewhere (cellpose, painted masks, phasor ROI apply); this feature only consumes existing masks.
- Implicit auto-filter without the opt-in checkbox. The opt-in is load-bearing because `active_mask` is also the OUTPUT of phasor ROI "Apply Visible as Mask".

## Dependencies and assumptions

- `session.active_mask` is reliably set/cleared when masks come and go (verified in `model.py:130-149` and `phasor_plot.py:319`).
- `/masks/<name>` is stored as `uint8` 0/1 binary arrays at full image resolution (per `napari-mask-layer-misclassified-as-segmentation.md` Bug fix). Matches the phasor map resolution by construction.
- The `_refresh_histogram` path in `peer_views/phasor_plot.py:570-610` is the single site for the additional `valid` boolean step. No new pipeline; one boolean intersection extends the existing one.

## Files likely touched (planning input, not implementation design)

- `src/percell4/interfaces/gui/peer_views/phasor_plot.py` — add checkbox; extend `_refresh_histogram` filter step; subscribe to active_mask changes to enable/disable.
- `src/percell4/model.py` and/or session — confirm `Event.MASK_CHANGED` (or equivalent) fires when `active_mask` changes; the phasor view subscribes to update checkbox state.
- Tests under `tests/` for the composition logic (unit-level: cell-only, mask-only, both, neither, shape-mismatch).

Detailed file changes and sequencing belong in `/ce-plan`.

## Outstanding questions

None blocking. Open to revisit during planning:

- Whether to add a small visual indicator on the phasor plot (e.g., border color, mask name in title) when mask filter is active, so the user knows their view is restricted. Lightweight polish, not load-bearing.
