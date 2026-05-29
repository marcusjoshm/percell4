"""Framework-level tests for the PerParticleMultichannel module (U4)."""
from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tifffile

from percell4.application.analysis import batch_run_analysis
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
    # v1.1.0 reshape: particle_table no longer carries *_integ columns.
    assert not any(c.endswith("_integ") for c in df.columns)

    # Numeric parity vs the committed CLI baseline, on the shared columns.
    # The CLI fixture still carries the richer schema (group id + *_integ);
    # the framework particle_table drops both, so compare on the overlap.
    # The fixture is intentionally NOT regenerated — it co-serves the CLI
    # regression test, which must keep matching the unchanged CLI output.
    expected = pd.read_csv(FIXTURE_ROOT / "group_a_expected" / "combined.csv")
    expected = expected.drop(columns=["group"])
    expected = expected[
        [c for c in expected.columns if not c.endswith("_integ")]
    ]
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


# ── particle_table exact-column-order contract (U3) ───────────────


def _build_synthetic_h5(
    path: Path, *, channels: list[str], with_cp: bool, blob_px: int = 8
) -> None:
    """Minimal h5 with full control over channel names + particle size."""
    h = w = 32
    intensity = np.stack(
        [np.full((h, w), 100.0 * (i + 1), dtype=np.float32)
         for i in range(len(channels))],
        axis=0,
    )
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[4:4 + blob_px, 4:4 + blob_px] = 1
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": channels})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_array("masks/particles", mask)
    if with_cp:
        cp = np.ones((h, w), dtype=np.int32)
        store.write_array("labels/cells", cp)


def test_combined_particle_table_exact_column_order(tmp_path: Path):
    """R1/R5/R2: combined CSV header is exactly the original-script order,
    led by `group`, one row per particle, with cell_<ch>_mean only for the
    selected channel and no *_integ columns."""
    h5 = tmp_path / "exp1.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_b",
                   channels=["mNG", "mTQ2"], with_cp=True)
    report = batch_run_analysis(
        "per_particle_multichannel",
        [h5],
        lambda _p: {"mask": "particles", "channel_1": "mNG",
                    "channel_2": "mTQ2", "cp_mask": "cells"},
        output_parent=tmp_path / "out",
        params={"channel_1_cell_mean": True},  # cell-mean for mNG only
    )
    assert report.succeeded_count == 1
    combined = pd.read_csv(report.run_folder / "combined_particle_table.csv")
    assert list(combined.columns) == [
        "group", "particle_id", "cell_id", "cell_mNG_mean",
        "particle_area_px", "donut_area_px",
        "condensed_mNG_mean", "dilute_mNG_mean", "mNG_condensed_over_dilute",
        "condensed_mTQ2_mean", "dilute_mTQ2_mean", "mTQ2_condensed_over_dilute",
    ]
    assert set(combined["group"]) == {"exp1"}
    assert "dataset" not in combined.columns
    assert not any(c.endswith("_integ") for c in combined.columns)
    assert len(combined) >= 1  # one row per particle, not per cell


def test_no_cell_mean_selected_omits_cell_mean_columns(tmp_path: Path):
    h5 = tmp_path / "exp1.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_b",
                   channels=["mNG", "mTQ2"], with_cp=True)
    report = batch_run_analysis(
        "per_particle_multichannel",
        [h5],
        lambda _p: {"mask": "particles", "channel_1": "mNG",
                    "channel_2": "mTQ2", "cp_mask": "cells"},
        output_parent=tmp_path / "out",
        params={},  # nothing selected
    )
    combined = pd.read_csv(report.run_folder / "combined_particle_table.csv")
    assert list(combined.columns) == [
        "group", "particle_id", "cell_id",
        "particle_area_px", "donut_area_px",
        "condensed_mNG_mean", "dilute_mNG_mean", "mNG_condensed_over_dilute",
        "condensed_mTQ2_mean", "dilute_mTQ2_mean", "mTQ2_condensed_over_dilute",
    ]


def test_no_cp_mask_omits_cell_id_and_cell_mean(tmp_path: Path):
    h5 = tmp_path / "a.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_a",
                   channels=["mNG", "CA-SiR"], with_cp=False)
    report = batch_run_analysis(
        "per_particle_multichannel",
        [h5],
        lambda _p: {"mask": "particles", "channel_1": "mNG",
                    "channel_2": "CA-SiR"},
        output_parent=tmp_path / "out",
        params={"channel_1_cell_mean": True},  # checked, but no cp_mask
    )
    # Does NOT raise (cell-mean bools carry no requires); cell columns absent.
    assert report.succeeded_count == 1
    combined = pd.read_csv(report.run_folder / "combined_particle_table.csv")
    assert "cell_id" not in combined.columns
    assert not any(c.startswith("cell_") for c in combined.columns)
    assert list(combined.columns) == [
        "group", "particle_id", "particle_area_px", "donut_area_px",
        "condensed_CA-SiR_mean", "dilute_CA-SiR_mean",
        "CA-SiR_condensed_over_dilute",
        "condensed_mNG_mean", "dilute_mNG_mean", "mNG_condensed_over_dilute",
    ]


def test_empty_particle_table_keeps_exact_columns(tmp_path: Path):
    """A dataset whose particles are all <= min_size yields an empty
    particle_table with the exact header (reindex path, no KeyError)."""
    h5 = tmp_path / "empty.h5"
    # 4-px blob is not > the default min_size of 4 → filtered out.
    _build_synthetic_h5(h5, channels=["GFP"], with_cp=False, blob_px=2)
    report = batch_run_analysis(
        "per_particle_multichannel",
        [h5],
        lambda _p: {"mask": "particles", "channel_1": "GFP"},
        output_parent=tmp_path / "out",
        params={},
    )
    assert report.succeeded_count == 1
    combined = pd.read_csv(report.run_folder / "combined_particle_table.csv")
    assert len(combined) == 0
    assert list(combined.columns) == [
        "group", "particle_id", "particle_area_px", "donut_area_px",
        "condensed_GFP_mean", "dilute_GFP_mean", "GFP_condensed_over_dilute",
    ]


def test_exact_order_arbitrary_channel_names(tmp_path: Path):
    """R6: no hardcoding of example names; a channel literally named
    'cell' proves the order is built from known names, not by parsing
    column strings."""
    h5 = tmp_path / "x.h5"
    _build_synthetic_h5(h5, channels=["GFP", "cell"], with_cp=True)
    report = batch_run_analysis(
        "per_particle_multichannel",
        [h5],
        lambda _p: {"mask": "particles", "channel_1": "GFP",
                    "channel_2": "cell", "cp_mask": "cells"},
        output_parent=tmp_path / "out",
        params={"channel_2_cell_mean": True},  # cell-mean for the 'cell' chan
    )
    combined = pd.read_csv(report.run_folder / "combined_particle_table.csv")
    # sorted(["GFP", "cell"]) == ["GFP", "cell"] (ASCII: 'G'=71 < 'c'=99).
    assert list(combined.columns) == [
        "group", "particle_id", "cell_id", "cell_cell_mean",
        "particle_area_px", "donut_area_px",
        "condensed_GFP_mean", "dilute_GFP_mean", "GFP_condensed_over_dilute",
        "condensed_cell_mean", "dilute_cell_mean", "cell_condensed_over_dilute",
    ]


def test_params_default_all_cell_means_false(tmp_path: Path):
    """resolve_params backfills the 8 cell-mean bools to False on params={}."""
    h5 = tmp_path / "exp1.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_b",
                   channels=["mNG", "mTQ2"], with_cp=True)
    report = batch_run_analysis(
        "per_particle_multichannel",
        [h5],
        lambda _p: {"mask": "particles", "channel_1": "mNG",
                    "channel_2": "mTQ2", "cp_mask": "cells"},
        output_parent=tmp_path / "out",
        params={},
    )
    config = json.loads((report.run_folder / "run_config.json").read_text())
    for i in range(1, 9):
        assert config["params"][f"channel_{i}_cell_mean"] is False


def test_per_particle_cell_mean_matches_single_cell_table(tmp_path: Path):
    """R3 at framework level: particle_table cell_<ch>_mean equals the
    single-cell cell_table value for the same cell."""
    h5 = tmp_path / "b.h5"
    _h5_from_group(h5, FIXTURE_ROOT / "group_b",
                   channels=["mNG", "mTQ2"], with_cp=True)
    layer_map = {"mask": "particles", "channel_1": "mNG",
                 "channel_2": "mTQ2", "cp_mask": "cells"}
    pp = run_analysis(
        "per_particle_multichannel", h5, layer_map,
        params={"channel_1_cell_mean": True},
    )["particle_table"]
    sc = run_analysis(
        "per_particle_multichannel", h5, layer_map,
        params={"single_cell": True},
    )["cell_table"]
    cell_means = dict(zip(sc["cell_id"], sc["cell_mNG_mean"], strict=False))
    for _, row in pp.iterrows():
        if row["cell_id"] != 0:
            assert row["cell_mNG_mean"] == pytest.approx(
                cell_means[row["cell_id"]]
            )
