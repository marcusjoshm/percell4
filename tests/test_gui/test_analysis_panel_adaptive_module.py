"""U5: the Adaptive Local Clipping module mounts after Grouped Thresholding."""

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


def test_adaptive_module_present_and_positioned(qtbot):
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

    titles = _ordered_group_titles(panel)
    assert "Adaptive Local Clipping" in titles
    # Immediately after Grouped Thresholding, and before Measurements.
    assert titles.index("Adaptive Local Clipping") == titles.index("Grouped Thresholding") + 1
    assert titles.index("Adaptive Local Clipping") < titles.index("Measurements")


def test_iterative_otsu_module_present_and_positioned(qtbot):
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

    titles = _ordered_group_titles(panel)
    assert "Iterative Otsu Thresholding" in titles
    # Mounts right after Adaptive Local Clipping, before Measurements.
    assert (
        titles.index("Iterative Otsu Thresholding")
        == titles.index("Adaptive Local Clipping") + 1
    )
    assert titles.index("Iterative Otsu Thresholding") < titles.index("Measurements")
    assert panel._iterative_otsu_panel is not None
