"""Tests for the Cellpose name prompt (U2).

The Cellpose flow now prompts the user for a segmentation name before
running the Worker. The chosen name is threaded through to
``SegmentCells.finalize(name=...)`` and ends up at ``/labels/<name>``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.gui import segmentation_panel as sp_module


# ── SegmentCells.finalize: optional name parameter ───────────────────


def test_finalize_uses_supplied_name_when_provided(tmp_path) -> None:
    """Explicit name lands as the seg_name; auto-derive is bypassed."""
    import h5py

    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.application.session import Session
    from percell4.application.use_cases.segment_cells import SegmentCells
    from percell4.domain.dataset import DatasetHandle

    h5 = tmp_path / "ds.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    handle = DatasetHandle(path=h5, metadata={})
    session = Session()
    session.set_dataset(handle)

    repo = Hdf5DatasetRepository()
    uc = SegmentCells(repo, session)
    raw = np.zeros((20, 20), dtype=np.int32)
    raw[5:10, 5:10] = 1
    raw[12:17, 12:17] = 2

    result = uc.finalize(raw, name="my_cells", remove_edge_cells=False, min_area=4)
    assert result.seg_name == "my_cells"
    with h5py.File(h5, "r") as f:
        assert "labels/my_cells" in f


def test_finalize_falls_back_to_auto_name_when_name_is_none(tmp_path) -> None:
    """name=None preserves the historical f'cellpose_{n_cells}' behavior."""
    import h5py

    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.application.session import Session
    from percell4.application.use_cases.segment_cells import SegmentCells
    from percell4.domain.dataset import DatasetHandle

    h5 = tmp_path / "ds.h5"
    with h5py.File(h5, "w") as f:
        f.create_group("metadata")

    handle = DatasetHandle(path=h5, metadata={})
    session = Session()
    session.set_dataset(handle)

    repo = Hdf5DatasetRepository()
    uc = SegmentCells(repo, session)
    raw = np.zeros((20, 20), dtype=np.int32)
    raw[5:10, 5:10] = 1
    raw[12:17, 12:17] = 2

    result = uc.finalize(raw, remove_edge_cells=False, min_area=4)
    assert result.seg_name == "cellpose_2"


# ── _on_run_cellpose: prompt up front, cancel cheap ──────────────────


def _build_panel(qtbot, monkeypatch, *, channel="ch0", existing_labels=None):
    """Construct a SegmentationPanel with a mocked launcher/store/viewer."""
    from percell4.gui.segmentation_panel import SegmentationPanel
    from percell4.model import CellDataModel

    existing_labels = existing_labels or []
    model = CellDataModel()
    model.session.set_active_channel(channel)

    # Stub store
    store = MagicMock()
    store.list_labels.return_value = list(existing_labels)
    store.list_masks.return_value = []

    # Stub viewer window with one Image layer matching the channel
    layer = MagicMock()
    layer.__class__ = type("Image", (MagicMock,), {})  # name dunder via class
    layer.name = channel
    layer.data = np.zeros((10, 10), dtype=np.uint16)
    # Need layer.__class__.__name__ == "Image"
    layer.__class__.__name__ = "Image"

    viewer = MagicMock()
    viewer.layers = [layer]
    viewer_win = MagicMock()
    viewer_win.viewer = viewer

    launcher = MagicMock()
    launcher._current_store = store
    launcher._windows = {"viewer": viewer_win}
    launcher.statusBar.return_value.showMessage = MagicMock()

    panel = SegmentationPanel(data_model=model, launcher=launcher)
    qtbot.addWidget(panel)
    return panel, store


def test_run_cellpose_aborts_when_user_cancels_prompt(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel returns None from the helper → no Worker is started."""
    panel, _store = _build_panel(qtbot, monkeypatch)

    # Stub the helper to return None (user cancelled).
    monkeypatch.setattr(
        sp_module, "prompt_for_resource_name", lambda *a, **kw: None
    )

    # Spy on Worker — if instantiated, the test fails.
    worker_calls: list[Any] = []
    import percell4.gui.workers as workers_mod

    class FakeWorker:
        def __init__(self, *a, **kw):  # noqa: ARG002
            worker_calls.append((a, kw))
            self.finished = MagicMock()
            self.error = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)
    panel._on_run_cellpose()
    assert worker_calls == []


def test_run_cellpose_threads_chosen_name_into_pending_attr(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chosen name lands on self._cellpose_pending_name and gets passed
    to finalize at _on_cellpose_done time."""
    panel, _store = _build_panel(qtbot, monkeypatch)

    monkeypatch.setattr(
        sp_module, "prompt_for_resource_name", lambda *a, **kw: "my_cells"
    )

    # Stub Worker to not actually run anything async.
    import percell4.gui.workers as workers_mod

    class FakeWorker:
        def __init__(self, *a, **kw):  # noqa: ARG002
            self.finished = MagicMock()
            self.error = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(workers_mod, "Worker", FakeWorker)

    panel._on_run_cellpose()
    assert panel._cellpose_pending_name == "my_cells"


def test_run_cellpose_helper_called_with_existing_segmentation_set(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """existing_names passed to the helper is list_labels - list_masks."""
    panel, store = _build_panel(
        qtbot, monkeypatch, existing_labels=["cellpose", "manual", "phasor_mask"]
    )
    store.list_masks.return_value = ["phasor_mask"]

    captured: dict[str, Any] = {}

    def fake_prompt(parent, *, title, label, default, existing_names):  # noqa: ARG001
        captured["existing"] = set(existing_names)
        captured["default"] = default
        return None  # cancel out — we only care about the args

    monkeypatch.setattr(sp_module, "prompt_for_resource_name", fake_prompt)

    panel._on_run_cellpose()
    assert captured["existing"] == {"cellpose", "manual"}  # masks excluded
    assert captured["default"] == "cellpose"


def test_run_cellpose_aborts_when_no_active_channel(qtbot, monkeypatch) -> None:
    """No channel selected → status message, no prompt opened."""
    panel, _store = _build_panel(qtbot, monkeypatch, channel="")
    prompt_calls = []
    monkeypatch.setattr(
        sp_module,
        "prompt_for_resource_name",
        lambda *a, **kw: prompt_calls.append(1) or None,
    )
    panel._on_run_cellpose()
    assert prompt_calls == []
