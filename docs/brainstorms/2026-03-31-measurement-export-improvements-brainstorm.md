---
title: Measurement, Particle Export, and Configuration Improvements
date: 2026-03-31
status: decided
---

# Measurement, Particle Export, and Configuration Improvements

## What We're Building

Four related improvements to the measurement and export pipeline:

### 1. Per-Particle CSV Export
- **Current:** CSV exports one row per cell with aggregated particle stats (particle_count, mean_particle_area, etc.)
- **Change:** Export one row per particle. Each row has particle-specific data (area, intensity, coordinates) plus a `cell_id` column linking back to the parent cell. Cell-level metrics are NOT duplicated on particle rows — join on `cell_id` if needed.

### 2. Analyze Particles Across All Channels
- **Current:** `_on_analyze_particles` only uses the first image layer for intensity measurements.
- **Change:** Iterate all image layers (same pattern as `_on_measure_cells`) so each particle gets intensity stats from every channel.

### 3. Preserve Filter/Selection on Measure/Analyze
- **Current:** `set_measurements()` unconditionally clears `_filtered_ids` and `_selected_ids` because "new data may have different cell IDs."
- **Change:** Never clear filter or selection during measurement. Cell IDs come from the segmentation labels and are stable — they only change if the user explicitly edits the segmentation layer. If measured cell IDs differ from the filter, that's expected (user measured a subset).

### 4. Measurement Configuration Window
- **What:** A dialog/window with checkboxes for each available metric (mean_intensity, max_intensity, std_intensity, etc.). Only checked metrics are computed during measurement.
- **Why:** The current DataFrame includes every metric for every channel, making the cell table and CSV hard to navigate. Controlling computation (not just visibility) means faster measurement and leaner data.
- **Persistence:** Save the checked metrics in QSettings so they persist across sessions.

## Why This Approach

- **Per-particle rows** are the standard format for downstream analysis (ImageJ, R, Python). Aggregated stats can always be recomputed from raw particle data, but not vice versa.
- **All-channel particle analysis** matches what Measure Cells already does and gives complete data without re-running.
- **Preserving filter/selection** respects user intent. The current auto-clear is surprising and forces users to re-apply filters after every measurement.
- **Compute-time config** (vs display-only) reduces actual work for large images and keeps the DataFrame clean from the start.

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Particle CSV format | One row per particle, cell_id column, no cell metrics | Lean export; join on cell_id if needed |
| Channel coverage | All channels for both Measure and Analyze | Consistency, complete data |
| Filter/selection on measure | Preserve both, never clear | Cell IDs are stable; clearing is surprising |
| Config scope | Controls what gets computed | Faster measurement, leaner DataFrame |
| Config persistence | QSettings | Matches existing geometry persistence pattern |

## Scope Boundaries

**In scope:**
- Per-particle DataFrame and CSV export
- Multi-channel particle analysis
- Remove filter/selection clearing from `set_measurements()`
- Metric selection dialog with checkboxes
- QSettings persistence for metric selection

**Out of scope (future work):**
- Column visibility toggle in cell table (display-only filtering)
- Per-channel metric configuration (same metrics apply to all channels)
- Particle visualization in napari (particle outlines, etc.)
- Custom metric definitions

## Key Files to Modify

- `src/percell4/measure/particle.py` — Per-particle data collection, multi-channel support
- `src/percell4/measure/measurer.py` — Metric selection parameter
- `src/percell4/measure/metrics.py` — Available metrics registry
- `src/percell4/gui/launcher.py` — Button handlers, config dialog trigger, multi-channel particle analysis
- `src/percell4/model.py` — `set_measurements()` filter/selection preservation
- `src/percell4/gui/cell_table.py` — Particle export option
