"""Regression test for the Layer Management → Channels dropdown.

Bug: ``refresh_management_combos`` populated the channels combo by iterating
``viewer.layers``. When called before the viewer was populated (the normal
order in ``_open_dataset`` — refresh runs at line 836, viewer populates at
line 840), the combo landed with zero items, which renders as an
unclickable QComboBox in Qt.

Fix: source channel names from ``session.dataset.metadata["channel_names"]``
first (canonical), then union with napari Image layers for orphan support.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.task_panels.data_panel import DataPanel
from percell4.model import CellDataModel


@pytest.fixture
def panel(qtbot, tmp_path):
    """Build a DataPanel where the viewer-window getter returns ``None`` —
    matching the moment in ``_open_dataset`` when refresh runs but the viewer
    hasn't been populated yet.
    """
    session = Session()
    model = CellDataModel(session=session)
    dummy_h5 = tmp_path / "exp.h5"
    handle = DatasetHandle(
        path=dummy_h5,
        metadata={"channel_names": ["CA-SiR", "mNG", "mTQ2"]},
    )
    session.set_dataset(handle)

    fake_store = SimpleNamespace(
        list_labels=lambda: [],
        list_masks=lambda: [],
    )

    p = DataPanel(
        data_model=model,
        get_store=lambda: fake_store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(dummy_h5),
    )
    qtbot.addWidget(p)
    return p, session, model


def test_channels_combo_populated_from_metadata_when_viewer_not_yet_built(panel):
    """Regression: combo must list channels from metadata even with no viewer.

    This is the exact ordering in ``_open_dataset`` — ``set_dataset`` runs
    BEFORE the viewer window is built/populated, so a refresh at that
    moment used to leave the Channels dropdown empty (= unclickable).
    """
    p, _session, _model = panel
    p.refresh_management_combos()
    items = [p._mgmt_chan_combo.itemText(i) for i in range(p._mgmt_chan_combo.count())]
    assert items == ["CA-SiR", "mNG", "mTQ2"]


def test_channels_combo_unions_napari_orphans_with_metadata(qtbot, tmp_path):
    """Orphan napari Image layers (not in channel_names) still appear so
    the user can delete them via this UI.
    """
    session = Session()
    model = CellDataModel(session=session)
    dummy_h5 = tmp_path / "exp.h5"
    handle = DatasetHandle(
        path=dummy_h5,
        metadata={"channel_names": ["CA-SiR", "mNG"]},
    )
    session.set_dataset(handle)
    fake_store = SimpleNamespace(list_labels=lambda: [], list_masks=lambda: [])

    # Fake viewer with one named-in-metadata Image layer + one orphan.
    # The refresh code checks ``layer.__class__.__name__ == "Image"``, so
    # we define a real class literally named "Image".
    class Image:
        def __init__(self, name): self.name = name
    layers = [Image("CA-SiR"), Image("ch5")]
    fake_viewer = SimpleNamespace(layers=layers)
    fake_viewer_win = SimpleNamespace(viewer=fake_viewer)

    p = DataPanel(
        data_model=model,
        get_store=lambda: fake_store,
        get_viewer_window=lambda: fake_viewer_win,
        get_h5_path=lambda: str(dummy_h5),
    )
    qtbot.addWidget(p)
    p.refresh_management_combos()
    items = [p._mgmt_chan_combo.itemText(i) for i in range(p._mgmt_chan_combo.count())]
    # Metadata first, then napari orphans (CA-SiR not duplicated)
    assert items == ["CA-SiR", "mNG", "ch5"]
