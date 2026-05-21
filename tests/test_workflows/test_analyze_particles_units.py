"""U4: workflow phase resolves min_area units per dataset.

px mode is a straight cast and is bit-identical to pre-U3 behavior
(R6 zero-regression). µm² mode converts to a per-dataset integer pixel
threshold using each dataset's ``/metadata.pixel_size_um``; datasets
without a known pixel size fail their particle phase explicitly rather
than silently defaulting.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.models import (
    ParticleSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import (
    _resolve_min_area_px,
    measure_one,
    measure_particles_one,
)


# ── Pure helper unit tests ────────────────────────────────────────────


def test_resolve_px_returns_rounded_int():
    s = ParticleSettings(min_area=10.7, min_area_unit="px")
    assert _resolve_min_area_px(s, pixel_size_um=0.5) == 11


def test_resolve_px_ignores_pixel_size():
    """In px mode, pixel_size_um is irrelevant — the threshold is a
    uniform pixel count regardless of dataset calibration."""
    s = ParticleSettings(min_area=10.0, min_area_unit="px")
    assert _resolve_min_area_px(s, pixel_size_um=None) == 10
    assert _resolve_min_area_px(s, pixel_size_um=0.001) == 10
    assert _resolve_min_area_px(s, pixel_size_um=100.0) == 10


def test_resolve_um2_at_known_pixel_size():
    """0.5 µm² at 0.12034 µm/px → round(0.5 / 0.014482) = 35 px."""
    s = ParticleSettings(min_area=0.5, min_area_unit="um2")
    assert _resolve_min_area_px(s, pixel_size_um=0.12034) == 35


def test_resolve_um2_zero_value_returns_zero():
    s = ParticleSettings(min_area=0.0, min_area_unit="um2")
    assert _resolve_min_area_px(s, pixel_size_um=0.12034) == 0


def test_resolve_um2_per_dataset_variation():
    """Two datasets with different pixel sizes resolve to different px
    thresholds for the same µm² intent — the Bug 3 scope-collapse
    pattern that the workflow phase must not trip."""
    s = ParticleSettings(min_area=1.0, min_area_unit="um2")
    # 0.1 µm/px → 1 / 0.01 = 100 px
    assert _resolve_min_area_px(s, pixel_size_um=0.1) == 100
    # 0.5 µm/px → 1 / 0.25 = 4 px
    assert _resolve_min_area_px(s, pixel_size_um=0.5) == 4


def test_resolve_um2_without_pixel_size_raises():
    s = ParticleSettings(min_area=0.5, min_area_unit="um2")
    with pytest.raises(ValueError, match="pixel size"):
        _resolve_min_area_px(s, pixel_size_um=None, dataset_name="ds_a")


def test_resolve_um2_with_zero_pixel_size_raises():
    s = ParticleSettings(min_area=0.5, min_area_unit="um2")
    with pytest.raises(ValueError, match="pixel size"):
        _resolve_min_area_px(s, pixel_size_um=0.0)


def test_resolve_um2_error_names_the_dataset():
    s = ParticleSettings(min_area=0.5, min_area_unit="um2")
    with pytest.raises(ValueError, match="'foo.h5'"):
        _resolve_min_area_px(s, pixel_size_um=None, dataset_name="foo.h5")


# ── Phase-level integration: measure_one and measure_particles_one ────


def _build_store_with_labels(
    path: Path, pixel_size_um: float | None = 0.12034,
) -> DatasetStore:
    """Stand up an h5 store with a 2-cell label image, one round mask,
    and optional pixel_size_um. Just enough for measure_one /
    measure_particles_one to execute the particle path.
    """
    store = DatasetStore(path)
    meta: dict = {"channel_names": ["ch0"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)

    # Two well-separated cells, each 8×8.
    labels = np.zeros((32, 32), dtype=np.int32)
    labels[2:10, 2:10] = 1
    labels[18:26, 18:26] = 2
    store.write_labels("cellpose_qc", labels)

    # Mask: a 6×6 connected component inside each cell (area = 36 px).
    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[3:9, 3:9] = 1
    mask[19:25, 19:25] = 1
    store.write_mask("puncta", mask)

    intensity = np.full((32, 32), 1000, dtype=np.uint16)
    store.write_array("intensity", intensity, attrs={"dims": ["H", "W"]})
    return store


def _round_spec(name: str) -> ThresholdingRound:
    return ThresholdingRound(
        name=name,
        channel="ch0",
        metric="median_intensity",
        algorithm=ThresholdAlgorithm.GMM,
    )


def test_measure_particles_one_um2_resolves_per_dataset(tmp_path):
    """A µm² threshold smaller than the 36-px puncta keeps the rows."""
    store = _build_store_with_labels(
        tmp_path / "ds.h5", pixel_size_um=0.12034,
    )
    # 0.1 µm² at 0.12034 µm/px → 7 px. 36 > 7, so puncta survive.
    settings = ParticleSettings(min_area=0.1, min_area_unit="um2")

    df, failure, msg = measure_particles_one(
        store, [_round_spec("puncta")], settings,
    )
    assert failure is None, msg
    assert not df.empty
    # 2 cells × 1 round × 1 particle each = 2 rows.
    assert len(df) == 2


def test_measure_particles_one_um2_drops_below_threshold(tmp_path):
    """A µm² threshold larger than 36 px-equivalent drops every puncta."""
    store = _build_store_with_labels(
        tmp_path / "ds.h5", pixel_size_um=0.12034,
    )
    # 1.0 µm² at 0.12034 µm/px → 69 px > 36 px, so no puncta survive.
    settings = ParticleSettings(min_area=1.0, min_area_unit="um2")

    df, failure, msg = measure_particles_one(
        store, [_round_spec("puncta")], settings,
    )
    assert failure is None, msg
    assert df.empty


def test_measure_particles_one_um2_without_pixel_size_fails(tmp_path):
    """µm² mode against a dataset without pixel_size_um raises a
    MEASUREMENT_ERROR rather than silently defaulting."""
    store = _build_store_with_labels(tmp_path / "ds.h5", pixel_size_um=None)
    settings = ParticleSettings(min_area=0.5, min_area_unit="um2")

    df, failure, msg = measure_particles_one(
        store, [_round_spec("puncta")], settings,
    )
    assert df.empty
    assert failure == DatasetFailure.MEASUREMENT_ERROR
    assert "pixel size" in msg


def test_measure_particles_one_px_mode_zero_regression(tmp_path):
    """px mode behaves identically with or without pixel_size_um."""
    settings = ParticleSettings(min_area=10.0, min_area_unit="px")

    # With pixel_size_um
    store_a = _build_store_with_labels(
        tmp_path / "a.h5", pixel_size_um=0.12034,
    )
    df_a, _, _ = measure_particles_one(
        store_a, [_round_spec("puncta")], settings,
    )

    # Without pixel_size_um
    store_b = _build_store_with_labels(tmp_path / "b.h5", pixel_size_um=None)
    df_b, _, _ = measure_particles_one(
        store_b, [_round_spec("puncta")], settings,
    )

    assert len(df_a) == len(df_b)
