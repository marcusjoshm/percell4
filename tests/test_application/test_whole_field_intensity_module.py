"""Framework-level tests for the WholeFieldIntensity module (U7)."""
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
    Path(__file__).resolve().parents[1] / "fixtures" / "whole_field_intensity"
)
PFX = "fieldA"

# Framework param sets mirroring the shipped presets (the module also exposes
# these as presets; we pass explicit params here to keep the test independent
# of preset resolution).
_V2 = dict(min_size=2, mng_bg_mode="manual", mng_bg_value=0,
           halo_bg_mode="manual", halo_bg_value=0, mNG_filter="NaN",
           percent=False, SiR_filter=True)
_V4 = dict(min_size=2, mng_bg_mode="manual", mng_bg_value=0,
           halo_bg_mode="manual", halo_bg_value=0, mNG_filter="NaN",
           FLIM_filter="zero", percent=True, intermediate_assemblies=True)


@pytest.fixture(autouse=True)
def _reregister() -> Iterator[None]:
    if "whole_field_intensity" not in registry_mod._REGISTRY:
        import percell4.application.analysis.modules.whole_field_intensity as mod
        importlib.reload(mod)
    yield


def _img(name: str) -> np.ndarray:
    return tifffile.imread(FIXTURE_ROOT / f"{PFX}_{name}.tif")


def _build_h5(path: Path) -> None:
    """Build an .h5 mirroring the whole_field fixture field."""
    halo = _img("Halo").astype(np.float32)
    mng = _img("mNG").astype(np.float32)
    intensity = np.stack([halo, mng], axis=0)
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": ["Halo", "mNG"]})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    for role, fname in [
        ("pbody", "P-body_mask"), ("dilute", "dilute_mask"),
        ("dcp2", "Dcp2_mask"), ("dcp2_2", "Dcp2_mask_2"),
        ("interaction", "interaction_mask"),
        ("interaction_2", "interaction_mask_2"), ("sir", "SiR_mask"),
    ]:
        store.write_array(f"masks/{role}", (_img(fname) > 0).astype(np.uint8))
    store.write_array("labels/cells", _img("cp_mask").astype(np.int32))


_LAYER_MAP = {
    "pbody_mask": "pbody", "dilute_mask": "dilute",
    "halo": "Halo", "mng": "mNG",
    "dcp2_mask": "dcp2", "interaction_mask": "interaction",
    "sir_mask": "sir", "dcp2_mask_2": "dcp2_2",
    "interaction_mask_2": "interaction_2", "cp_mask": "cells",
}


def _parity(out_df: pd.DataFrame, expected_csv: Path, sort_key: str) -> None:
    a = out_df.copy()
    e = pd.read_csv(expected_csv)
    if "group" in e.columns:
        e = e.drop(columns=["group"])
    if "group" in a.columns:
        a = a.drop(columns=["group"])
    a = a.reindex(columns=sorted(a.columns)).sort_values(sort_key)
    e = e.reindex(columns=sorted(e.columns)).sort_values(sort_key)
    a = a.reset_index(drop=True)
    e = e.reset_index(drop=True)
    assert list(a.columns) == list(e.columns)
    for col in a.columns:
        if pd.api.types.is_float_dtype(e[col]):
            np.testing.assert_allclose(a[col].values, e[col].values,
                                       rtol=1e-10, equal_nan=True,
                                       err_msg=col)
        else:
            pd.testing.assert_series_equal(a[col].astype(e[col].dtype), e[col],
                                           check_dtype=False, check_exact=True,
                                           obj=col)


# ── Registration ──────────────────────────────────────────────────


def test_registered_and_listed():
    assert registry_get("whole_field_intensity").name == "whole_field_intensity"
    assert "whole_field_intensity" in [i.name for i in list_analyses()]


# ── Framework <-> CLI parity ───────────────────────────────────────


def test_v2_parity_with_cli(tmp_path: Path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP, params=_V2)
    _parity(out["whole_field_table"],
            FIXTURE_ROOT / "expected" / "v2.csv", sort_key="pbody_area_px")


def test_v4_three_region_parity_with_cli(tmp_path: Path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP, params=_V4)
    df = out["whole_field_table"]
    assert "mNG_intermediate_mean" in df.columns
    _parity(df, FIXTURE_ROOT / "expected" / "v4.csv", sort_key="pbody_area_px")


def test_v4_single_cell_parity_with_cli(tmp_path: Path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                       params={**_V4, "single_cell": True})
    _parity(out["whole_field_table"],
            FIXTURE_ROOT / "expected" / "v4_sc.csv", sort_key="cell_id")


def test_preset_v3_runs(tmp_path: Path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                       preset="decapping-sensor-v3")
    _parity(out["whole_field_table"],
            FIXTURE_ROOT / "expected" / "v3.csv", sort_key="pbody_area_px")


# ── Cross-cutting constraint guards (error path) ───────────────────


def test_sir_filter_with_sir_subtract_raises(tmp_path: Path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    with pytest.raises(ValueError, match="SiR"):
        run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                     params={"SiR_filter": True, "SiR_subtract": "zero"})


def test_intermediate_requires_filters(tmp_path: Path):
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    # intermediate_assemblies True but mNG_filter left 'none' -> ValueError.
    with pytest.raises(ValueError, match="mNG_filter"):
        run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                     params={"intermediate_assemblies": True,
                             "FLIM_filter": "zero"})
