"""U9 — the cross-layer alignment invariant + golden passthrough + reproducibility.

This is the crown-jewel regression for overlap-aware registered stitching: the
SAME overlapping mosaic source must produce a *byte-identical* registered
``/decay`` whether the decay is written **at compress time** (Path A:
``import_dataset`` with ``register=True`` + ``.bin`` present) or **appended
afterwards** (Path B: intensity-only registered import, then
``add_decay_to_dataset`` reusing the persisted geometry). Because the per-pixel
phasor ``(g, s)`` is derived from ``/decay`` alone (intensity is
``decay.sum(axis=-1)``, never read from a sibling ``/intensity`` stack — see
``docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md``),
byte-identical decay ⇒ identical phasor by construction. This directly locks the
invariant the prior critical FLIM bug violated: a data-dependent canvas computed
once and applied identically to intensity and decay.

The file also re-states the back-compat guarantees at the integration level
(golden passthrough — single-tile + 0%-overlap byte-identical to a direct grid
write), proves placement reproducibility from the persisted integer offsets (no
recompute), and proves the coverage-gap guard (uncovered decay → phasor g=s=0,
no fill leaking into a measured cell).

Everything runs IN-PROCESS (the staleness learning forbids subprocess
validation).
"""

from __future__ import annotations

import warnings

import h5py
import numpy as np
import pytest
import tifffile

from percell4.adapters.importer import (
    _tile_positions_from_config,
    import_dataset,
    write_decay_streaming,
)
from percell4.adapters.readers import read_tiff
from percell4.application.use_cases.add_decay_to_dataset import add_decay_to_dataset
from percell4.domain.flim.phasor import compute_phasor, median_filter_gs
from percell4.domain.io.assembler import assemble_tiles, canvas_from_offsets
from percell4.domain.io.models import (
    FlimConfig,
    TileConfig,
    TokenConfig,
    ZeroPadOffsetRule,
)
from percell4.store import DatasetStore

# ── Synthetic overlapping mosaic geometry ───────────────────────────────────
# A 2x2 grid of 60px tiles stepping 45px (25% overlap) → 105x105 canvas. Mirrors
# the geometry the U6 importer tests use so phase correlation locks onto an exact
# integer offset and the assertions have exact expected values.
_TH = 60
_TW = 60
_OVERLAP = 0.25
_STEP = int(_TW * (1 - _OVERLAP))  # 45
_CANVAS = (_TH + _STEP, _TW + _STEP)  # (105, 105)
_T_BINS = 8
_CORNERS = {
    0: (0, 0),
    1: (0, _STEP),
    2: (_STEP, 0),
    3: (_STEP, _STEP),
}
_EXPECTED_OFFSETS = np.array([[0, 0], [0, 45], [45, 0], [45, 45]], dtype=np.int32)


def _textured_scene(seed: int, h: int = 110, w: int = 110) -> np.ndarray:
    """Seeded random texture + a smooth gradient → phase-correlation friendly."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 5000, size=(h, w))
    grad = np.add.outer(np.arange(h) * 7, np.arange(w) * 5)
    return (noise + grad).astype(np.uint16)


def _tile_decay(tile_index: int, th: int = _TH, tw: int = _TW) -> np.ndarray:
    """A (th, tw, T) decay whose per-pixel curve varies spatially AND by tile.

    The shape of the decay curve (a single-exponential whose decay rate ramps
    across rows) is what makes the phasor non-trivial — a flat decay would put
    every pixel at the same (g, s) and hide a misalignment. The per-tile photon
    scale (``tile_index + 1``) additionally lets overlap-winner assertions read
    which tile won a pixel straight out of the time-integrated counts.
    """
    rows = np.arange(th)[:, None]
    cols = np.arange(tw)[None, :]
    # Decay rate ramps with row+col so adjacent pixels have distinct phasors.
    rate = 0.05 + 0.002 * (rows + cols)  # (th, tw)
    tvec = np.arange(_T_BINS, dtype=np.float64)  # (T,)
    curve = np.exp(-rate[..., None] * tvec[None, None, :])  # (th, tw, T)
    scale = (tile_index + 1) * 200.0
    decay = (curve * scale).astype(np.uint32)
    return decay


def _write_overlapping_source(
    src, scene, *, channel="00", make_bin=False, decay_tiles=None,
):
    """Write 2x2 overlapping TIFF tiles (+ optional .bin decay tiles)."""
    src.mkdir(parents=True, exist_ok=True)
    for i, (y, x) in _CORNERS.items():
        tile = scene[y : y + _TH, x : x + _TW]
        tifffile.imwrite(str(src / f"img_s{i:02d}_ch{channel}.tif"), tile)
        if make_bin:
            decay = decay_tiles[i]
            (src / f"img_s{i:02d}_ch{channel}.bin").write_bytes(decay.tobytes())


def _reg_tile_config(**overrides) -> TileConfig:
    base = dict(
        grid_rows=2, grid_cols=2, grid_type="row_by_row", order="right_down",
        overlap=_OVERLAP, register=True, reference_channel="ch00",
    )
    base.update(overrides)
    return TileConfig(**base)


def _flim_params() -> dict:
    """``flim_params`` for the compress (import-time decay) path."""
    return {
        "frequency_mhz": 80.0,
        "channel_calibrations": {},
        "bin_dimensions": {
            "x_dim": _TW, "y_dim": _TH, "t_dim": _T_BINS,
            "dtype": "uint32", "dim_order": "YXT", "header_bytes": 0,
        },
    }


def _flim_config() -> FlimConfig:
    """``FlimConfig`` for the append (decay-only) path — same .bin geometry."""
    return FlimConfig(
        frequency_mhz=80.0, bin_x=_TW, bin_y=_TH, bin_t=_T_BINS,
        bin_dtype="uint32", bin_dim_order="YXT",
    )


def _phasor_from_decay(decay: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Derive the per-pixel phasor the way every FLIM consumer must.

    Intensity is ``decay.sum(axis=-1)`` (NOT a sibling ``/intensity`` stack),
    and zero-photon pixels are guarded to ``g=s=0`` — exactly the pipeline
    convention in ``compute_phasor.py`` so uncovered canvas regions can't leak
    NaN/garbage into a measured cell.
    """
    g, s = compute_phasor(decay, harmonic=1)
    intensity = decay.sum(axis=-1)
    low = intensity <= 0
    g = g.copy()
    s = s.copy()
    g[low] = 0.0
    s[low] = 0.0
    return g, s


# ── 1. THE ALIGNMENT INVARIANT (crown jewel) ────────────────────────────────


def test_compress_vs_append_decay_byte_identical_and_phasor_matches(tmp_path):
    """Path A (decay-at-compress) and Path B (decay appended afterwards) on the
    SAME registered mosaic produce byte-identical /decay AND identical per-pixel
    phasor (g, s and filtered g, s). This is the cross-layer alignment invariant
    the prior critical FLIM bug violated.
    """
    scene = _textured_scene(7)
    decay_tiles = {i: _tile_decay(i) for i in range(4)}

    # ── Path A: import WITH decay at compress time (register=True). ──────────
    src_a = tmp_path / "compress"
    _write_overlapping_source(
        src_a, scene, make_bin=True, decay_tiles=decay_tiles
    )
    h5_a = tmp_path / "compress.h5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(
            src_a, h5_a, tile_config=_reg_tile_config(),
            flim_params=_flim_params(),
        )
    decay_a = DatasetStore(h5_a).read_array("decay/ch00")

    # ── Path B: intensity-only registered import, THEN append the SAME decay. ─
    src_b = tmp_path / "append"
    # Intensity-only source: TIFFs only (no .bin), so the import persists the
    # registered geometry without writing /decay.
    _write_overlapping_source(src_b, scene, make_bin=False)
    h5_b = tmp_path / "append.h5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(src_b, h5_b, tile_config=_reg_tile_config())

    # The intensity-only import registered but wrote no decay.
    geo_b = DatasetStore(h5_b).read_stitch_geometry()
    assert geo_b.registered is True
    with h5py.File(h5_b, "r") as f:
        assert "decay" not in f

    # Append the decay from a .bin-only source dir reusing persisted offsets.
    src_bin = tmp_path / "append_bin"
    src_bin.mkdir()
    for i in range(4):
        (src_bin / f"img_s{i:02d}_ch00.bin").write_bytes(
            decay_tiles[i].tobytes()
        )
    report = add_decay_to_dataset(
        h5_path=h5_b,
        source_dir=src_bin,
        token_config=TokenConfig(),
        tile_config=_reg_tile_config(),
        flim_config=_flim_config(),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
    )
    assert report.written == ("ch00",), report.errors
    decay_b = DatasetStore(h5_b).read_array("decay/ch00")

    # ── The invariant: byte-identical /decay across the two flows. ───────────
    assert decay_a.shape == decay_b.shape == (*_CANVAS, _T_BINS)
    assert np.array_equal(decay_a, decay_b), (
        "registered /decay differs between compress and append — the "
        "cross-layer alignment invariant is broken"
    )

    # An overlap pixel equals exactly ONE source tile's photons in both flows
    # (proves alignment, not just equal-to-each-other): the 4-way center is the
    # highest-index tile (tile 3) by the pinned ascending-index priority.
    center_y, center_x = 50, 50
    # tile 3's local coordinate at canvas (50, 50): offset (45, 45) → (5, 5).
    expected_center = decay_tiles[3][5, 5, :]
    assert np.array_equal(decay_a[center_y, center_x, :], expected_center)
    assert np.array_equal(decay_b[center_y, center_x, :], expected_center)

    # ── Phasor derived from each (intensity via decay.sum(axis=-1)). ─────────
    g_a, s_a = _phasor_from_decay(decay_a)
    g_b, s_b = _phasor_from_decay(decay_b)
    np.testing.assert_array_equal(g_a, g_b)
    np.testing.assert_array_equal(s_a, s_b)

    # Filtered phasor (size=3 median == the legacy "Filtered" view) also matches.
    gf_a, sf_a = median_filter_gs(g_a, s_a, size=3)
    gf_b, sf_b = median_filter_gs(g_b, s_b, size=3)
    np.testing.assert_array_equal(gf_a, gf_b)
    np.testing.assert_array_equal(sf_a, sf_b)

    # The phasor is genuinely non-trivial (the spatially-ramped decay produces a
    # spread of g values), so the equality above is not vacuously true on a flat
    # field. Guard against a degenerate single-point phasor.
    covered = decay_a.sum(axis=-1) > 0
    assert covered.all(), "this fixture should fully cover the canvas"
    assert np.ptp(g_a[covered]) > 1e-3, "phasor must vary across pixels"


# ── 2. GOLDEN PASSTHROUGH (back-compat at the integration level) ─────────────


def test_golden_single_tile_intensity_byte_identical_to_grid(tmp_path):
    """A single-tile import is byte-identical to a direct assemble_tiles/grid
    write (the matcher-refactor scoping-collapse defense, single-tile arm)."""
    src = tmp_path / "single"
    src.mkdir()
    img = _textured_scene(3, h=_TH, w=_TW)
    tifffile.imwrite(str(src / "img_ch00.tif"), img)

    h5 = tmp_path / "single.h5"
    import_dataset(src, h5, tile_config=TileConfig(grid_rows=1, grid_cols=1))
    stored = DatasetStore(h5).read_array("intensity")

    golden = img.astype(np.float32)
    assert stored.shape == (_TH, _TW)
    assert np.array_equal(stored, golden)
    # Single-tile import persists NO registered geometry.
    assert DatasetStore(h5).read_stitch_geometry().registered is False


def test_golden_2x2_zero_overlap_intensity_byte_identical_to_grid(tmp_path):
    """A 2x2 0%-overlap (register=False) import is byte-identical to a direct
    assemble_tiles grid write — mirrors U6's AE3 guarantee at this level."""
    scene = _textured_scene(7)
    src = tmp_path / "grid"
    _write_overlapping_source(src, scene)  # TIFF tiles only

    h5 = tmp_path / "grid.h5"
    # register=False, overlap=0 → the legacy edge-to-edge grid path.
    import_dataset(src, h5, tile_config=TileConfig(grid_rows=2, grid_cols=2))
    stored = DatasetStore(h5).read_array("intensity")

    tiles = {
        i: read_tiff(str(src / f"img_s{i:02d}_ch00.tif"))["array"]
        for i in range(4)
    }
    golden = assemble_tiles(tiles, grid_rows=2, grid_cols=2).astype(np.float32)
    # Edge-to-edge grid: 2x2 of 60px tiles = 120x120 (no overlap fusion).
    assert stored.shape == (120, 120)
    assert np.array_equal(stored, golden)
    assert DatasetStore(h5).read_stitch_geometry().registered is False


def test_golden_2x2_zero_overlap_decay_byte_identical_to_grid(tmp_path):
    """The 0%-overlap decay grid path (pixel_offsets=None) is byte-identical to
    a direct grid write_decay_streaming — re-states the decay back-compat gate.
    """
    scene = _textured_scene(7)
    decay_tiles = {i: _tile_decay(i) for i in range(4)}

    src = tmp_path / "grid"
    _write_overlapping_source(
        src, scene, make_bin=True, decay_tiles=decay_tiles
    )
    h5 = tmp_path / "grid.h5"
    # No register → decay placed on the edge-to-edge grid.
    import_dataset(
        src, h5, tile_config=TileConfig(grid_rows=2, grid_cols=2),
        flim_params=_flim_params(),
    )
    stored = DatasetStore(h5).read_array("decay/ch00")

    # Golden: a direct grid write_decay_streaming on the same tiles (the
    # canonical decay-write primitive, pixel_offsets=None → edge-to-edge grid).
    golden_h5 = tmp_path / "golden.h5"
    gstore = DatasetStore(golden_h5)
    gstore.create(metadata={"channel_names": ["ch00"]})
    tile_bins = {i: str(src / f"img_s{i:02d}_ch00.bin") for i in range(4)}
    grid_cfg = TileConfig(
        grid_rows=2, grid_cols=2, grid_type="row_by_row", order="right_down",
    )
    positions = _tile_positions_from_config(grid_cfg)
    write_decay_streaming(
        h5_path=str(golden_h5),
        channel_name="ch00",
        tile_bins=tile_bins,
        bin_dims={
            "x_dim": _TW, "y_dim": _TH, "t_dim": _T_BINS,
            "dtype": "uint32", "dim_order": "YXT", "header_bytes": 0,
        },
        tile_h=_TH,
        tile_w=_TW,
        n_bins=_T_BINS,
        out_h=120,
        out_w=120,
        positions=positions,
        use_tiling=True,
    )
    golden = DatasetStore(golden_h5).read_array("decay/ch00")

    assert stored.shape == golden.shape == (120, 120, _T_BINS)
    assert np.array_equal(stored, golden)


# ── 3. REPRODUCIBILITY (offsets are persisted ints — no recompute) ───────────


def test_persisted_offsets_reproduce_identical_placement(tmp_path):
    """Read the persisted offsets, rebuild the canvas placement IN-PROCESS from
    those ints, and assert identical placement to the stored /decay. Offsets are
    persisted integers, so re-placement recomputes nothing (R12).
    """
    scene = _textured_scene(7)
    decay_tiles = {i: _tile_decay(i) for i in range(4)}

    src = tmp_path / "reg"
    _write_overlapping_source(
        src, scene, make_bin=True, decay_tiles=decay_tiles
    )
    h5 = tmp_path / "reg.h5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_dataset(
            src, h5, tile_config=_reg_tile_config(),
            flim_params=_flim_params(),
        )

    store = DatasetStore(h5)
    geo = store.read_stitch_geometry()
    assert geo.registered is True
    np.testing.assert_array_equal(geo.offsets, _EXPECTED_OFFSETS)

    stored_decay = store.read_array("decay/ch00")

    # Rebuild the decay canvas purely from the persisted integer offsets, using
    # the SAME ascending-index overwrite priority. canvas_from_offsets is the
    # single canvas source.
    canvas_hw = canvas_from_offsets(geo.offsets, (_TH, _TW))
    assert canvas_hw == _CANVAS
    rebuilt = np.zeros((*canvas_hw, _T_BINS), dtype=stored_decay.dtype)
    for i in sorted(decay_tiles):  # ascending index → highest wins overlap
        y0, x0 = int(geo.offsets[i, 0]), int(geo.offsets[i, 1])
        rebuilt[y0 : y0 + _TH, x0 : x0 + _TW, :] = decay_tiles[i]

    assert np.array_equal(rebuilt, stored_decay), (
        "re-placing tiles from the persisted integer offsets did not reproduce "
        "the stored /decay — placement is not deterministic from geometry"
    )


# ── 4. COVERAGE GAP (uncovered region → phasor g=s=0, no fill leak) ──────────


def test_uncovered_region_decay_zero_and_phasor_zero(tmp_path):
    """A binned registered import leaves an uncovered corner band (coverage <
    1.0); that region's /decay is all-zero and the phasor guard yields g=s=0
    there — no fill-zeros leaking into a measured cell.
    """
    scene = _textured_scene(7)
    decay_tiles = {i: _tile_decay(i) for i in range(4)}

    src = tmp_path / "reg"
    _write_overlapping_source(
        src, scene, make_bin=True, decay_tiles=decay_tiles
    )
    h5 = tmp_path / "reg.h5"
    # creation_bin=2: post-bin step exceeds the post-bin tile overlap, leaving a
    # deterministic uncovered band (the same gap U6's coverage-warning test hits).
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import_dataset(
            src, h5, tile_config=_reg_tile_config(),
            flim_params=_flim_params(), creation_bin=2,
        )
    msgs = [str(w.message).lower() for w in caught]
    assert any("covers only" in m for m in msgs), msgs

    store = DatasetStore(h5)
    decay = store.read_array("decay/ch00")

    # There IS an uncovered region (all-zero decay) on this binned canvas.
    intensity = decay.sum(axis=-1)
    uncovered = intensity == 0
    assert uncovered.any(), "expected an uncovered band on the binned canvas"

    # Every uncovered pixel's decay is exactly zero (no fill bleed).
    assert np.array_equal(
        decay[uncovered], np.zeros_like(decay[uncovered])
    )

    # The phasor guard zeros g and s exactly there — NaN/garbage cannot enter a
    # measured cell from the uncovered fill.
    g, s = _phasor_from_decay(decay)
    assert np.all(g[uncovered] == 0.0)
    assert np.all(s[uncovered] == 0.0)
    # Covered pixels still carry a real (finite) phasor.
    assert np.all(np.isfinite(g[~uncovered]))
    assert np.all(np.isfinite(s[~uncovered]))
