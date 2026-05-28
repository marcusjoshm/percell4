"""Pure-function unit tests for ``run_one_image_set``.

These tests build small synthetic numpy arrays in-test, drive the
pure function directly, and assert on the returned row counts +
column values. No file I/O, no fixture TIFFs — the regression test at
tests/test_scripts/test_per_particle_regression.py covers the CLI's
end-to-end byte-flow against committed CSVs.

The pure function lives at
src/percell4/domain/analysis/_impl/per_particle_donut.py
(extracted from the CLI in U4).
"""
from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import disk

from percell4.domain.analysis._impl.per_particle_donut import (
    run_one_image_set,
)


# Mirror the CLI's ORIGINAL_DEFAULTS argparse defaults. Tests that need
# different values override individual keys via dict-spread.
DEFAULT_PARAMS = {
    "buffer": 4,
    "donut": 5,
    "bg_mode": "donut",
    "bg_value": 1,
    "exclude_cap_zero": True,
    "min_size": 10,
    "bgsub_k": 2.5,
    "no_bgsub": True,  # Tests use synthetic arrays where the histogram
    # fitting is unstable; we disable bg-sub for determinism. The CLI
    # exercises the bg-sub path in the regression fixture.
    "single_cell": False,
    "export_donuts": False,
}


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_cap(shape: tuple[int, int], blob_centers: list[tuple[int, int]],
              radius: int = 5, base: float = 1000.0,
              blob_intensity: float = 6000.0, seed: int = 0
              ) -> np.ndarray:
    rng = np.random.default_rng(seed=seed)
    img = rng.normal(loc=base, scale=20.0, size=shape)
    img = np.clip(img, 0, None)
    for (r, c) in blob_centers:
        rr, cc = disk((r, c), radius, shape=shape)
        img[rr, cc] += blob_intensity
    return img.astype(np.uint16)


def _make_norm(shape: tuple[int, int], blob_centers: list[tuple[int, int]],
               radius: int = 5, base: float = 1200.0,
               blob_intensity: float = 4500.0, seed: int = 1
               ) -> np.ndarray:
    rng = np.random.default_rng(seed=seed)
    img = rng.normal(loc=base, scale=20.0, size=shape)
    img = np.clip(img, 0, None)
    for (r, c) in blob_centers:
        rr, cc = disk((r, c), radius, shape=shape)
        img[rr, cc] += blob_intensity
    return img.astype(np.uint16)


def _make_mask(shape: tuple[int, int], centers: list[tuple[int, int]],
               radius: int = 5) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    for (r, c) in centers:
        rr, cc = disk((r, c), radius, shape=shape)
        mask[rr, cc] = 255
    return mask


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_happy_path_pbody_only() -> None:
    """P-body inputs only — no SG branch, no cp_mask."""
    shape = (64, 64)
    pbody_centers = [(15, 15), (45, 45)]
    cap = _make_cap(shape, pbody_centers)
    pnorm = _make_norm(shape, pbody_centers)
    pbody_mask = _make_mask(shape, pbody_centers)

    out = run_one_image_set(
        cap=cap, pbody_mask=pbody_mask, pnorm=pnorm,
        **DEFAULT_PARAMS,
    )

    assert out["pbody_rows"] is not None
    assert out["sg_rows"] is None
    assert out["pbody_donut_mask"] is None
    assert out["sg_donut_mask"] is None
    assert len(out["pbody_rows"]) == len(pbody_centers)
    # Each row has the expected id + area > 0 + finite cap_pbody_mean
    for row in out["pbody_rows"]:
        assert row["pbody_area_px"] > 0
        assert row["donut_area_px"] > 0
        assert np.isfinite(row["cap_pbody_mean"])


def test_happy_path_sg_only() -> None:
    """SG inputs only — no P-body branch."""
    shape = (64, 64)
    sg_centers = [(15, 30)]
    cap = _make_cap(shape, sg_centers, radius=8)
    sgnorm = _make_norm(shape, sg_centers, radius=8)
    sg_mask = _make_mask(shape, sg_centers, radius=8)

    out = run_one_image_set(
        cap=cap, sg_mask=sg_mask, sgnorm=sgnorm,
        **DEFAULT_PARAMS,
    )

    assert out["sg_rows"] is not None
    assert out["pbody_rows"] is None
    assert len(out["sg_rows"]) == 1
    assert out["sg_rows"][0]["sg_id"] == 1
    assert np.isfinite(out["sg_rows"][0]["cap_sg_mean"])


def test_happy_path_both_with_sg_exclusion() -> None:
    """A P-body lying inside an SG must end up with NaN cap_pbody_mean
    because every Cap pixel of that P-body was NaN'd by the SG-exclusion
    side effect before the P-body branch ran.
    """
    shape = (96, 96)
    # P-body at (20, 20) sits inside SG at (20, 20). A second P-body at
    # (70, 70) is well outside the SG.
    pbody_centers = [(20, 20), (70, 70)]
    sg_centers = [(20, 20)]

    cap = _make_cap(shape, pbody_centers + sg_centers, radius=5,
                    blob_intensity=6000.0)
    pnorm = _make_norm(shape, pbody_centers)
    sgnorm = _make_norm(shape, sg_centers)
    pbody_mask = _make_mask(shape, pbody_centers, radius=5)
    sg_mask = _make_mask(shape, sg_centers, radius=10)

    out = run_one_image_set(
        cap=cap,
        pbody_mask=pbody_mask, pnorm=pnorm,
        sg_mask=sg_mask, sgnorm=sgnorm,
        **DEFAULT_PARAMS,
    )

    assert out["pbody_rows"] is not None
    assert out["sg_rows"] is not None
    assert len(out["pbody_rows"]) == 2
    assert len(out["sg_rows"]) == 1

    by_id = {r["pbody_id"]: r for r in out["pbody_rows"]}
    # P-body 1 is the (20, 20) blob inside the SG — cap_pbody_mean must be NaN.
    assert np.isnan(by_id[1]["cap_pbody_mean"])
    # P-body 2 is outside the SG — cap_pbody_mean is finite.
    assert np.isfinite(by_id[2]["cap_pbody_mean"])


def test_happy_path_single_cell_mode() -> None:
    """Single-cell mode produces one row per unique non-zero cp_mask label."""
    shape = (96, 96)
    pbody_centers = [(15, 15), (25, 25), (75, 75)]
    cap = _make_cap(shape, pbody_centers)
    pnorm = _make_norm(shape, pbody_centers)
    pbody_mask = _make_mask(shape, pbody_centers, radius=5)
    # Two cells: top half = cell 1, bottom half = cell 2.
    cp_mask = np.zeros(shape, dtype=np.uint16)
    cp_mask[0:48, :] = 1
    cp_mask[48:, :] = 2

    out = run_one_image_set(
        cap=cap, pbody_mask=pbody_mask, pnorm=pnorm, cp_mask=cp_mask,
        **{**DEFAULT_PARAMS, "single_cell": True},
    )

    assert out["pbody_rows"] is not None
    cell_ids = sorted(r["cell_id"] for r in out["pbody_rows"])
    assert cell_ids == [1, 2]
    # Two particles map to cell 1, one to cell 2.
    by_cell = {r["cell_id"]: r for r in out["pbody_rows"]}
    assert by_cell[1]["n_pbodys"] == 2
    assert by_cell[2]["n_pbodys"] == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_edge_no_particles_after_min_size_filter() -> None:
    """When min_size is larger than every region's area, pbody_rows is
    an empty list (NOT None — the branch was enabled, it just found
    nothing)."""
    shape = (64, 64)
    pbody_centers = [(20, 20), (40, 40)]
    cap = _make_cap(shape, pbody_centers)
    pnorm = _make_norm(shape, pbody_centers)
    pbody_mask = _make_mask(shape, pbody_centers, radius=5)

    out = run_one_image_set(
        cap=cap, pbody_mask=pbody_mask, pnorm=pnorm,
        **{**DEFAULT_PARAMS, "min_size": 10000},
    )

    assert out["pbody_rows"] == []
    assert out["sg_rows"] is None


def test_edge_cap_all_zeros_no_crash() -> None:
    """Cap = zeros must not crash; intensity columns are NaN (because
    every donut pixel value is zero, then the exclude_cap_zero branch
    falls back to the all-NaN path which yields NaN means).

    With exclude_cap_zero=True and all-zero cap, the donut bg-estimate
    path falls back to ``cap_donut_raw[~isnan]`` (all zeros) then
    further skips if len==0. With all zeros + no NaN'ing (no_bgsub=True),
    cap_donut_for_bg is non-empty (length=donut pixel count, all zero),
    so the analysis proceeds with bg_value=0. cap_pbody_mean ends up 0
    (max(0-0,0)=0).
    """
    shape = (64, 64)
    pbody_centers = [(20, 20)]
    cap = np.zeros(shape, dtype=np.uint16)
    pnorm = _make_norm(shape, pbody_centers)
    pbody_mask = _make_mask(shape, pbody_centers, radius=5)

    # Must not raise.
    out = run_one_image_set(
        cap=cap, pbody_mask=pbody_mask, pnorm=pnorm,
        **{**DEFAULT_PARAMS, "exclude_cap_zero": False},
    )

    assert out["pbody_rows"] is not None
    # bg_value == 0 (donut-median over all zeros); cap_pbody_mean == 0.
    row = out["pbody_rows"][0]
    assert row["bg_value"] == 0
    assert row["cap_pbody_mean"] == 0
    # pnorm columns are real (non-NaN) since pnorm has signal.
    assert np.isfinite(row["pnorm_pbody_mean"])


def test_edge_export_donuts_returns_masks() -> None:
    """export_donuts=True returns uint8 donut-union masks for each
    enabled branch; export_donuts=False returns None for both."""
    shape = (64, 64)
    pbody_centers = [(20, 20)]
    sg_centers = [(45, 45)]
    cap = _make_cap(shape, pbody_centers + sg_centers, radius=5)
    pnorm = _make_norm(shape, pbody_centers)
    sgnorm = _make_norm(shape, sg_centers)
    pbody_mask = _make_mask(shape, pbody_centers, radius=5)
    sg_mask = _make_mask(shape, sg_centers, radius=5)

    # With export
    out_on = run_one_image_set(
        cap=cap,
        pbody_mask=pbody_mask, pnorm=pnorm,
        sg_mask=sg_mask, sgnorm=sgnorm,
        **{**DEFAULT_PARAMS, "export_donuts": True},
    )
    assert isinstance(out_on["pbody_donut_mask"], np.ndarray)
    assert out_on["pbody_donut_mask"].dtype == np.uint8
    assert out_on["pbody_donut_mask"].shape == shape
    assert (out_on["pbody_donut_mask"] == 255).any()
    assert isinstance(out_on["sg_donut_mask"], np.ndarray)
    assert out_on["sg_donut_mask"].dtype == np.uint8

    # Without export
    out_off = run_one_image_set(
        cap=cap,
        pbody_mask=pbody_mask, pnorm=pnorm,
        sg_mask=sg_mask, sgnorm=sgnorm,
        **DEFAULT_PARAMS,
    )
    assert out_off["pbody_donut_mask"] is None
    assert out_off["sg_donut_mask"] is None


def test_error_single_cell_without_cp_mask_raises() -> None:
    """single_cell=True but cp_mask=None must raise — defensive
    validation that catches dialog bugs early."""
    shape = (32, 32)
    pbody_centers = [(15, 15)]
    cap = _make_cap(shape, pbody_centers)
    pnorm = _make_norm(shape, pbody_centers)
    pbody_mask = _make_mask(shape, pbody_centers, radius=5)

    with pytest.raises(ValueError, match="single_cell"):
        run_one_image_set(
            cap=cap, pbody_mask=pbody_mask, pnorm=pnorm, cp_mask=None,
            **{**DEFAULT_PARAMS, "single_cell": True},
        )
