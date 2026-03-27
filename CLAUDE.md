# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is PerCell4

Single-cell microscopy analysis software. The core value proposition is tracking individual cells across analysis steps, timepoints, and conditions. This is a clean rebuild — not an iteration on PerCell3.

## Tech Stack

- **Python 3.12** (virtual environment at `.venv/`)
- **GUI:** Qt application (qtpy + PyQt5) with embedded napari viewer and pyqtgraph panels
- **Data storage:** HDF5 via h5py — single `.h5` file per experiment (images, labels, phasor maps, measurements)
- **Data model:** pandas DataFrames for per-cell measurements (no database)
- **Segmentation:** Cellpose
- **Plotting:** pyqtgraph (Qt-native, fast scatter/ROI)
- **Image I/O:** tifffile, sdtfile (Becker&Hickl)
- **Image processing:** scikit-image, scipy, numpy

## Development Setup

```bash
# Activate the virtual environment
source .venv/bin/activate

# Run the app
python main.py

# Install new dependencies
pip install <package> && pip freeze > requirements.txt
```

## Architecture

Single Qt process, multi-window. Each functional unit (napari viewer, data plots, phasor plot, plugin manager) is its own independent top-level window. A launcher/hub window manages the app. All windows communicate through a shared `CellDataModel` (QObject with Qt signals) — windows never talk to each other directly.

Key pattern: `CellDataModel` holds a pandas DataFrame (one row per cell) and emits `data_updated` / `selection_changed` signals. All windows react to these.

Heavy computation (Cellpose, etc.) runs in QThread workers to avoid freezing the UI.

## Documentation Rules

- Per-module CLAUDE.md files describe current state only — never plans, never history
- Archive brainstorms and planning docs immediately after implementation
- Active docs contain ONLY what IS, not what WAS or MIGHT BE
- Never allow contradictory architectural decisions to coexist in context

## Previous Versions

Code from prior versions can be referenced for domain logic:
- PerCell (v1/v2): `/Users/leelab/percell`
- PerCell3: `/Users/leelab/percell3`
- PerCell3 stable: `/Users/leelab/percell3_stable`
