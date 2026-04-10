# Multi-ROI Phasor Masks Brainstorm

**Date:** 2026-03-27
**Status:** Draft
**Author:** Lee Lab + Claude

---

## What We're Building

Expand the Phasor Plot from a single ROI ellipse to multiple named, colored ROI ellipses. Each ROI represents a distinct lifetime population (e.g., "short lifetime", "long lifetime", "donor", "acceptor"). All ROIs combine into a single integer-labeled mask for downstream measurement and analysis. ROI positions can be saved to and loaded from an app-level library for reuse across datasets.

---

## Why This Approach

FLIM phasor analysis often requires classifying pixels into multiple lifetime populations simultaneously. The current single-ROI design forces serial workflows: draw ROI → apply mask → measure → draw different ROI → repeat. With multiple ROIs, the user defines all populations at once, producing a single labeled mask that the measurement pipeline can process in one pass.

The single labeled mask approach (vs. separate binary masks per ROI) fits the existing architecture:
- `CellDataModel` tracks one `active_mask` — no multi-mask support needed
- `measure_cells()` iterates over unique nonzero mask values, computing per-cell metrics for each ROI label
- DataFrame filtering lets users combine or exclude ROI groups after measurement (e.g., ROI 1 + 2 but not 3)

---

## Key Decisions

### 1. Single labeled mask with integer values per ROI

One mask array stored at `/masks/phasor_roi` in HDF5:
- `0` = outside all ROIs
- `1` = ROI "short lifetime"
- `2` = ROI "long lifetime"
- `3` = ROI "intermediate"
- etc.

Overlapping ROIs: last-added ROI wins for overlapping pixels (higher label overwrites lower). This is simple and predictable.

### 2. Named + colored ROIs

Each ROI has:
- **Name** — user-editable text (e.g., "short lifetime"). Used in measurement column prefixes: `short_lifetime_mean`, `short_lifetime_area`, etc.
- **Color** — distinct color for the ellipse outline on the phasor plot and for the mask overlay in the viewer. Auto-assigned from a color cycle, user can override.
- **Label integer** — auto-assigned (1, 2, 3, ...), displayed but not user-editable.

A small ROI list panel alongside the phasor plot shows all ROIs with their name, color swatch, and visibility toggle.

### 3. ROI management UI

Phasor Plot toolbar additions:

| Control | Behavior |
|---------|----------|
| **Add ROI** | Creates a new ellipse ROI at a default position with the next available label and color |
| **Remove ROI** | Deletes the selected ROI from the plot |
| **ROI list** | Small panel listing all ROIs — click to select/edit name, toggle visibility |
| **Apply All as Mask** | Combines all ROIs into the single labeled mask, writes to HDF5, pushes to viewer |

Each ROI ellipse retains the existing interaction: drag to move, resize handles, angle spinbox (or per-ROI angle control).

### 4. Save/Load ROI positions — app-level JSON library

**Storage location:** `~/.percell4/phasor_rois/`

**JSON format:**
```json
{
  "name": "donor_acceptor_fret",
  "description": "Standard FRET donor/acceptor ROIs for CFP-YFP",
  "rois": [
    {
      "name": "donor",
      "center": [0.4, 0.32],
      "radii": [0.10, 0.08],
      "angle_deg": 15,
      "label": 1,
      "color": "#3498db"
    },
    {
      "name": "acceptor",
      "center": [0.62, 0.41],
      "radii": [0.12, 0.06],
      "angle_deg": -5,
      "label": 2,
      "color": "#e74c3c"
    }
  ]
}
```

**UI in Phasor Plot toolbar:**

```
[Save ROIs...] [Load ROIs ▾]
                ├─ donor_acceptor_fret.json
                ├─ short_vs_long.json
                └─ Browse...
```

- **Save ROIs...** — prompts for a name, saves current ROI set to `~/.percell4/phasor_rois/<name>.json`
- **Load ROIs ▾** — dropdown listing all saved `.json` files. Selecting one replaces the current ROIs on the plot. "Browse..." opens a file dialog for loading from arbitrary paths.

### 5. Measurement integration

`measure_cells()` needs a small extension: when the mask contains multiple nonzero labels, compute metrics per ROI label per cell:

- For each cell, for each unique mask label within that cell's region:
  - `{roi_name}_{metric}` columns (e.g., `donor_mean_intensity`, `acceptor_mean_intensity`)
  - `{roi_name}_area` — pixel count for that ROI within the cell
  - `{roi_name}_fraction` — fraction of cell pixels in that ROI

This replaces the current binary `mask_inside` / `mask_outside` scoping with label-aware scoping.

---

## Open Questions

None — all questions resolved during brainstorming.
