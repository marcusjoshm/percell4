"""Per-timepoint measurement + particle detail (U5)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from percell4.store import DatasetStore
from percell4.workflows.models import ParticleSettings, ThresholdAlgorithm, ThresholdingRound
from percell4.workflows.phases import measure_one, measure_particles_one


def _tracked_store(path: Path) -> DatasetStore:
    """2-timepoint dataset with a tracked seg (label==track_id) + lineage.

    Frame 0: tracks 1 and 2. Frame 1: only track 1 (track 2 died).
    """
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.ones((2, 20, 20), dtype=np.float32),
                      attrs={"dims": ["T", "H", "W"]})
    labels = np.zeros((2, 20, 20), dtype=np.int32)
    labels[0, 2:6, 2:6] = 1
    labels[0, 10:14, 10:14] = 2
    labels[1, 2:6, 2:6] = 1
    store.write_labels("tracked", labels)
    lineage = pd.DataFrame({
        "track_id": [1, 2], "tree_id": [0, 1],
        "begin_t": [0, 0], "end_t": [1, 0], "parent_track_id": [-1, -1],
    })
    store.write_tracks("tracked", lineage)
    return store


def test_measure_one_timelapse_tags_timepoint_and_track_columns(tmp_path):
    store = _tracked_store(tmp_path / "tl.h5")

    df, failure, _msg = measure_one(store, round_specs=[], seg_name="tracked")

    assert failure is None
    assert "timepoint" in df.columns
    assert sorted(df["timepoint"].unique()) == [0, 1]
    # 2 cells at t0 + 1 cell at t1 = 3 rows.
    assert len(df) == 3
    # Tracked seg: track_id == label, lineage columns joined.
    assert (df["track_id"] == df["label"]).all()
    assert "tree_id" in df.columns
    assert "parent_track_id" in df.columns
    assert set(df.loc[df["track_id"] == 1, "parent_track_id"]) == {-1}


def test_measure_one_frame_with_no_cells_contributes_no_rows(tmp_path):
    store = DatasetStore(tmp_path / "gap.h5")
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.ones((2, 20, 20), dtype=np.float32),
                      attrs={"dims": ["T", "H", "W"]})
    labels = np.zeros((2, 20, 20), dtype=np.int32)
    labels[0, 2:6, 2:6] = 1  # frame 1 is empty
    store.write_labels("tracked", labels)
    store.write_tracks("tracked", pd.DataFrame({
        "track_id": [1], "tree_id": [0], "begin_t": [0], "end_t": [0],
        "parent_track_id": [-1],
    }))

    df, failure, _msg = measure_one(store, round_specs=[], seg_name="tracked")

    assert failure is None
    assert sorted(df["timepoint"].unique()) == [0]  # only frame 0 contributes
    assert len(df) == 1


def test_measure_one_single_timepoint_has_no_timepoint_column(tmp_path):
    store = DatasetStore(tmp_path / "still.h5")
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array("intensity", np.ones((20, 20), dtype=np.float32),
                      attrs={"dims": ["H", "W"]})
    lab = np.zeros((20, 20), dtype=np.int32)
    lab[2:6, 2:6] = 1
    store.write_labels("cellpose_qc", lab)

    df, failure, _msg = measure_one(store, round_specs=[], seg_name="cellpose_qc")

    assert failure is None
    assert "timepoint" not in df.columns
    assert "track_id" not in df.columns
    assert len(df) == 1


def test_measure_particles_one_timelapse_tags_timepoint(tmp_path):
    store = _tracked_store(tmp_path / "tl.h5")
    # A round mask covering the cells (T,H,W), so particles are detected.
    mask = (store.read_labels("tracked") > 0).astype(np.uint8)
    store.write_mask("GFP_split", mask)
    round_spec = ThresholdingRound(
        name="GFP_split", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2, gaussian_sigma=0.0,
    )

    df, failure, _msg = measure_particles_one(
        store, [round_spec], ParticleSettings(min_area=1.0, min_area_unit="px"),
        seg_name="tracked",
    )

    assert failure is None
    assert "timepoint" in df.columns
    assert set(df["timepoint"].unique()) == {0, 1}
