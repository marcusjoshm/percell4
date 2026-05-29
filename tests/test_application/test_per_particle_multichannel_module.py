"""Framework-level tests for the PerParticleMultichannel module (U4)."""
from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tifffile

from percell4.application.analysis import get as registry_get
from percell4.application.analysis import list_analyses
from percell4.application.analysis import registry as registry_mod
from percell4.application.use_cases.run_analysis import run_analysis
from percell4.store import DatasetStore

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "per_particle_multichannel"
)


@pytest.fixture(autouse=True)
def _reregister() -> Iterator[None]:
    if "per_particle_multichannel" not in registry_mod._REGISTRY:
        import percell4.application.analysis.modules.per_particle_multichannel as mod
        importlib.reload(mod)
    yield


def _h5_from_group(
    path: Path, group_dir: Path, *, channels: list[str], with_cp: bool
) -> None:
    """Build an .h5 whose layers mirror a fixture group directory."""
    prefix = group_dir.name.split("_")[-1] + "1"  # group_a -> a1, group_b -> b1
    imgs = [
        tifffile.imread(group_dir / f"{prefix}_{ch}.tif").astype(np.float32)
        for ch in channels
    ]
    intensity = np.stack(imgs, axis=0)
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": channels})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    mask = tifffile.imread(group_dir / f"{prefix}_mask.tif")
    store.write_array("masks/particles", (mask > 0).astype(np.uint8))
    if with_cp:
        cp = tifffile.imread(group_dir / f"{prefix}_cellpose.tif")
        store.write_array("labels/cells", cp.astype(np.int32))


# ── Registration ──────────────────────────────────────────────────


def test_registered_and_listed():
    cls = registry_get("per_particle_multichannel")
    assert cls.name == "per_particle_multichannel"
    names = [info.name for info in list_analyses()]
    assert "per_particle_multichannel" in names


def test_no_channel_group_means_two_channels_load(tmp_path: Path):
    """3 channels mapped (not 8) must run — guards the group-semantics trap."""
    h5 = tmp_path / "a.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_a",
                   channels=["mNG", "CA-SiR"], with_cp=False)
    out = run_analysis(
        "per_particle_multichannel", h5,
        {"mask": "particles", "channel_1": "mNG", "channel_2": "CA-SiR"},
        params={},
    )
    assert "particle_table" in out
    assert "cell_table" not in out


# ── Layer-name column naming + per-dataset parity ─────────────────


def test_columns_named_by_layer_and_parity_with_cli(tmp_path: Path):
    h5 = tmp_path / "a.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_a",
                   channels=["mNG", "CA-SiR"], with_cp=False)
    out = run_analysis(
        "per_particle_multichannel", h5,
        {"mask": "particles", "channel_1": "mNG", "channel_2": "CA-SiR"},
        params={},
    )
    df = out["particle_table"]
    # Columns are named by the chosen layer name, not the role key.
    assert "condensed_mNG_mean" in df.columns
    assert "condensed_CA-SiR_mean" in df.columns
    assert not any("channel_1" in c for c in df.columns)

    # Per-dataset numeric parity vs the committed CLI baseline.
    expected = pd.read_csv(FIXTURE_ROOT / "group_a_expected" / "combined.csv")
    expected = expected.drop(columns=["group"])
    a = df.reindex(columns=sorted(df.columns))
    e = expected.reindex(columns=sorted(expected.columns))
    a = a.sort_values("particle_id").reset_index(drop=True)
    e = e.sort_values("particle_id").reset_index(drop=True)
    assert list(a.columns) == list(e.columns)
    for col in a.columns:
        if pd.api.types.is_float_dtype(e[col]):
            np.testing.assert_allclose(
                a[col].values, e[col].values, rtol=1e-10, equal_nan=True,
                err_msg=f"col {col}",
            )
        else:
            pd.testing.assert_series_equal(
                a[col].astype(e[col].dtype), e[col], check_dtype=False,
                check_exact=True, obj=col,
            )


# ── single_cell switches the produced table ───────────────────────


def test_single_cell_produces_cell_table(tmp_path: Path):
    h5 = tmp_path / "b.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_b",
                   channels=["mNG", "mTQ2"], with_cp=True)
    out = run_analysis(
        "per_particle_multichannel", h5,
        {"mask": "particles", "channel_1": "mNG", "channel_2": "mTQ2",
         "cp_mask": "cells"},
        params={"single_cell": True},
    )
    assert "cell_table" in out
    assert "particle_table" not in out
    assert "cell_mNG_mean" in out["cell_table"].columns


def test_cp_mask_without_single_cell_adds_cell_id(tmp_path: Path):
    h5 = tmp_path / "b.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_b",
                   channels=["mNG", "mTQ2"], with_cp=True)
    out = run_analysis(
        "per_particle_multichannel", h5,
        {"mask": "particles", "channel_1": "mNG", "channel_2": "mTQ2",
         "cp_mask": "cells"},
        params={},  # single_cell False
    )
    assert "cell_id" in out["particle_table"].columns


def test_single_cell_without_cp_mask_rejected(tmp_path: Path):
    h5 = tmp_path / "a.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_a",
                   channels=["mNG", "CA-SiR"], with_cp=False)
    with pytest.raises(ValueError):
        run_analysis(
            "per_particle_multichannel", h5,
            {"mask": "particles", "channel_1": "mNG"},
            params={"single_cell": True},
        )
