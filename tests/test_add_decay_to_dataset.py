"""Tests for add_decay_to_dataset use case (U3 of TCSPC append thread)."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from percell4.application.use_cases.add_decay_to_dataset import (
    AppendReport,
    add_decay_to_dataset,
)
from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    ExplicitRule,
    FlimConfig,
    TileConfig,
    TokenConfig,
    ZeroPadOffsetRule,
)
from percell4.store import DatasetStore


def _h5_with_intensity(tmp_path, channel_names=("ch00", "ch01")):
    """Create an .h5 with /intensity (3D C,H,W) + channel_names metadata."""
    store = DatasetStore(tmp_path / "experiment.h5")
    store.create(metadata={"channel_names": list(channel_names)})
    intensity = np.zeros((len(channel_names), 32, 32), dtype=np.uint16)
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    return store


def _make_bin_files(source_dir: Path, names_and_shapes):
    """Create empty .bin placeholder files; reader is mocked in tests."""
    source_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names_and_shapes:
        p = source_dir / name
        p.write_bytes(b"\0" * 32)  # placeholder
        paths.append(p)
    return paths


@pytest.fixture
def mock_read_flim_bin(monkeypatch):
    """Replace read_flim_bin with a deterministic synthetic reader.

    Patches both the use-case's local binding AND the source module so
    the shared ``write_decay_streaming`` helper (which imports
    read_flim_bin lazily inside the function body) also picks up the fake.
    """
    def _fake_read(path, **kwargs):
        # Return a fake decay (8, 8, 4) array — small for fast tests
        return {
            "array": np.full((8, 8, 4), int(str(path).encode().__hash__()) & 0xFF, dtype=np.uint16),
            "intensity": np.zeros((8, 8), dtype=np.uint16),
            "metadata": {"shape": (8, 8, 4)},
        }
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _fake_read,
    )
    # write_decay_streaming does ``from percell4.adapters.readers import read_flim_bin``
    # inside its body — patch the source module to cover that binding too.
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _fake_read,
    )
    return _fake_read


# ── Happy paths ─────────────────────────────────────────────────────────


def test_add_decay_single_channel_zero_pad_offset(tmp_path, mock_read_flim_bin):
    """One .bin matched to one TIFF channel via ZeroPadOffsetRule(2,1)."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    source = tmp_path / "bin"
    _make_bin_files(source, ["exp_s0_ch1.bin"])  # token "1" → "00"

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    assert isinstance(report, AppendReport)
    assert report.written == ("ch00",)
    assert report.unmatched == ()
    assert report.ambiguous == ()
    assert report.errors == {}
    with h5py.File(store.path, "r") as f:
        assert "decay/ch00" in f
        assert "provenance/decay/ch00" in f


def test_add_decay_two_channels_default_composite_rule(tmp_path, mock_read_flim_bin):
    """Two .bin files matched via default CompositeRule(ZeroPadOffset(2,1), BaseStem)."""
    store = _h5_with_intensity(tmp_path)
    source = tmp_path / "bin"
    _make_bin_files(source, ["exp_s0_ch1.bin", "exp_s0_ch2.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=CompositeRule(rules=(
            ZeroPadOffsetRule(pad_width=2, offset=1),
            BaseStemRule(),
        )),
    )

    assert set(report.written) == {"ch00", "ch01"}


def test_add_decay_with_tile_stitching(tmp_path, mock_read_flim_bin):
    """4 tiles per channel are stitched into 16x16x4 output."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    source = tmp_path / "bin"
    _make_bin_files(source, [
        "exp_s1_ch1.bin", "exp_s2_ch1.bin", "exp_s3_ch1.bin", "exp_s4_ch1.bin",
    ])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=2, grid_cols=2),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    assert report.written == ("ch00",)
    with h5py.File(store.path, "r") as f:
        # 2x2 grid of 8x8 tiles → 16x16 stitched
        assert f["decay/ch00"].shape == (16, 16, 4)


def test_add_decay_rotates_stitched_array(tmp_path, monkeypatch):
    """rotate_k applies a 90°·k CCW rotation in the (H, W) plane post-stitch.

    Uses a non-square, asymmetric synthetic decay so the rotation is
    observable in both saved shape and content.
    """
    def _patterned_read(path, **kwargs):
        h, w, t = 4, 8, 2
        arr = np.zeros((h, w, t), dtype=np.uint16)
        for r in range(h):
            arr[r, :, 0] = r + 1  # row index encoded into time bin 0
        return {
            "array": arr,
            "intensity": arr[..., 0].copy(),
            "metadata": {"shape": (h, w, t)},
        }
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _patterned_read,
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _patterned_read,
    )

    # Without rotation: shape stays (H=4, W=8, T=2)
    store0 = _h5_with_intensity(tmp_path / "norot", channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])
    add_decay_to_dataset(
        h5_path=store0.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=4, bin_t=2),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )
    with h5py.File(store0.path, "r") as f:
        assert f["decay/ch00"].shape == (4, 8, 2)

    # With rotate_k=1 (90° CCW): (H=4, W=8, T=2) → (W=8, H=4, T=2)
    store1 = _h5_with_intensity(tmp_path / "rot", channel_names=("ch00",))
    add_decay_to_dataset(
        h5_path=store1.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=4, bin_t=2),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        rotate_k=1,
    )
    with h5py.File(store1.path, "r") as f:
        rotated = f["decay/ch00"][...]
    assert rotated.shape == (8, 4, 2)
    # 90° CCW: original row r=0 (all 1s) → rotated column 0 (all 1s).
    # Original row r=3 (all 4s) → rotated column W-1-r = 4-1-3 = 0… wait,
    # that maps the original top edge (row 0) to the rotated LEFT column,
    # and the original bottom edge (row 3) to the rotated RIGHT column.
    assert (rotated[:, 0, 0] == 1).all()
    assert (rotated[:, -1, 0] == 4).all()


def test_add_decay_rotate_k_zero_is_noop(tmp_path, mock_read_flim_bin):
    """rotate_k=0 (default) leaves shape unchanged."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        rotate_k=0,
    )

    with h5py.File(store.path, "r") as f:
        assert f["decay/ch00"].shape == (8, 8, 4)


def test_add_decay_uses_supplied_intensity_channels_with_overrides(
    tmp_path, mock_read_flim_bin,
):
    """Caller passes IntensityChannel records → use case skips token
    derivation from channel names.

    Reproduces the dialog scenario: TIFF channels carry semantic names
    ``CA-SiR``, ``mNG``, ``mTQ2`` (no parseable digit suffix). Without
    overrides, the matcher would see token "" for every channel and
    return zero bindings ("Appended 0 decay layer(s)"). With overrides,
    channel ``CA-SiR`` ↔ token ``"00"`` ↔ ``_ch00.bin`` matches.
    """
    from percell4.domain.io.cross_format import IntensityChannel

    store = DatasetStore(tmp_path / "experiment.h5")
    store.create(metadata={"channel_names": ["CA-SiR", "mNG", "mTQ2"]})
    intensity = np.zeros((3, 32, 32), dtype=np.uint16)
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})

    src = tmp_path / "bin"
    _make_bin_files(src, [
        "exp_s0_ch00.bin",
        "exp_s0_ch01.bin",
        "exp_s0_ch02.bin",
    ])

    # Dialog-style overrides: positional fallback seeded as "00"/"01"/"02"
    overrides = [
        IntensityChannel(name="CA-SiR", token="00"),
        IntensityChannel(name="mNG", token="01"),
        IntensityChannel(name="mTQ2", token="02"),
    ]

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
        intensity_channels=overrides,
    )

    assert set(report.written) == {"CA-SiR", "mNG", "mTQ2"}
    assert report.errors == {}


def test_add_decay_without_intensity_channels_falls_back_to_metadata(
    tmp_path, mock_read_flim_bin,
):
    """When intensity_channels is omitted, the use case still derives
    from store.metadata['channel_names'] (back-compat for headless
    callers and existing tests)."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    assert report.written == ("ch00",)


def test_add_decay_flip_axis_0_vertical(tmp_path, monkeypatch):
    """flip_axis=0 mirrors the (H, W) plane top↔bottom (np.flipud)."""
    def _patterned_read(path, **kwargs):
        h, w, t = 4, 8, 2
        arr = np.zeros((h, w, t), dtype=np.uint16)
        for r in range(h):
            arr[r, :, 0] = r + 1
        return {"array": arr, "intensity": arr[..., 0].copy(),
                "metadata": {"shape": (h, w, t)}}
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _patterned_read,
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _patterned_read,
    )

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    add_decay_to_dataset(
        h5_path=store.path, source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=4, bin_t=2,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        flip_axis=0,
    )
    with h5py.File(store.path, "r") as f:
        flipped = f["decay/ch00"][...]
    assert flipped.shape == (4, 8, 2)
    # Original row 0 (all 1s) should now be at LAST row position
    assert (flipped[-1, :, 0] == 1).all()
    assert (flipped[0, :, 0] == 4).all()


def test_add_decay_flip_axis_1_horizontal(tmp_path, monkeypatch):
    """flip_axis=1 mirrors the (H, W) plane left↔right (np.fliplr)."""
    def _patterned_read(path, **kwargs):
        h, w, t = 4, 8, 2
        arr = np.zeros((h, w, t), dtype=np.uint16)
        for c in range(w):
            arr[:, c, 0] = c + 1
        return {"array": arr, "intensity": arr[..., 0].copy(),
                "metadata": {"shape": (h, w, t)}}
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _patterned_read,
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _patterned_read,
    )

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    add_decay_to_dataset(
        h5_path=store.path, source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=4, bin_t=2,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        flip_axis=1,
    )
    with h5py.File(store.path, "r") as f:
        flipped = f["decay/ch00"][...]
    assert flipped.shape == (4, 8, 2)
    # Original column 0 (all 1s) should now be at LAST column
    assert (flipped[:, -1, 0] == 1).all()
    assert (flipped[:, 0, 0] == 8).all()


def test_add_decay_flip_axis_none_is_noop(tmp_path, mock_read_flim_bin):
    """flip_axis=None leaves /decay unchanged."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    add_decay_to_dataset(
        h5_path=store.path, source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        flip_axis=None,
    )
    with h5py.File(store.path, "r") as f:
        assert f["decay/ch00"].shape == (8, 8, 4)


def test_add_decay_rotate_then_flip_compose(tmp_path, mock_read_flim_bin):
    """Rotation runs first, then flip — composes correctly."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    add_decay_to_dataset(
        h5_path=store.path, source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        rotate_k=1,
        flip_axis=0,
    )
    with h5py.File(store.path, "r") as f:
        # 8x8x4 stays 8x8x4 under rotation+flip on a square
        assert f["decay/ch00"].shape == (8, 8, 4)


def test_add_decay_stitch_then_rotate_compose(tmp_path, mock_read_flim_bin):
    """2x2 tile stitch + 90° CCW rotation → 16x16 stitched, still square."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, [
        "exp_s1_ch1.bin", "exp_s2_ch1.bin", "exp_s3_ch1.bin", "exp_s4_ch1.bin",
    ])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=2, grid_cols=2),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        rotate_k=1,
    )

    assert report.written == ("ch00",)
    with h5py.File(store.path, "r") as f:
        assert f["decay/ch00"].shape == (16, 16, 4)


def test_add_decay_multitile_rotate_flip_repositions_tiles_globally(
    tmp_path, monkeypatch,
):
    """Multi-tile rot+flip moves entire tile blocks (not just rotates contents).

    Regression for the "individual tiles are rotated/flipped in place" bug
    report: verifies that on a 4x4 grid stitched as
    ``snake_by_column + bottom_right`` followed by ``rotate_k=1`` (CCW90)
    and ``flip_axis=1`` (horizontal), tile #0 — placed by the stitch at
    grid block (3,3) — ends up at the rotated+flipped output's grid block
    (0,0), proving the transforms run on the full stitched array, not
    per-tile.
    """
    grid_rows, grid_cols = 4, 4
    tile_h, tile_w, t_dim = 4, 4, 2

    def _patterned_read(path, **kwargs):
        # Use the file stem's _s<idx> token to embed the tile id in T=0 plane
        import re as _re
        m = _re.search(r"_s(\d+)", str(path))
        idx = int(m.group(1)) if m else 0
        arr = np.zeros((tile_h, tile_w, t_dim), dtype=np.uint16)
        arr[:, :, 0] = idx
        return {"array": arr, "intensity": arr[..., 0].astype(np.float32),
                "metadata": {"shape": (tile_h, tile_w, t_dim)}}
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _patterned_read,
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _patterned_read,
    )

    # /intensity sized to match: 4x4 grid of 4x4 tiles = 16x16
    store = DatasetStore(tmp_path / "experiment.h5")
    store.create(metadata={"channel_names": ["ch00"]})
    intensity = np.zeros((1, grid_rows * tile_h, grid_cols * tile_w),
                         dtype=np.uint16)
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})

    src = tmp_path / "bin"
    src.mkdir()
    for i in range(16):
        # offset=0 so token "00" maps to "ch00"; tile token from _s<idx>
        (src / f"exp_s{i:02d}_ch00.bin").write_bytes(b"\0")

    report = add_decay_to_dataset(
        h5_path=store.path, source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(
            grid_rows=grid_rows, grid_cols=grid_cols,
            grid_type="snake_by_column", order="bottom_right",
        ),
        flim_config=FlimConfig(bin_x=tile_w, bin_y=tile_h, bin_t=t_dim,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
        rotate_k=1, flip_axis=1,
    )
    assert report.written == ("ch00",), report.errors

    with h5py.File(store.path, "r") as f:
        decay = f["decay/ch00"][...]

    # Read tile-id at the top-left pixel of every grid block in the output.
    # If rot+flip operates on the FULL stitched array, the resulting
    # tile-id grid is the canonical raster scan (top_left + snake_by_row):
    #     0  1  2  3
    #     7  6  5  4
    #     8  9 10 11
    #    15 14 13 12
    # If rot+flip were applied per-tile (the bug), tile #0 would still be
    # at output (3,3) — same grid slot the stitch placed it.
    tid = decay[..., 0]
    out_grid = np.array([
        [int(tid[r * tile_h, c * tile_w]) for c in range(grid_cols)]
        for r in range(grid_rows)
    ])
    expected = np.array([
        [0, 1, 2, 3],
        [7, 6, 5, 4],
        [8, 9, 10, 11],
        [15, 14, 13, 12],
    ])
    assert np.array_equal(out_grid, expected), (
        f"rot+flip not applied at full-image level\n"
        f"got:\n{out_grid}\nexpected:\n{expected}"
    )


# ── Error paths ─────────────────────────────────────────────────────────


def test_add_decay_no_intensity_returns_error(tmp_path, mock_read_flim_bin):
    """An .h5 without /intensity → AppendReport.errors['intensity']."""
    store = DatasetStore(tmp_path / "empty.h5")
    store.create()  # no /intensity, no channel_names
    source = tmp_path / "bin"
    _make_bin_files(source, ["exp_s0_ch1.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    assert "intensity" in report.errors
    assert report.written == ()


def test_add_decay_no_bin_files_returns_error(tmp_path, mock_read_flim_bin):
    """Empty source_dir → AppendReport.errors['scan']."""
    store = _h5_with_intensity(tmp_path)
    source = tmp_path / "bin"
    source.mkdir()

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    assert "scan" in report.errors
    assert report.written == ()


def test_add_decay_ambiguous_no_explicit_returns_early(tmp_path, mock_read_flim_bin):
    """Ambiguous matches surface in report and nothing is written."""
    _h5_with_intensity(tmp_path, channel_names=("ch00", "ch01"))
    source = tmp_path / "bin"
    _make_bin_files(source, ["Dataset.bin"])  # no token, ambiguous via stem

    # Use BaseStemRule alone — both channels have base_stem set to be ambiguous
    # (caller sets up DiscoveredFile.tokens to seed both with prefix-matching stems)
    # We do this by giving channel_names that both prefix-match "Dataset"
    # — actually BaseStemRule needs intensity_channels with base_stem; the use
    # case derives base_stem from TIFF stems. Without a TIFF context here, the
    # use case falls back to computing base_stem from channel_names. For this
    # test, we need the use case to set base_stem appropriately.
    #
    # Simpler: use ExplicitRule with TWO entries pointing the same .bin to two
    # channels (impossible — mapping is dict, only one entry per path). So
    # ambiguity testing needs a different setup. Use BaseStemRule + a context
    # where two channels both stem-match.

    # Re-create store with channels whose base stems both prefix-match the bin.
    store2 = DatasetStore(tmp_path / "ambig.h5")
    store2.create(metadata={
        "channel_names": ["chA", "chB"],
        "channel_base_stems": ["Dataset_A", "Dataset_B"],
    })
    intensity = np.zeros((2, 32, 32), dtype=np.uint16)
    store2.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})

    report = add_decay_to_dataset(
        h5_path=store2.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=BaseStemRule(),
    )

    assert report.written == ()
    assert len(report.ambiguous) == 1
    bin_path, candidates = report.ambiguous[0]
    assert set(candidates) == {"chA", "chB"}


def test_add_decay_pre_flight_existing_without_force(tmp_path, mock_read_flim_bin):
    """When /decay/<name> already exists, pre-flight surfaces it without writing anything else."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    source = tmp_path / "bin"
    _make_bin_files(source, ["exp_s0_ch1.bin"])

    # First run lands ch00
    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    # Second run should find the conflict in pre-flight
    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    assert "ch00" in report.errors
    assert "decay" in report.errors["ch00"].lower()
    assert report.written == ()


def test_add_decay_explicit_rule_overrides(tmp_path, mock_read_flim_bin):
    """ExplicitRule maps a .bin file to a channel regardless of tokens."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00", "ch01"))
    source = tmp_path / "bin"
    bins = _make_bin_files(source, ["exp_s0_ch99.bin"])  # token would not match anything

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ExplicitRule(mapping=((str(bins[0].resolve()), "ch01"),)),
    )

    assert report.written == ("ch01",)


def test_add_decay_records_provenance(tmp_path, mock_read_flim_bin):
    """Provenance attrs include source_path, rule type, sha256, etc."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    source = tmp_path / "bin"
    _make_bin_files(source, ["exp_s0_ch1.bin"])

    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )

    with h5py.File(store.path, "r") as f:
        prov = dict(f["provenance/decay/ch00"].attrs)
    assert "source_path" in prov
    assert prov["cross_format_rule"] == "ZeroPadOffsetRule"
    assert "match_evidence" in prov
    assert "content_sha256" in prov
    assert len(prov["content_sha256"]) == 64
    assert "timestamp_utc" in prov


def test_add_decay_progress_callback_called(tmp_path, mock_read_flim_bin):
    """progress_callback receives status messages."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    source = tmp_path / "bin"
    _make_bin_files(source, ["exp_s0_ch1.bin"])
    messages: list[str] = []

    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=source,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        progress_callback=messages.append,
    )

    # At least one message should have been delivered
    assert len(messages) > 0


# ── Spatial binning ─────────────────────────────────────────────────────


def test_add_decay_spatial_bin_k1_is_noop(tmp_path, mock_read_flim_bin):
    """spatial_bin=1 produces byte-identical output to omitting the kwarg."""
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    store_a = _h5_with_intensity(tmp_path / "a", channel_names=("ch00",))
    add_decay_to_dataset(
        h5_path=store_a.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
    )
    store_b = _h5_with_intensity(tmp_path / "b", channel_names=("ch00",))
    add_decay_to_dataset(
        h5_path=store_b.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        spatial_bin=1,
    )
    with h5py.File(store_a.path, "r") as fa, h5py.File(store_b.path, "r") as fb:
        assert np.array_equal(fa["decay/ch00"][...], fb["decay/ch00"][...])


def test_add_decay_spatial_bin_k3_floor_divides_tile_dims(tmp_path, monkeypatch):
    """spatial_bin=3 reduces a 9×9 tile to 3×3; T-axis untouched."""
    def _ones_read(path, **kwargs):
        return {
            "array": np.ones((9, 9, 4), dtype=np.uint16),
            "intensity": np.zeros((9, 9), dtype=np.uint16),
            "metadata": {"shape": (9, 9, 4)},
        }
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _ones_read,
    )
    monkeypatch.setattr("percell4.adapters.readers.read_flim_bin", _ones_read)

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])
    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=9, bin_y=9, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        spatial_bin=3,
    )
    with h5py.File(store.path, "r") as f:
        decay = f["decay/ch00"][...]
        assert decay.shape == (3, 3, 4)
        # Each output pixel sums 3×3 input ones → 9
        assert np.all(decay == 9.0)
        assert f["decay/ch00"].attrs["spatial_bin"] == 3


def test_add_decay_spatial_bin_truncates_residual_pixels(tmp_path, monkeypatch):
    """spatial_bin=3 on a 10×10 tile drops the last row+col (10 % 3 = 1)."""
    def _ones_read(path, **kwargs):
        return {
            "array": np.ones((10, 10, 2), dtype=np.uint16),
            "intensity": np.zeros((10, 10), dtype=np.uint16),
            "metadata": {"shape": (10, 10, 2)},
        }
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _ones_read,
    )
    monkeypatch.setattr("percell4.adapters.readers.read_flim_bin", _ones_read)

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])
    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=10, bin_y=10, bin_t=2),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        spatial_bin=3,
    )
    with h5py.File(store.path, "r") as f:
        decay = f["decay/ch00"][...]
        # floor(10 / 3) = 3 → output shape (3, 3, 2)
        assert decay.shape == (3, 3, 2)


def test_add_decay_spatial_bin_sum_preserves_total_counts(tmp_path, monkeypatch):
    """Sum-binning preserves total photon counts (Poisson statistics)."""
    rng = np.random.default_rng(0)
    fake = rng.integers(0, 100, size=(6, 6, 3), dtype=np.uint16)

    def _patterned_read(path, **kwargs):
        return {
            "array": fake.copy(),
            "intensity": fake[..., 0].copy(),
            "metadata": {"shape": fake.shape},
        }
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _patterned_read,
    )
    monkeypatch.setattr("percell4.adapters.readers.read_flim_bin", _patterned_read)

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])
    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=6, bin_y=6, bin_t=3),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        spatial_bin=3,
    )
    with h5py.File(store.path, "r") as f:
        binned = f["decay/ch00"][...]
        assert binned.shape == (2, 2, 3)
        # Total photon counts preserved across spatial dims, per T-bin
        assert np.allclose(binned.sum(axis=(0, 1)), fake.astype(np.float64).sum(axis=(0, 1)))


def test_add_decay_spatial_bin_combined_with_tile_stitching(tmp_path, monkeypatch):
    """2×2 grid × 6×6 tiles × spatial_bin=3 → (4, 4, T) stitched output."""
    def _ones_read(path, **kwargs):
        return {
            "array": np.ones((6, 6, 2), dtype=np.uint16),
            "intensity": np.zeros((6, 6), dtype=np.uint16),
            "metadata": {"shape": (6, 6, 2)},
        }
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _ones_read,
    )
    monkeypatch.setattr("percell4.adapters.readers.read_flim_bin", _ones_read)

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, [
        "exp_s1_ch1.bin", "exp_s2_ch1.bin", "exp_s3_ch1.bin", "exp_s4_ch1.bin",
    ])
    add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=2, grid_cols=2),
        flim_config=FlimConfig(bin_x=6, bin_y=6, bin_t=2),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        spatial_bin=3,
    )
    with h5py.File(store.path, "r") as f:
        # Each 6×6 tile → 2×2 after bin; 2×2 grid → 4×4 stitched.
        assert f["decay/ch00"].shape == (4, 4, 2)


def test_add_decay_spatial_bin_too_large_reports_per_channel_error(
    tmp_path, mock_read_flim_bin,
):
    """spatial_bin larger than tile dims reports per-channel error, no write."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])
    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        spatial_bin=16,
    )
    assert report.written == ()
    assert "ch00" in report.errors
    assert "larger than tile dims" in report.errors["ch00"]


def test_add_decay_spatial_bin_invalid_raises(tmp_path, mock_read_flim_bin):
    """spatial_bin < 1 raises before any work happens."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])
    with pytest.raises(ValueError, match="spatial_bin"):
        add_decay_to_dataset(
            h5_path=store.path,
            source_dir=src,
            token_config=TokenConfig(),
            tile_config=TileConfig(grid_rows=1, grid_cols=1),
            flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
            cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
            spatial_bin=0,
        )
