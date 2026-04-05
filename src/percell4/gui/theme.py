"""Centralized dark theme for PerCell4.

All color constants and the global application stylesheet live here.
Individual GUI files import constants for dynamic styling only.
"""

# ── Color palette ──────────────────────────────────────────────────
BACKGROUND_DEEP = "#121212"    # Launcher main area
BACKGROUND = "#1e1e1e"         # Dialogs, panels, standalone windows
SURFACE = "#2a2a2a"            # Input fields, buttons, combo boxes
BORDER = "#3a3a3a"             # Group box borders, dividers
BORDER_INPUT = "#444444"       # Input field borders
ACCENT = "#4ea8de"             # Focus borders, active labels, hover
TEXT = "#e0e0e0"               # Primary text
TEXT_BRIGHT = "#ffffff"        # Headings, selected items
TEXT_MUTED = "#888888"         # Disabled/inactive text
TEXT_LABEL = "#cccccc"         # Form labels
SELECTION = "#2a4a6b"          # Selected row/item highlight
SUCCESS = "#66cc66"            # Success status
WARNING = "#ffaa44"            # Warning/in-progress status
ERROR = "#ff6666"              # Error status
ACTION_GREEN = "#2d7d46"       # Green action buttons (Run, Accept)
ACTION_GREEN_HOVER = "#35a355" # Green button hover
SIDEBAR = "#1e2a3a"            # Launcher sidebar
SIDEBAR_ACTIVE = "#0d1b2a"    # Launcher sidebar checked state
SIDEBAR_HOVER = "#2a3d52"     # Launcher sidebar hover

APP_STYLESHEET = f"""
    /* ── Base ── */
    QMainWindow, QDialog, QWidget {{ background-color: {BACKGROUND}; color: {TEXT}; }}

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
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background-color: {BORDER};
        border: none;
        border-left: 1px solid {BORDER_INPUT};
        width: 18px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {BORDER_INPUT};
    }}
    QComboBox QAbstractItemView {{
        background-color: {SURFACE};
        color: {TEXT_BRIGHT};
        border: 1px solid {BORDER};
        selection-background-color: {SELECTION};
    }}
    QComboBox::drop-down {{
        border: none;
        border-left: 1px solid {BORDER_INPUT};
        width: 20px;
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
    QCheckBox::indicator, QGroupBox::indicator {{ width: 16px; height: 16px; }}
    QCheckBox::indicator:unchecked, QGroupBox::indicator:unchecked {{
        border: 1px solid #555555; border-radius: 3px; background-color: {SURFACE};
    }}
    QCheckBox::indicator:checked, QGroupBox::indicator:checked {{
        border: 1px solid {ACCENT}; border-radius: 3px; background-color: {ACCENT};
    }}

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

    /* ── Scrollbars ── */
    QScrollBar:vertical {{
        background-color: {BACKGROUND};
        width: 12px;
        border: none;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background-color: {BORDER};
        border-radius: 4px;
        min-height: 20px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {BORDER_INPUT};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}
    QScrollBar:horizontal {{
        background-color: {BACKGROUND};
        height: 12px;
        border: none;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background-color: {BORDER};
        border-radius: 4px;
        min-width: 20px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background-color: {BORDER_INPUT};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
    }}

    /* ── Dialog button boxes ── */
    QDialogButtonBox QPushButton {{ min-width: 80px; }}
"""


def apply_theme(app) -> None:
    """Apply Fusion style and dark theme to the QApplication."""
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
