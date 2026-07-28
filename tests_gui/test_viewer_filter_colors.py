"""Filter-only rendering keeps cells distinguishable.

When a filter is active with no selection, the segmentation labels layer is
repainted via a ``DirectLabelColormap`` that shows only the filtered cells.
Regression guard: those cells must keep distinct colors (sampled from the
layer's original palette), not collapse into one flat ``FILTER_ONLY_VISIBLE``
teal, and non-filtered labels must be transparent.
"""

from __future__ import annotations

import numpy as np
import pytest
from napari.utils.colormaps import DirectLabelColormap

from percell4.application.session import Session
from percell4.config import viewer_presets as vp
from percell4.gui.viewer import ViewerWindow
from percell4.model import CellDataModel


@pytest.fixture
def viewer_harness(qtbot):
    session = Session()
    data_model = CellDataModel(session=session)
    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()
    yield viewer_win, session
    try:
        viewer_win.viewer.close()
    except Exception:
        pass


def _seg() -> np.ndarray:
    """4 labelled cells (1..4) on an 8x8 grid."""
    arr = np.zeros((8, 8), dtype=np.int32)
    arr[0:4, 0:4] = 1
    arr[0:4, 4:8] = 2
    arr[4:8, 0:4] = 3
    arr[4:8, 4:8] = 4
    return arr


def test_filter_only_paints_distinct_colors(viewer_harness):
    viewer_win, session = viewer_harness
    viewer_win.add_labels(_seg(), "seg")
    session.set_active_segmentation("seg")

    session.set_filter(frozenset({1, 2, 3}))  # triggers _update_label_display

    cmap = viewer_win.viewer.layers["seg"].colormap
    assert isinstance(cmap, DirectLabelColormap)
    cd = cmap.color_dict

    # Filtered cells are present and mutually distinct.
    c1, c2, c3 = (tuple(np.asarray(cd[i])) for i in (1, 2, 3))
    assert len({c1, c2, c3}) == 3
    # ...and not the flat filter-only fallback color.
    flat = tuple(float(x) for x in vp.FILTER_ONLY_VISIBLE_RGBA)
    assert c1 != flat and c2 != flat and c3 != flat

    # Non-filtered labels fall through to a transparent default.
    assert tuple(np.asarray(cd[None])) == (0.0, 0.0, 0.0, 0.0)
