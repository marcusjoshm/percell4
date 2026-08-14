"""Data tab → Rename Channel on an unnamed /intensity slice.

Bug: a dataset whose ``/intensity`` carries more slices than
``metadata.channel_names`` shows the extra slices under synthesized
``ch<N>`` names, and the Channels dropdown offers them (deliberate orphan
support — see ``test_data_panel_channel_combo.py``). Renaming one used to
call ``DatasetStore.rename_channel``, which was a documented silent no-op
for every surface it could not find. Nothing was written, no exception was
raised, and the handler reported ``Renamed channel 'ch1' → 'ER'`` — the
name reverted on the next reload.

These run against a real ``DatasetStore``: the defect lived in the seam
between the handler and the store, so a fake store would not have caught it.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.task_panels import data_panel as dp
from percell4.interfaces.gui.task_panels.data_panel import DataPanel
from percell4.model import CellDataModel
from percell4.store import DatasetStore


def _make_h5(path: Path, *, names: list[str], n_slices: int) -> None:
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = names
        meta.attrs["n_channels"] = n_slices
        meta.attrs["n_timepoints"] = 1
        ds = f.create_dataset(
            "intensity", data=np.zeros((n_slices, 4, 4), dtype=np.float32)
        )
        ds.attrs["dims"] = ["C", "H", "W"]


@pytest.fixture
def panel(qtbot, tmp_path):
    """DataPanel over a real store whose /intensity has 2 slices but only
    one name — so slice 1 displays as the placeholder ``ch1``."""
    h5 = tmp_path / "exp.h5"
    _make_h5(h5, names=["G3BP1"], n_slices=2)
    store = DatasetStore(h5)

    session = Session()
    model = CellDataModel(session=session)
    session.set_dataset(
        DatasetHandle(path=h5, metadata={"channel_names": ["G3BP1"]})
    )

    status: list[str] = []
    p = DataPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(h5),
        show_status=status.append,
    )
    qtbot.addWidget(p)
    return p, session, store, status


def test_renaming_an_unnamed_slice_persists_to_disk(panel, monkeypatch):
    p, session, store, status = panel
    monkeypatch.setattr(dp, "text_input", lambda *a, **kw: ("ER", True))

    p._mgmt_chan_combo.clear()
    p._mgmt_chan_combo.addItems(["G3BP1", "ch1"])
    p._mgmt_chan_combo.setCurrentText("ch1")
    p._on_rename_channel()

    assert DatasetStore(store.path).metadata["channel_names"] == ["G3BP1", "ER"]
    assert status[-1] == "Renamed channel 'ch1' → 'ER'"


def test_session_metadata_picks_up_the_appended_name(panel, monkeypatch):
    """The promotion APPENDS a name — an index-substitution sync leaves the
    in-memory handle stale, and peer views keep showing the placeholder."""
    p, session, store, _status = panel
    monkeypatch.setattr(dp, "text_input", lambda *a, **kw: ("ER", True))

    p._mgmt_chan_combo.clear()
    p._mgmt_chan_combo.addItems(["G3BP1", "ch1"])
    p._mgmt_chan_combo.setCurrentText("ch1")
    p._on_rename_channel()

    assert session.dataset.metadata["channel_names"] == ["G3BP1", "ER"]
    assert session.dataset.metadata["n_channels"] == 2


def test_rename_that_matches_nothing_reports_failure(panel, monkeypatch):
    """Regression: the handler must not claim success for a rename the store
    could not apply."""
    p, _session, store, status = panel
    monkeypatch.setattr(dp, "text_input", lambda *a, **kw: ("ER", True))

    p._mgmt_chan_combo.clear()
    p._mgmt_chan_combo.addItems(["ghost"])
    p._mgmt_chan_combo.setCurrentText("ghost")
    p._on_rename_channel()

    assert status[-1].startswith("Rename failed:")
    assert DatasetStore(store.path).metadata["channel_names"] == ["G3BP1"]


def test_renaming_a_real_channel_still_works(panel, monkeypatch):
    p, session, store, status = panel
    monkeypatch.setattr(dp, "text_input", lambda *a, **kw: ("SG", True))

    p._mgmt_chan_combo.clear()
    p._mgmt_chan_combo.addItems(["G3BP1", "ch1"])
    p._mgmt_chan_combo.setCurrentText("G3BP1")
    p._on_rename_channel()

    assert DatasetStore(store.path).metadata["channel_names"] == ["SG"]
    assert session.dataset.metadata["channel_names"] == ["SG"]
    assert status[-1] == "Renamed channel 'G3BP1' → 'SG'"
