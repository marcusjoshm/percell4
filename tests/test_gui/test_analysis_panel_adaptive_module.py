"""U7: the Analysis tab modules render in detection-first order."""

from __future__ import annotations

from qtpy.QtWidgets import QGroupBox


def _ordered_group_titles(panel) -> list[str]:
    """Top-level module group titles in visual (layout) order."""
    layout = panel.layout()
    titles = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if isinstance(w, QGroupBox):
            titles.append(w.title())
    return titles


def test_analysis_modules_in_detection_first_order(qtbot):
    from percell4.interfaces.gui.task_panels.analysis_panel import AnalysisPanel
    from percell4.model import CellDataModel

    panel = AnalysisPanel(
        CellDataModel(),
        get_repo=lambda: None,
        get_viewer_window=lambda: None,
        get_phasor_roi_names=lambda: None,
        show_window=lambda _n: None,
        get_store=lambda: None,
        show_status=lambda _m: None,
    )
    qtbot.addWidget(panel)

    assert _ordered_group_titles(panel) == [
        "Adaptive Local Clipping",
        "Particle Analysis",
        "Measurements",
        "Grouped Thresholding",
        "Whole Field Thresholding",
    ]


def test_iterative_otsu_module_removed(qtbot):
    """The Iterative Otsu Thresholding GUI module no longer exists (U5)."""
    from percell4.interfaces.gui.task_panels.analysis_panel import AnalysisPanel
    from percell4.model import CellDataModel

    panel = AnalysisPanel(
        CellDataModel(),
        get_repo=lambda: None,
        get_viewer_window=lambda: None,
        get_phasor_roi_names=lambda: None,
        show_window=lambda _n: None,
        get_store=lambda: None,
        show_status=lambda _m: None,
    )
    qtbot.addWidget(panel)

    assert "Iterative Otsu Thresholding" not in _ordered_group_titles(panel)
    assert not hasattr(panel, "_iterative_otsu_panel")
