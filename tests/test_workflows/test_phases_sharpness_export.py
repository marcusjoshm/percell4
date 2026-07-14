"""Guard tests: per-particle sharpness metrics propagate to particles.csv.

These lock in the plan's "particles.csv is written wholesale" assumption
(docs/plans/2026-07-14-001-feat-particle-sharpness-metrics-plan.md, U2): the
sharpness columns added in analyze_particles_detail must survive the full
measure -> staging -> export chain and the batch-measure CLI, while the
per-cell combined.csv must NOT gain them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from percell4.domain.measure.particle import (
    _PARTICLE_SHARPNESS_METRICS,
    _SHARPNESS_COMPUTABLE_COL,
)
from percell4.interfaces.cli import batch_measure as cli
from percell4.store import DatasetStore

_SHARPNESS = tuple(_PARTICLE_SHARPNESS_METRICS)


def _make_dataset(path: Path, *, size: int = 100, n_cells: int = 12) -> None:
    """h5 with /labels/cellpose + a /masks/pbody carrying one 3x3 blob per cell.

    Two channels (GFP, RFP) so the export exercises per-channel prefixing.
    """
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"], "pixel_size_um": 0.1})

    intensity = np.zeros((2, size, size), dtype=np.float32)
    labels = np.zeros((size, size), dtype=np.int32)
    mask = np.zeros((size, size), dtype=np.uint8)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        # A bright compact core with a darker surround so the metrics are
        # non-trivial (real edge, real skirt contrast).
        intensity[0, row : row + 6, col : col + 6] = 20.0
        intensity[0, row + 1 : row + 4, col + 1 : col + 4] = 200.0
        intensity[1, row : row + 6, col : col + 6] = 100.0
        labels[row : row + 6, col : col + 6] = i + 1
        mask[row + 1 : row + 4, col + 1 : col + 4] = 1  # 3x3 = 9px blob
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_labels("cellpose", labels)
    store.write_mask("pbody", mask)


def _run(tmp_path: Path) -> Path:
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    out_parent = tmp_path / "out"
    rc = cli.main([
        str(p), "--segmentation", "cellpose", "--mask", "pbody",
        "--min-particle-area", "9", "--output", str(out_parent),
    ])
    assert rc == 0
    return next(out_parent.glob("run_*"))


def test_sharpness_columns_reach_particles_csv(tmp_path):
    """The three per-channel sharpness columns + validity flag land in
    particles.csv end-to-end, with populated (non-all-NaN) values."""
    rf = _run(tmp_path)
    parts = pd.read_csv(rf / "particles.csv")
    for ch in ("GFP", "RFP"):
        for metric in _SHARPNESS:
            col = f"{ch}_{metric}"
            assert col in parts.columns, f"missing {col}"
    assert _SHARPNESS_COMPUTABLE_COL in parts.columns

    # Values populated, not an all-NaN placeholder column.
    assert parts["GFP_edge_skirt_ratio"].notna().any()
    assert parts["GFP_boundary_gradient"].notna().any()
    # The 9px blobs have room for a skirt inside their cell -> computable.
    assert bool(parts[_SHARPNESS_COMPUTABLE_COL].all())


def test_sharpness_columns_absent_from_combined_csv(tmp_path):
    """The per-cell combined.csv must NOT gain any sharpness columns — the
    metrics deliberately stay out of the per-cell summary + its column
    machinery."""
    rf = _run(tmp_path)
    combined = pd.read_csv(rf / "combined.csv")
    for metric in _SHARPNESS:
        offenders = [c for c in combined.columns if metric in c]
        assert offenders == [], f"combined.csv leaked {offenders}"
    assert _SHARPNESS_COMPUTABLE_COL not in combined.columns
