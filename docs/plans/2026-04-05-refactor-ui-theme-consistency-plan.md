---
title: "refactor: Centralize UI theme for consistency and cross-platform readability"
type: refactor
date: 2026-04-05
---

# refactor: Centralize UI Theme for Consistency and Cross-Platform Readability

## Overview

Create a centralized `gui/theme.py` module with named color constants and a single global stylesheet. Set `app.setStyle("Fusion")` for platform independence. Sweep all 12 GUI files to remove ~60 inline `setStyleSheet` calls, replacing them with the global theme. Big bang rollout in one branch.

## Problem Statement / Motivation

1. **Readability failures** — widgets without explicit styling fall through to macOS Aqua (light theme), creating unreadable dark-on-dark or light-on-light text depending on the machine's dark mode setting
2. **Inconsistency** — 3 different background colors (`#121212`, `#1e1e1e`, `#2b2b2b`), selection highlights off by one hex digit (`#2a4a6a` vs `#2a4a6b`), zero scrollbar styling, unstyled QComboBox/QSpinBox in standalone windows
3. **No platform independence** — no `app.setStyle("Fusion")`, no QPalette, no dark mode handling. The app looks different on every machine.

## Proposed Solution

### Phase 1: Create `gui/theme.py` + Set Fusion

**New file: `src/percell4/gui/theme.py`**

```python
"""Centralized dark theme for PerCell4.

All color constants and the global application stylesheet live here.
Individual GUI files import constants for dynamic styling only.
"""

# ── Color palette ──────────────────────────────────────
BACKGROUND_DEEP = "#121212"   # Launcher main area
BACKGROUND = "#1e1e1e"        # Dialogs, panels, standalone windows
SURFACE = "#2a2a2a"           # Input fields, buttons, combo boxes
BORDER = "#3a3a3a"            # Group box borders, dividers
BORDER_INPUT = "#444444"      # Input field borders
ACCENT = "#4ea8de"            # Focus borders, active labels, hover
TEXT = "#e0e0e0"              # Primary text
TEXT_BRIGHT = "#ffffff"       # Headings, selected items
TEXT_MUTED = "#888888"        # Disabled/inactive text
TEXT_LABEL = "#cccccc"        # Form labels
SELECTION = "#2a4a6b"         # Selected row/item highlight
SUCCESS = "#66cc66"           # Success status
WARNING = "#ffaa44"           # Warning/in-progress status
ERROR = "#ff6666"             # Error status
ACTION_GREEN = "#2d7d46"      # Green action buttons (Run, Accept)
ACTION_GREEN_HOVER = "#35a355" # Green button hover
SIDEBAR = "#1e2a3a"           # Launcher sidebar
SIDEBAR_ACTIVE = "#0d1b2a"    # Launcher sidebar checked state
SIDEBAR_HOVER = "#2a3d52"     # Launcher sidebar hover

APP_STYLESHEET = f"""
    /* ── Base ── */
    QMainWindow, QDialog {{ background-color: {BACKGROUND}; color: {TEXT}; }}
    QWidget {{ color: {TEXT}; }}

    /* ── Group boxes ── */
    QGroupBox {{
        color: {TEXT_BRIGHT};
        border: 1px solid {BORDER};
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 16px;
    }}
    QGroupBox::title {{
        color: {ACCENT};
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}

    /* ── Input widgets ── */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {SURFACE};
        color: {TEXT_BRIGHT};
        border: 1px solid {BORDER_INPUT};
        border-radius: 4px;
        padding: 4px 8px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {ACCENT};
    }}
    QComboBox QAbstractItemView {{
        background-color: {SURFACE};
        color: {TEXT_BRIGHT};
        border: 1px solid {BORDER};
        selection-background-color: {SELECTION};
    }}

    /* ── Buttons ── */
    QPushButton {{
        background-color: {SURFACE};
        color: {TEXT_BRIGHT};
        border: 1px solid {BORDER_INPUT};
        border-radius: 4px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{ background-color: {BORDER}; border-color: {ACCENT}; }}
    QPushButton:disabled {{ color: {TEXT_MUTED}; }}

    /* ── Check / Radio ── */
    QCheckBox, QRadioButton {{ color: {TEXT}; }}

    /* ── Labels ── */
    QLabel {{ color: {TEXT_LABEL}; }}

    /* ── List widgets ── */
    QListWidget {{
        background-color: {BACKGROUND};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 4px;
        outline: none;
    }}
    QListWidget::item {{ padding: 3px 4px; }}
    QListWidget::item:selected {{ background-color: {SELECTION}; color: {TEXT_BRIGHT}; }}
    QListWidget::item:hover:!selected {{ background-color: {SURFACE}; }}
    QListWidget::indicator {{ width: 16px; height: 16px; }}
    QListWidget::indicator:unchecked {{
        border: 1px solid #555555; border-radius: 3px; background-color: {SURFACE};
    }}
    QListWidget::indicator:checked {{
        border: 1px solid {ACCENT}; border-radius: 3px; background-color: {ACCENT};
    }}

    /* ── Tree widgets ── */
    QTreeWidget {{
        background-color: {BACKGROUND};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 4px;
        outline: none;
    }}
    QTreeWidget::item {{ padding: 3px 2px; }}
    QTreeWidget::item:selected {{ background-color: {SELECTION}; color: {TEXT_BRIGHT}; }}
    QTreeWidget::item:hover:!selected {{ background-color: {SURFACE}; }}
    QTreeWidget::indicator {{ width: 16px; height: 16px; }}
    QTreeWidget::indicator:unchecked {{
        border: 1px solid #555555; border-radius: 3px; background-color: {SURFACE};
    }}
    QTreeWidget::indicator:checked {{
        border: 1px solid {ACCENT}; border-radius: 3px; background-color: {ACCENT};
    }}
    QTreeWidget::indicator:indeterminate {{
        border: 1px solid {ACCENT}; border-radius: 3px; background-color: {SELECTION};
    }}

    /* ── Table view ── */
    QTableView {{
        background-color: {BACKGROUND};
        color: {TEXT};
        gridline-color: {BORDER};
        border: 1px solid {BORDER};
        selection-background-color: {SELECTION};
    }}
    QHeaderView::section {{
        background-color: {SURFACE};
        color: {ACCENT};
        border: none;
        border-right: 1px solid {BORDER};
        border-bottom: 1px solid {BORDER};
        padding: 4px 8px;
        font-weight: bold;
    }}

    /* ── Tabs ── */
    QTabWidget::pane {{ border: 1px solid {BORDER}; background-color: {BACKGROUND}; }}
    QTabBar::tab {{
        background-color: {SURFACE};
        color: {TEXT_LABEL};
        border: 1px solid {BORDER};
        padding: 6px 14px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {BACKGROUND};
        color: {ACCENT};
        border-bottom-color: {BACKGROUND};
    }}

    /* ── Scroll areas ── */
    QScrollArea {{ background-color: {BACKGROUND}; border: none; }}
"""


def apply_theme(app):
    """Apply Fusion style and dark theme to the QApplication."""
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
```

**Modify `app.py`:**

```python
def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)

    from percell4.gui.theme import apply_theme
    apply_theme(app)

    # ... rest unchanged
```

### Phase 2: Sweep All GUI Files

For each file, remove inline `setStyleSheet` calls that are now covered by the global stylesheet. Keep only:
- Dynamic status color changes (using `theme.SUCCESS`, `theme.ERROR`, etc.)
- pyqtgraph `setBackground()` calls (using `theme.BACKGROUND`)
- Launcher-specific styling (sidebar with `theme.SIDEBAR` colors, `BACKGROUND_DEEP`)
- Green action buttons (using `theme.ACTION_GREEN`)

**File-by-file migration:**

#### `launcher.py`
- [ ] Remove the 3 large inline stylesheet blocks (lines 61-82, 121-140, 147-200)
- [ ] Keep the sidebar as a targeted stylesheet on the sidebar widget only, using theme constants
- [ ] Set launcher central widget background to `theme.BACKGROUND_DEEP`
- [ ] Replace all hardcoded hex values in dynamic status calls with `theme.SUCCESS`, `theme.WARNING`, `theme.ERROR`
- [ ] Replace scroll area background strings with `theme.BACKGROUND`
- [ ] Replace `_section_label` and `_placeholder` helper hex values with theme constants

#### `import_dialog.py`
- [ ] Remove `_apply_style()` method entirely
- [ ] Remove inline `setStyleSheet` on QScrollArea and content widget (global covers it)

#### `compress_dialog.py`
- [ ] Remove `_apply_style()` method entirely
- [ ] Remove inline `setStyleSheet` on QScrollArea and content widget

#### `add_layer_dialog.py`
- [ ] Remove `_apply_style()` method entirely
- [ ] Remove inline `setStyleSheet` on QScrollArea

#### `segmentation_panel.py`
- [ ] Remove all 6 inline `setStyleSheet` calls for static styling (title label, channel label, checkbox)
- [ ] Keep dynamic status color calls, replace hex values with `theme.SUCCESS`, `theme.WARNING`, `theme.ERROR`

#### `cell_table.py`
- [ ] Remove the single inline stylesheet block (lines 184-199) — global covers QTableView + QHeaderView

#### `data_plot.py`
- [ ] Replace `self._plot.setBackground("#1e1e1e")` with `self._plot.setBackground(theme.BACKGROUND)`

#### `phasor_plot.py`
- [ ] Replace `self._plot.setBackground("w")` with `self._plot.setBackground(theme.BACKGROUND)`
- [ ] Update semicircle pen from `"k"` (black) to a light color (e.g., `theme.TEXT_LABEL` or `"#aaaaaa"`)
- [ ] Remove inline checkbox `setStyleSheet`

#### `grouped_seg_panel.py`
- [ ] Replace green button inline style with theme constants: `theme.ACTION_GREEN`, `theme.ACTION_GREEN_HOVER`
- [ ] Replace status label color with `theme.TEXT_MUTED`

#### `threshold_qc.py`
- [ ] Remove all ~12 inline `setStyleSheet` calls for window backgrounds, title labels, stat labels
- [ ] Replace plot background `"#2b2b2b"` with `theme.BACKGROUND`
- [ ] Keep green button styling using `theme.ACTION_GREEN`
- [ ] Keep dynamic group color assignments (tab10 palette — semantic, not theme)

### Phase 3: Verify

- [ ] Launch the app and visually inspect every window/dialog
- [ ] Test on macOS with dark mode ON and OFF — should look identical
- [ ] Check all QComboBox dropdowns render with dark background (previously unstyled in standalone windows)
- [ ] Check QSpinBox widgets in phasor panel and segmentation panel are dark-themed
- [ ] Verify napari viewer is unaffected (independent theme)
- [ ] Verify pyqtgraph plots have correct background colors
- [ ] Verify the phasor semicircle and ROI colors are visible on dark background

## Acceptance Criteria

- [ ] `gui/theme.py` exists with all named color constants and `APP_STYLESHEET`
- [ ] `app.py` calls `apply_theme(app)` before any windows are created
- [ ] Fusion style is active (`app.style().objectName() == "fusion"`)
- [ ] Zero `setStyleSheet` calls with hardcoded hex color values remain (except dynamic status and pyqtgraph)
- [ ] All `_apply_style()` methods removed from dialogs
- [ ] All windows render consistently dark on macOS light mode AND dark mode
- [ ] QComboBox dropdowns, QSpinBox, QScrollArea all render dark everywhere
- [ ] Phasor plot has dark background with visible semicircle and data
- [ ] No visual regressions in launcher, dialogs, panels, or popups

## Dependencies & Risks

| Risk | Mitigation |
|------|-----------|
| Global stylesheet conflicts with napari's internal styling | napari runs in its own QMainWindow with its own theme — Qt stylesheet inheritance is per-widget-tree, so the global stylesheet should not bleed into napari. Verify during testing. |
| Fusion style changes widget metrics (padding, sizes) | Fusion is close to macOS Aqua in metrics. Minor layout adjustments may be needed. |
| pyqtgraph ignores QSS | pyqtgraph uses its own rendering. `setBackground()` calls must remain per-plot. |
| Some widgets rely on system style for proper rendering | Fusion is the most stylesheet-compatible Qt style. This is actually lower risk than the current no-style approach. |

## References

### Internal
- Brainstorm: `docs/brainstorms/2026-04-05-ui-theme-consistency-brainstorm.md`
- Current styling audit: see brainstorm "Audit Summary" section
- App entry point: `src/percell4/app.py`

### Files to Modify
- `src/percell4/gui/theme.py` — **new**
- `src/percell4/app.py`
- `src/percell4/gui/launcher.py`
- `src/percell4/gui/import_dialog.py`
- `src/percell4/gui/compress_dialog.py`
- `src/percell4/gui/add_layer_dialog.py`
- `src/percell4/gui/segmentation_panel.py`
- `src/percell4/gui/cell_table.py`
- `src/percell4/gui/data_plot.py`
- `src/percell4/gui/phasor_plot.py`
- `src/percell4/gui/grouped_seg_panel.py`
- `src/percell4/gui/threshold_qc.py`
