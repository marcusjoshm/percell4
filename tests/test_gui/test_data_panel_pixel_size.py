"""U2 tests: DataPanel surfaces pixel_size_um in Dataset Info.

The dataset's linear µm/px (and derived µm²/px) lands on the info
label; when the session's active_bin > 1, an additional view-bin
effective line appears so users can reason about exported TIFFs.
Legacy datasets without ``pixel_size_um`` render as ``unknown``.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.task_panels.data_panel import (
    DataPanel,
    _format_pixel_size_lines,
)
from percell4.model import CellDataModel
from percell4.store import DatasetStore

# ── Pure formatter unit tests (no Qt) ─────────────────────────────────


def test_format_pixel_size_lines_known():
    text = _format_pixel_size_lines(0.12034, active_bin=1)
    assert "0.1203 µm/px" in text
    assert "0.01448 µm²/px" in text
    assert "View-bin pixel size" not in text


def test_format_pixel_size_lines_appends_view_bin_when_above_one():
    text = _format_pixel_size_lines(0.12034, active_bin=2)
    assert "0.1203 µm/px" in text
    assert "View-bin pixel size: 0.2407 µm/px" in text
    assert "0.05793 µm²/px" in text


def test_format_pixel_size_lines_unknown_when_none():
    assert _format_pixel_size_lines(None, active_bin=1) == "Pixel size: unknown"


def test_format_pixel_size_lines_unknown_when_zero():
    """Defensive: a stored zero or negative renders as unknown."""
    assert _format_pixel_size_lines(0.0, active_bin=1) == "Pixel size: unknown"
    assert _format_pixel_size_lines(-0.1, active_bin=1) == "Pixel size: unknown"


# ── Qt-backed integration tests (real DataPanel + DatasetStore) ───────


@pytest.fixture
def panel_with_pixel_size(qtbot, tmp_path):
    """DataPanel backed by a real .h5 with pixel_size_um in /metadata."""
    session = Session()
    model = CellDataModel(session=session)

    h5_path = tmp_path / "exp.h5"
    store = DatasetStore(h5_path)
    store.create(
        metadata={
            "channel_names": ["ch00"],
            "pixel_size_um": 0.12034,
            "native_shape": (32, 32),
            "creation_bin": 1,
        }
    )
    store.write_array("intensity", np.zeros((32, 32), dtype=np.uint16))

    handle = DatasetHandle(
        path=h5_path,
        metadata={
            "channel_names": ["ch00"],
            "segmentation_names": [],
            "mask_names": [],
            "native_shape": (32, 32),
            "creation_bin": 1,
            "pixel_size_um": 0.12034,
        },
    )
    session.set_dataset(handle)

    p = DataPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(h5_path),
    )
    qtbot.addWidget(p)
    return p, session, store


def test_info_label_shows_pixel_size(panel_with_pixel_size):
    panel, _session, _store = panel_with_pixel_size
    panel.refresh_dataset_info()
    text = panel._info_label.text()
    assert "Pixel size: 0.1203 µm/px" in text
    assert "0.01448 µm²/px" in text


def test_info_label_appends_view_bin_line(panel_with_pixel_size):
    """Bumping active_bin surfaces the effective view-bin pixel size."""
    panel, session, _store = panel_with_pixel_size
    session.set_active_bin(2)
    text = panel._info_label.text()
    assert "Pixel size: 0.1203 µm/px" in text
    assert "View-bin pixel size: 0.2407 µm/px" in text


def test_info_label_unknown_when_metadata_missing(qtbot, tmp_path):
    """Legacy dataset without pixel_size_um → 'Pixel size: unknown'."""
    session = Session()
    model = CellDataModel(session=session)

    h5_path = tmp_path / "legacy.h5"
    store = DatasetStore(h5_path)
    store.create(
        metadata={
            "channel_names": ["ch00"],
            "native_shape": (16, 16),
            "creation_bin": 1,
        }
    )
    store.write_array("intensity", np.zeros((16, 16), dtype=np.uint16))

    handle = DatasetHandle(
        path=h5_path,
        metadata={
            "channel_names": ["ch00"],
            "segmentation_names": [],
            "mask_names": [],
            "native_shape": (16, 16),
            "creation_bin": 1,
        },
    )
    session.set_dataset(handle)

    p = DataPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(h5_path),
    )
    qtbot.addWidget(p)
    p.refresh_dataset_info()
    assert "Pixel size: unknown" in p._info_label.text()


def test_info_label_no_dataset_unchanged(qtbot, tmp_path):
    """When no dataset is loaded, the label stays at its baseline."""
    session = Session()
    model = CellDataModel(session=session)
    p = DataPanel(
        data_model=model,
        get_store=lambda: None,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: None,
    )
    qtbot.addWidget(p)
    p.refresh_dataset_info()
    assert p._info_label.text() == "No dataset loaded"
