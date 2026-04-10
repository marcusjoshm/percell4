# UI Theme Consistency Brainstorm

**Date:** 2026-04-05
**Status:** Draft

## What We're Building

A centralized theme system (`gui/theme.py`) that eliminates all per-file inline styling, enforces consistent colors and widget appearance across the entire application, and works reliably on any platform regardless of OS dark mode settings.

### Problems Being Solved
1. **Readability** — unstyled widgets fall through to macOS Aqua light theme, creating dark-text-on-dark or light-text-on-light depending on which machine the app runs on
2. **Inconsistency** — 3 different background colors, ~60 scattered `setStyleSheet` calls across 12 files, zero scrollbar styling, selection highlight off by one hex digit between files
3. **No platform independence** — no `app.setStyle("Fusion")`, no QPalette, no dark mode detection

## Why This Approach

- **Fusion style** ensures identical rendering on macOS, Windows, and Linux — no platform-dependent surprises
- **Single global stylesheet** applied at `app.setStyleSheet()` means every widget gets dark theme by default — no more unstyled widgets showing up in light theme
- **Named color constants** in `theme.py` eliminate hex-value duplication and typos (like `#2a4a6a` vs `#2a4a6b`)
- **Big bang rollout** in one PR — the app is small enough (12 files) that migrating incrementally would leave it visually inconsistent during the transition

## Key Decisions

### 1. Base Style: Fusion
Use `app.setStyle("Fusion")` at startup. This is Qt's cross-platform style that fully respects stylesheets. napari and most Qt scientific apps use it.

### 2. Theme Module: Constants + Global Stylesheet
Create `gui/theme.py` with:
- Named color constants (BACKGROUND, SURFACE, BORDER, ACCENT, TEXT, etc.)
- A single `APP_STYLESHEET` string applied via `app.setStyleSheet()` at startup
- Individual files remove their `_apply_style()` methods and inline `setStyleSheet` calls
- Only dynamic styling (status color changes) remains inline, using constants from theme.py

### 3. Two-Level Background Hierarchy
- **BACKGROUND_DEEP** (`#121212`) — launcher main area (the "base layer")
- **BACKGROUND** (`#1e1e1e`) — dialogs, panels, popups, standalone windows
- **SURFACE** (`#2a2a2a`) — input fields, buttons, cards (interactive elements)

### 4. Phasor Plot: Switch to Dark
Convert phasor plot from white background to dark (`#1e1e1e`), matching the rest of the app. The universal semicircle and ROI ellipses will need lighter stroke colors.

### 5. Scrollbars: System Default
Keep the OS-native scrollbar appearance. The Fusion style + dark stylesheet should make them visible enough.

### 6. Rollout: Big Bang
One PR that creates `theme.py`, sets Fusion + global stylesheet, and sweeps all 12 files to remove inline styles.

## Color Palette (Finalized)

| Constant | Value | Role |
|----------|-------|------|
| `BACKGROUND_DEEP` | `#121212` | Launcher main area |
| `BACKGROUND` | `#1e1e1e` | Dialogs, panels, standalone windows, plot backgrounds |
| `SURFACE` | `#2a2a2a` | Input fields, buttons, combo boxes |
| `BORDER` | `#3a3a3a` | Group box borders, dividers |
| `BORDER_INPUT` | `#444444` | Input field borders |
| `ACCENT` | `#4ea8de` | Focus borders, active labels, group titles, hover states |
| `TEXT` | `#e0e0e0` | Primary text |
| `TEXT_BRIGHT` | `#ffffff` | Headings, selected items |
| `TEXT_MUTED` | `#888888` | Disabled/inactive text |
| `TEXT_LABEL` | `#cccccc` | Form labels |
| `SELECTION` | `#2a4a6b` | Selected row/item highlight |
| `SUCCESS` | `#66cc66` | Success status |
| `WARNING` | `#ffaa44` | Warning/in-progress status |
| `ERROR` | `#ff6666` | Error status |
| `ACTION_GREEN` | `#2d7d46` | Green action buttons (Run, Accept) |
| `ACTION_GREEN_HOVER` | `#35a355` | Green button hover |
| `SIDEBAR` | `#1e2a3a` | Launcher sidebar |
| `SIDEBAR_ACTIVE` | `#0d1b2a` | Launcher sidebar checked state |

## Audit Summary (Current State)

### Files to Migrate

| File | Current Approach | setStyleSheet calls |
|------|-----------------|-------------------|
| `launcher.py` | 3 large inline blocks + ~15 dynamic | ~18 |
| `import_dialog.py` | `_apply_style()` + inline | ~4 |
| `compress_dialog.py` | `_apply_style()` + inline | ~3 |
| `add_layer_dialog.py` | `_apply_style()` + inline | ~2 |
| `segmentation_panel.py` | All inline | ~6 |
| `cell_table.py` | Single inline block | ~1 |
| `data_plot.py` | pyqtgraph only | ~1 |
| `phasor_plot.py` | pyqtgraph + inline | ~2 |
| `grouped_seg_panel.py` | Inline | ~2 |
| `threshold_qc.py` | All inline (~12) | ~12 |

### What the Global Stylesheet Must Cover
- QMainWindow, QDialog, QWidget backgrounds
- QGroupBox + QGroupBox::title
- QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox (all input widgets)
- QPushButton (default, hover, disabled states)
- QCheckBox, QRadioButton
- QLabel
- QListWidget + indicators (checked/unchecked)
- QTreeWidget + indicators
- QTableView + QHeaderView
- QTabWidget + QTabBar
- QComboBox QAbstractItemView (dropdown popup)
- QDialogButtonBox QPushButton

### What Stays Per-File
- Dynamic status color changes (`setStyleSheet` with SUCCESS/WARNING/ERROR)
- pyqtgraph `setBackground()` calls (use `theme.BACKGROUND` constant)
- napari viewer theme (independent, stays as-is)
- Phasor/data plot brush and pen colors (semantic, not theme colors)
- Green action buttons (theme constant, but applied inline for specific buttons)

## Open Questions

(none)
