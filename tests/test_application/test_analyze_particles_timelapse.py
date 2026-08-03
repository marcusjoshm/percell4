"""Time-aware particle analysis: per-frame loop + conditional merge (U13)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from percell4.application.session import Session
from percell4.application.use_cases.analyze_particles import AnalyzeParticles
from percell4.domain.dataset import DatasetHandle


class FakeParticleRepo:
    """Per-frame channel/labels/mask backing for AnalyzeParticles."""

    def __init__(self, channel, labels, mask):
        self._ch = channel    # (T,H,W)
        self._lbl = labels     # (T,H,W)
        self._mask = mask      # (T,H,W)
        self.written = None

    def read_channel_images(self, handle, view_bin=1, timepoint=None):
        arr = self._ch
        if arr.ndim == 3:
            arr = arr[0 if timepoint is None else timepoint]
        return {"GFP": arr.astype(np.float32)}

    def read_labels(self, handle, name, view_bin=1, timepoint=None):
        if timepoint is None or self._lbl.ndim == 2:
            return self._lbl
        return self._lbl[timepoint]

    def read_mask(self, handle, name, view_bin=1, timepoint=None):
        if timepoint is None or self._mask.ndim == 2:
            return self._mask
        return self._mask[timepoint]

    def write_measurements(self, handle, df):
        self.written = df


def _session(n_timepoints):
    s = Session()
    s.set_dataset(
        DatasetHandle(path=Path("/tmp/m.h5"), metadata={"n_timepoints": n_timepoints})
    )
    s.set_active_segmentation("cp")
    s.set_active_mask("thr")
    return s


def _two_cell_labels():
    f = np.zeros((20, 20), dtype=np.int32)
    f[4:9, 4:9] = 1
    f[11:16, 11:16] = 2
    return f


def _mask_with_particles():
    m = np.zeros((20, 20), dtype=np.uint8)
    m[5:7, 5:7] = 1   # particle in cell 1
    m[12:14, 12:14] = 1  # particle in cell 2
    return m


def test_particles_tagged_per_timepoint():
    labels = np.stack([_two_cell_labels(), _two_cell_labels()], axis=0)
    channel = np.ones((2, 20, 20), dtype=np.float32)
    mask = np.stack([_mask_with_particles(), _mask_with_particles()], axis=0)
    repo = FakeParticleRepo(channel, labels, mask)

    res = AnalyzeParticles(repo, _session(2)).execute(min_area=1)

    assert "timepoint" in res.summary_df.columns
    assert sorted(res.summary_df["timepoint"].unique().tolist()) == [0, 1]
    # 2 cells x 2 frames = 4 summary rows.
    assert len(res.summary_df) == 4
    assert res.total_particles > 0
    assert "timepoint" in res.detail_df.columns


def test_merge_does_not_cartesian_explode():
    """With a pre-existing time-lapse measurements df, the particle merge keys
    on (label, timepoint) -> exactly n_cells * n_timepoints rows, not the
    cartesian product (the label-only-merge regression)."""
    labels = np.stack([_two_cell_labels(), _two_cell_labels()], axis=0)
    channel = np.ones((2, 20, 20), dtype=np.float32)
    mask = np.stack([_mask_with_particles(), _mask_with_particles()], axis=0)
    repo = FakeParticleRepo(channel, labels, mask)
    session = _session(2)

    # Pre-existing measurements: 2 cells x 2 timepoints = 4 rows.
    existing = pd.DataFrame(
        {
            "label": [1, 2, 1, 2],
            "timepoint": [0, 0, 1, 1],
            "area_pixels": [25, 25, 25, 25],
        }
    )
    session.set_measurements(existing)

    AnalyzeParticles(repo, session).execute(min_area=1)

    merged = repo.written
    # Exactly 4 rows (2 cells x 2 frames) -- NOT 8 (cartesian).
    assert len(merged) == 4
    assert "particle_count" in merged.columns
    assert set(zip(merged["label"], merged["timepoint"])) == {
        (1, 0), (2, 0), (1, 1), (2, 1)
    }


def test_single_timepoint_no_timepoint_column():
    """Single-timepoint analysis is byte-identical: no timepoint column."""
    labels = _two_cell_labels()             # 2D
    channel = np.ones((20, 20), dtype=np.float32)  # 2D
    mask = _mask_with_particles()           # 2D
    repo = FakeParticleRepo(channel, labels, mask)

    res = AnalyzeParticles(repo, _session(1)).execute(min_area=1)
    assert "timepoint" not in res.summary_df.columns
    assert len(res.summary_df) == 2
