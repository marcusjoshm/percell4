---
topic: Phasor ROI separate masks (one mask per ROI)
date: 2026-04-17
status: decided
---

# Phasor ROI: Separate Mask Per ROI

## What We're Building

Change phasor ROI mask saving from one multi-label mask (`/masks/phasor_roi` with values 0,1,2,3...) to separate binary masks per ROI (`/masks/Fast`, `/masks/Slow`, etc.). Each ROI becomes its own HDF5 entry, its own napari Labels layer, and can be independently selected as the active mask for particle analysis.

## Problems with the Current Model

1. **ROI names don't propagate.** The phasor plot has editable ROI names ("Fast", "Slow") but the mask layer is always named "phasor_roi". The user can't tell which integer label maps to which ROI without checking the phasor plot.

2. **Particle analysis ignores labels.** `analyze_particles` collapses the mask to binary (`mask > 0`) — it doesn't distinguish ROI 1 from ROI 2. Per-ROI particle counts aren't possible.

3. **Label ordering is confusing.** In a multi-label mask, ROI 1 = label 1, ROI 2 = label 2, etc. If the user deletes ROI 1, labels shift. There's no way to reorder or rename labels after saving.

## Key Decisions

1. **One binary mask per ROI in HDF5.** "Apply Visible as Mask" saves each visible ROI as `/masks/<roi_name>` (binary uint8, 0/1). ROI named "Fast" → `/masks/Fast`. ROI named "Slow" → `/masks/Slow`. Each gets its own napari Labels layer.

2. **ROI name = mask name directly.** The phasor plot's user-editable name field controls the mask name. No prefix, no dialog. Name uniqueness is already enforced by the phasor plot.

3. **Active mask selects one ROI for particle analysis.** User sets "Active Mask" in the Data tab to "Fast" or "Slow". Particle analysis uses that single binary mask — no multi-label parsing needed.

4. **Keep multi-ROI measurement via "Measure All Phasor Masks".** A button (or the existing measure path) can combine all phasor-origin masks into a temporary multi-label mask for `measure_multichannel_multi_roi`. The user gets per-ROI columns (`GFP_Fast_mean_intensity`, `GFP_Slow_mean_intensity`) in the measurements DataFrame without manually switching masks.

## What Changes

### Phasor plot (`phasor_plot.py`)
- `mask_applied` signal changes payload: emit a list of `(name, binary_mask)` tuples instead of one multi-label mask + color_dict
- Or: emit one mask at a time per ROI, firing `mask_applied` N times

### Launcher / main_window
- `_on_phasor_mask_applied` writes each mask to HDF5 separately: `store.write_mask(roi_name, binary_mask)` for each
- Adds each as a napari layer: `viewer_win.add_mask(binary_mask, name=roi_name)`
- Sets the last one as active mask (or the first, depending on UX)

### Particle analysis
- No changes needed — it already treats the mask as binary (`mask > 0`). With a true binary mask, this is correct by construction.

### Measurement
- "Measure All Phasor Masks" button reconstructs a multi-label mask at measurement time by combining all phasor-origin masks: for each mask in `/masks/`, assign a unique label. Pass to `measure_multichannel_multi_roi` with `roi_names={1: "Fast", 2: "Slow"}`.
- Existing single-mask measurement continues to work via the active mask.

### Session / Data tab
- No Session changes needed — `active_mask` already tracks a single mask name. The Data tab's mask dropdown will show all individual masks ("Fast", "Slow" instead of just "phasor_roi").

## What This Does NOT Change

- ROI creation, editing, and preview in the phasor plot (unchanged)
- The phasor preview mask (the live overlay while editing — still a single ephemeral layer)
- Threshold masks (these are already separate binary masks per method/channel)
- The `_on_phasor_preview` handler (preview is still one combined mask for visual feedback)

## Open Questions

None remaining — all resolved during brainstorm.
