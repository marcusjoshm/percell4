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
    "condensate_mask": "pbody", "dilute_mask": "dilute",
    "halo": "Halo", "mng": "mNG",
    "mng_mask": "dcp2", "interaction_mask": "interaction",
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


# ── Multi-timepoint (T,…) framework auto-loop (U1) ─────────────────


def _build_timelapse_h5(
    path: Path,
    *,
    n_t: int = 3,
    drop_cell_2_last_frame: bool = True,
) -> None:
    """Build a ``(T, C, H, W)`` whole-field h5 from the fixture field.

    The Halo / mNG fixture planes are stacked across ``n_t`` acquisition
    frames (a small per-frame ``+t`` offset so the frames carry distinct
    data — proving the framework genuinely re-runs the 2D core per frame
    rather than reusing one result). ``/intensity`` is written with a
    leading-``T`` ``dims`` so the store reports ``n_timepoints == n_t`` and
    ``run_analysis`` takes its per-frame loop.

    The seven masks are written as 2D ``(H, W)`` planes — *time-invariant*
    gates the store broadcasts to every frame on a per-timepoint read.
    ``labels/cells`` is a ``(T, H, W)`` stack so the segmentation can vary
    per frame: when ``drop_cell_2_last_frame`` is set, cell ``2`` is zeroed
    on the final frame, exercising the "a cell present in one frame but not
    another is simply absent that frame" contract.
    """
    halo = _img("Halo").astype(np.float32)
    mng = _img("mNG").astype(np.float32)
    h, w = halo.shape
    intensity = np.zeros((n_t, 2, h, w), dtype=np.float32)
    for t in range(n_t):
        intensity[t, 0] = halo + t  # Halo
        intensity[t, 1] = mng + t   # mNG
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": ["Halo", "mNG"]})
    store.write_array(
        "intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]}
    )
    for role, fname in [
        ("pbody", "P-body_mask"), ("dilute", "dilute_mask"),
        ("dcp2", "Dcp2_mask"), ("dcp2_2", "Dcp2_mask_2"),
        ("interaction", "interaction_mask"),
        ("interaction_2", "interaction_mask_2"), ("sir", "SiR_mask"),
    ]:
        store.write_mask(role, (_img(fname) > 0).astype(np.uint8))
    cp_plane = _img("cp_mask").astype(np.int32)  # ids {0, 1, 2}
    cp = np.stack([cp_plane.copy() for _ in range(n_t)], axis=0)
    if drop_cell_2_last_frame:
        cp[n_t - 1][cp[n_t - 1] == 2] = 0
    store.write_labels("cells", cp)


def test_timelapse_two_region_spans_all_timepoints(tmp_path: Path):
    """R1 (two-region): a (3,C,H,W) v2-style run yields one whole-field row
    per frame, tagged with a ``timepoint`` column spanning {0,1,2}."""
    h5 = tmp_path / "tl.h5"
    _build_timelapse_h5(h5, n_t=3)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP, params=_V2)
    df = out["whole_field_table"]
    assert "timepoint" in df.columns
    assert set(df["timepoint"]) == {0, 1, 2}
    assert len(df) == 3  # one whole-field row per frame
    assert "pbody_area_px" in df.columns


def test_timelapse_three_region_spans_all_timepoints(tmp_path: Path):
    """R1 (three-region): a (3,C,H,W) v4-style run yields one three-region
    whole-field row per frame with a ``timepoint`` column."""
    h5 = tmp_path / "tl.h5"
    _build_timelapse_h5(h5, n_t=3)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP, params=_V4)
    df = out["whole_field_table"]
    assert "mNG_intermediate_mean" in df.columns  # three-region schema
    assert "timepoint" in df.columns
    assert set(df["timepoint"]) == {0, 1, 2}
    assert len(df) == 3  # one whole-field row per frame


def test_timelapse_single_cell_spans_all_timepoints(tmp_path: Path):
    """R1 (single-cell): single_cell + cp_mask over a (3,C,H,W) dataset
    yields per-cell rows per frame, each tagged with ``timepoint``. Cell 2 is
    absent from the final frame's segmentation, so it is simply missing that
    frame (no error, no placeholder row)."""
    h5 = tmp_path / "tlsc.h5"
    _build_timelapse_h5(h5, n_t=3, drop_cell_2_last_frame=True)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                       params={**_V4, "single_cell": True})
    ct = out["whole_field_table"]
    assert "timepoint" in ct.columns
    assert "cell_id" in ct.columns
    assert "mNG_cell_mean" in ct.columns
    assert set(ct["timepoint"]) == {0, 1, 2}
    assert set(ct["cell_id"]) == {1, 2}
    # Frames 0 and 1 carry both cells; frame 2 dropped cell 2.
    assert set(ct.loc[ct["timepoint"] == 0, "cell_id"]) == {1, 2}
    assert set(ct.loc[ct["timepoint"] == 1, "cell_id"]) == {1, 2}
    assert set(ct.loc[ct["timepoint"] == 2, "cell_id"]) == {1}
    assert len(ct) == 5  # 2 + 2 + 1 cells across the three frames


def test_single_t_two_region_has_no_timepoint_column(tmp_path: Path):
    """R6 backward compat: a single-timepoint (C,H,W) dataset is byte-identical
    to today — no ``timepoint`` column is added (the per-frame aggregation path
    is never taken). The v2/v4/v4_sc parity tests pin the values; this pins the
    column's absence explicitly across both modes."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    two_region = run_analysis(
        "whole_field_intensity", h5, _LAYER_MAP, params=_V2
    )["whole_field_table"]
    assert "timepoint" not in two_region.columns
    three_region = run_analysis(
        "whole_field_intensity", h5, _LAYER_MAP, params=_V4
    )["whole_field_table"]
    assert "timepoint" not in three_region.columns
    single_cell = run_analysis(
        "whole_field_intensity", h5, _LAYER_MAP,
        params={**_V4, "single_cell": True},
    )["whole_field_table"]
    assert "timepoint" not in single_cell.columns


# ── decapping-sensor-v6 stress-granule preset (U4) ────────────────


def test_preset_v6_runs(tmp_path: Path):
    """R3 happy path: the v6 stress-granule preset is a two-region run with
    ``mNG_filter='NaN'`` + ``FLIM_filter='zero'`` semantics. Its param set is
    identical to v3's (two-region, no intermediate assemblies), so a v6 run
    reproduces the v3 parity fixture byte-for-byte — pinning the exact
    FLIM/mNG-filter numeric semantics the source preset prescribes."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    out = run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                       preset="decapping-sensor-v6")
    df = out["whole_field_table"]
    # Two-region path (NOT three-region) — no intermediate compartment column.
    assert "mNG_intermediate_mean" not in df.columns
    # percent=True surfaces the pct_halo_in_mNG_* columns.
    assert "pct_halo_in_mNG_pbody" in df.columns
    # v6 == v3 params → identical output (generic pbody/dilute column names).
    _parity(df, FIXTURE_ROOT / "expected" / "v3.csv", sort_key="pbody_area_px")


def test_preset_v6_mng_filter_restricts_to_mng_mask(tmp_path: Path):
    """R3: v6's ``mNG_filter='NaN'`` genuinely restricts mNG to ``mng_mask``
    (not a silent no-op). The v6 mNG dilute mean differs from an otherwise
    identical run with ``mNG_filter='none'``."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    v6 = run_analysis("whole_field_intensity", h5, _LAYER_MAP,
                      preset="decapping-sensor-v6")["whole_field_table"]
    no_filter = run_analysis(
        "whole_field_intensity", h5, _LAYER_MAP,
        params=dict(min_size=2, mng_bg_mode="manual", mng_bg_value=0,
                    halo_bg_mode="manual", halo_bg_value=0, mNG_filter="none",
                    FLIM_filter="zero", percent=True),
    )["whole_field_table"]
    assert v6["mNG_dilute_mean"].iloc[0] != no_filter["mNG_dilute_mean"].iloc[0]


def test_preset_v6_missing_mng_mask_raises(tmp_path: Path):
    """R4 headless safety net: a v6 run with ``mng_mask`` absent from the
    layer_map raises a clear ValueError naming the role (via U3's use-case
    enforcement) — it does NOT silently no-op ``mNG_filter`` and emit
    scientifically-wrong numbers."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    layer_map = {k: v for k, v in _LAYER_MAP.items() if k != "mng_mask"}
    with pytest.raises(ValueError, match="mng_mask"):
        run_analysis("whole_field_intensity", h5, layer_map,
                     preset="decapping-sensor-v6")


def test_preset_v6_missing_interaction_mask_raises(tmp_path: Path):
    """R4 headless safety net: a v6 run with ``interaction_mask`` absent raises
    a clear ValueError naming the role — its ``FLIM_filter='zero'`` only
    applies when interaction_mask is supplied, so the core would otherwise
    silently skip the Halo filtering."""
    h5 = tmp_path / "f.h5"
    _build_h5(h5)
    layer_map = {k: v for k, v in _LAYER_MAP.items() if k != "interaction_mask"}
    with pytest.raises(ValueError, match="interaction_mask"):
        run_analysis("whole_field_intensity", h5, layer_map,
                     preset="decapping-sensor-v6")
