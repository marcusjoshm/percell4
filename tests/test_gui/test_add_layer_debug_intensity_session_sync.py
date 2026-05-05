"""Regression test: TCSPC tab debug-intensity write must sync session metadata.

Bug: ``_tcspc_write_bin_intensity_debug_layers`` wrote new channel_names to
the .h5 store but did not update ``session.dataset.metadata`` (the in-memory
snapshot the Data tab combos read from). After adding ``<ch>_bin`` debug
intensity layers, the Data tab still showed only the original channels
until the dataset was reloaded.

Fix: call ``session.refresh_resource_lists(channel_names=all_names)`` after
the store write — same pattern the Single TIFF tab path uses at
``add_layer_dialog.py:749``.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest
from qtpy.QtWidgets import QWidget

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.add_layer_dialog import AddLayerDialog
from percell4.model import CellDataModel
from percell4.store import DatasetStore


@pytest.fixture
def dataset_with_decay(tmp_path):
    """Real .h5 with /intensity (3 ch) + /decay/<ch> for each channel.

    Mirrors the post-add-layer state where ``add_decay_to_dataset`` already
    wrote /decay/<ch> for the three TIFF channels and we're about to call
    the debug-intensity helper to materialize ``<ch>_bin`` layers.
    """
    path = tmp_path / "exp.h5"
    store = DatasetStore(path)
    names = ["CA-SiR", "mNG", "mTQ2"]
    store.create(metadata={"channel_names": names})
    intensity = np.zeros((3, 8, 8), dtype=np.float32)
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    # Synthesize tiny /decay/<ch> for each — sum-over-T must produce (8, 8)
    with h5py.File(path, "a") as f:
        for ch in names:
            f.create_dataset(
                f"decay/{ch}",
                data=np.ones((8, 8, 4), dtype=np.float32),
            )
    return path, store, names


@pytest.fixture
def dialog(qtbot, dataset_with_decay):
    path, store, names = dataset_with_decay
    session = Session()
    session.set_dataset(DatasetHandle(path=path, metadata={"channel_names": list(names)}))
    data_model = CellDataModel(session)

    launcher = QWidget()
    qtbot.addWidget(launcher)
    dlg = AddLayerDialog(launcher, store, data_model, viewer_win=None)
    qtbot.addWidget(dlg)
    return dlg, session, names


def test_debug_intensity_write_updates_session_metadata(dialog):
    """After writing <ch>_bin debug layers, session.dataset.metadata
    reflects the new channel_names — without the fix, only on-disk
    /metadata changes and the Data tab combos stay stale."""
    dlg, session, names = dialog

    dlg._tcspc_write_bin_intensity_debug_layers(list(names))

    expected = list(names) + [f"{ch}_bin" for ch in names]
    in_memory = list(session.dataset.metadata.get("channel_names", []))
    assert in_memory == expected, (
        f"session.dataset.metadata not updated: got {in_memory}, expected {expected}"
    )


def test_debug_intensity_write_emits_channel_list_changed(dialog):
    """The channel_list event must fire so DataPanel subscribers re-list."""
    from percell4.application.session import Event
    dlg, session, names = dialog
    received = []
    session.subscribe(Event.CHANNEL_LIST_CHANGED, lambda: received.append(True))

    dlg._tcspc_write_bin_intensity_debug_layers(list(names))

    assert received, "CHANNEL_LIST_CHANGED was not emitted"
