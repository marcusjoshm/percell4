"""Tests for the percell4-batch-measure CLI (measure + particles + CSV export)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.interfaces.cli import batch_measure as cli
from percell4.store import DatasetStore
from percell4.workflows.csv_columns import (
    DEFAULT_CSV_METRICS,
    DEFAULT_CSV_PARTICLE_PER_CELL,
    DEFAULT_CSV_PARTICLE_PER_CHANNEL,
    build_selected_csv_columns,
)


def _make_dataset(path: Path, *, mask_names=("pbody",), pixel_size_um=0.1,
                  n_cells: int = 12, size: int = 100) -> None:
    """h5 with /labels/cellpose + a /masks/<name> carrying one 3x3 (9px) blob
    inside each cell."""
    store = DatasetStore(path)
    md = {"channel_names": ["GFP", "RFP"]}
    if pixel_size_um is not None:
        md["pixel_size_um"] = pixel_size_um
    store.create(metadata=md)

    intensity = np.zeros((2, size, size), dtype=np.float32)
    labels = np.zeros((size, size), dtype=np.int32)
    mask = np.zeros((size, size), dtype=np.uint8)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        intensity[0, row : row + 6, col : col + 6] = 50 + 30 * i
        intensity[1, row : row + 6, col : col + 6] = 100
        labels[row : row + 6, col : col + 6] = i + 1
        mask[row + 1 : row + 4, col + 1 : col + 4] = 1  # 3x3 = 9px blob
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_labels("cellpose", labels)
    for m in mask_names:
        store.write_mask(m, mask)


def test_batch_measure_happy_path(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    out_parent = tmp_path / "out"
    rc = cli.main([str(p), "--segmentation", "cellpose", "--mask", "pbody",
                   "--min-particle-area", "9", "--output", str(out_parent)])
    assert rc == 0
    run_folders = list(out_parent.glob("run_*"))
    assert len(run_folders) == 1
    rf = run_folders[0]
    assert (rf / "combined.csv").is_file()
    assert (rf / "particles.csv").is_file()
    assert (rf / "per_dataset" / "DS1.csv").is_file()
    assert (rf / "summary_groups.csv").is_file()

    parts = pd.read_csv(rf / "particles.csv")
    assert len(parts) == 12               # one 9px blob per cell survives
    assert parts["area"].min() == 9       # the 9px filter held
    assert (parts["area"] < 9).sum() == 0

    measurements = pd.read_parquet(rf / "measurements.parquet")
    assert "pbody_particle_count" in measurements.columns
    assert measurements["pbody_particle_count"].sum() == 12


def test_batch_measure_filter_drops_below_threshold(tmp_path):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    out_parent = tmp_path / "out"
    rc = cli.main([str(p), "--mask", "pbody", "--min-particle-area", "10",
                   "--output", str(out_parent)])
    # Cells are still measured (rc 0), but every 9px blob is below the
    # 10px threshold, so no particle survives: counts are all zero and no
    # per-particle rows are written.
    assert rc == 0
    rf = next(out_parent.glob("run_*"))
    measurements = pd.read_parquet(rf / "measurements.parquet")
    assert measurements["pbody_particle_count"].sum() == 0
    assert not (rf / "particles.csv").exists()


def test_batch_measure_default_columns_match_shared_builder(tmp_path):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    out_parent = tmp_path / "out"
    cli.main([str(p), "--mask", "pbody", "--output", str(out_parent)])
    rf = next(out_parent.glob("run_*"))
    from percell4.workflows.artifacts import read_run_config

    cfg, _meta = read_run_config(rf)
    expected = build_selected_csv_columns(
        ["GFP", "RFP"],
        ["pbody"],
        metrics=DEFAULT_CSV_METRICS,
        particle_per_cell=DEFAULT_CSV_PARTICLE_PER_CELL,
        particle_per_channel=DEFAULT_CSV_PARTICLE_PER_CHANNEL,
    )
    assert cfg.selected_csv_columns == expected


def test_batch_measure_default_mask_warns_and_measures_all(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p, mask_names=("pbody", "grouped"))
    out_parent = tmp_path / "out"
    rc = cli.main([str(p), "--output", str(out_parent)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "measuring all masks present" in err
    assert "pbody" in err and "grouped" in err


def test_batch_measure_um2_without_pixel_size_fails(tmp_path):
    p = tmp_path / "DS1.h5"
    _make_dataset(p, pixel_size_um=None)
    out_parent = tmp_path / "out"
    rc = cli.main([str(p), "--mask", "pbody", "--particle-unit", "um2",
                   "--min-particle-area", "0.5", "--output", str(out_parent)])
    # µm² threshold needs a pixel size → that dataset's measure fails → exit 1.
    assert rc == 1


def test_batch_measure_no_labels_skips(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    store = DatasetStore(p)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})
    store.write_array("intensity", np.zeros((2, 32, 32), dtype=np.float32),
                      attrs={"dims": ["C", "H", "W"]})
    store.write_mask("pbody", np.zeros((32, 32), dtype=np.uint8))
    rc = cli.main([str(p), "--mask", "pbody", "--output", str(tmp_path / "out")])
    assert rc == 1
    assert "no usable segmentation" in capsys.readouterr().err


def test_batch_measure_import_is_qt_free():
    qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    from percell4.interfaces.cli import batch_measure  # noqa: F401
    qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    assert not (qt_after - qt_before)
