"""Time-lapse-aware segment_one + the 4D-safe per-frame channel reader (U2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.store import DatasetStore
from percell4.workflows import phases
from percell4.workflows.models import CellposeSettings, EdgeMode
from percell4.workflows.phases import (
    _read_segmentation_channel_stack,
    segment_one,
)


def _interior_two_cells(h, w):
    """A 2-interior-cell label plane (survives edge + small filters)."""
    lab = np.zeros((h, w), dtype=np.int32)
    lab[h // 4 : h // 4 + 4, w // 4 : w // 4 + 4] = 1
    lab[h // 2 : h // 2 + 4, w // 2 : w // 2 + 4] = 2
    return lab


@pytest.fixture
def fake_seg(monkeypatch):
    """Patch run_cellpose to return 2 interior cells; record per-call shapes."""
    seen = []

    def _fake(plane, **kw):
        seen.append(plane.shape)
        return _interior_two_cells(plane.shape[-2], plane.shape[-1])

    monkeypatch.setattr(phases, "run_cellpose", _fake)
    return seen


def _store_with_intensity(path, intensity, dims):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"] if "C" not in dims else ["GFP", "RFP"]})
    store.write_array("intensity", intensity, attrs={"dims": dims})
    return store


def test_segment_one_timelapse_single_channel(tmp_path, fake_seg):
    intensity = np.ones((3, 40, 40), dtype=np.float32)  # (T, H, W)
    store = _store_with_intensity(tmp_path / "tl.h5", intensity, ["T", "H", "W"])
    assert store.metadata["n_timepoints"] == 3

    labels, failure, _msg = segment_one(
        store, CellposeSettings(min_size=1), edge_mode=EdgeMode.EXCLUDE
    )

    assert failure is None
    assert labels.shape == (3, 40, 40)
    assert store.read_labels("cellpose_qc").shape == (3, 40, 40)
    # Every frame handed to run_cellpose was 2D (the per-frame reader).
    assert all(len(s) == 2 for s in fake_seg)


def test_segment_one_timelapse_multichannel_4d(tmp_path, fake_seg):
    # (T, C, H, W) — read_channel would RAISE on this 4D layout.
    intensity = np.ones((3, 2, 40, 40), dtype=np.float32)
    store = _store_with_intensity(
        tmp_path / "tl4d.h5", intensity, ["T", "C", "H", "W"]
    )
    assert store.metadata["n_timepoints"] == 3

    labels, failure, _msg = segment_one(
        store, CellposeSettings(min_size=1), channel_idx=0
    )

    assert failure is None
    assert labels.shape == (3, 40, 40)
    # The per-frame reader fed run_cellpose a 2D (H,W) plane each time — not
    # the 4D stack or a 3D (C,H,W) frame.
    assert fake_seg == [(40, 40), (40, 40), (40, 40)]


def test_segment_one_single_timepoint_unchanged(tmp_path, fake_seg):
    intensity = np.ones((2, 40, 40), dtype=np.float32)  # (C, H, W), 1 timepoint
    store = _store_with_intensity(tmp_path / "still.h5", intensity, ["C", "H", "W"])
    assert store.metadata["n_timepoints"] == 1

    labels, failure, _msg = segment_one(store, CellposeSettings(min_size=1))

    assert failure is None
    assert labels.ndim == 2  # 2D, exactly as today
    assert store.read_labels("cellpose_qc").ndim == 2


def test_read_segmentation_channel_stack_picks_channel(tmp_path):
    # (T=2, C=2, H, W): channel 1 distinguishable from channel 0.
    intensity = np.zeros((2, 2, 8, 8), dtype=np.float32)
    intensity[:, 1] = 7.0
    store = _store_with_intensity(tmp_path / "s.h5", intensity, ["T", "C", "H", "W"])

    stack0 = _read_segmentation_channel_stack(store, channel_idx=0, n_timepoints=2)
    stack1 = _read_segmentation_channel_stack(store, channel_idx=1, n_timepoints=2)
    assert stack0.shape == (2, 8, 8)
    assert stack0.max() == 0.0
    assert stack1.max() == 7.0
