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
from percell4.store import DatasetStore, LayerSizeMismatchError


def _h5_with_intensity(tmp_path, channel_names=("ch00", "ch01"), shape=(8, 8)):
    """Create an .h5 with /intensity (3D C,H,W) + channel_names metadata.

    Default ``shape`` matches the ``mock_read_flim_bin`` fake reader's
    (8, 8) tile so the use case's native-shape validation passes for the
    common 1x1-tile case. Pass a different ``shape`` for tile-stitching
    tests (e.g. 2x2 stitching of 8x8 tiles = (16, 16)).
    """
    store = DatasetStore(tmp_path / "experiment.h5")
    store.create(metadata={"channel_names": list(channel_names)})
    intensity = np.zeros((len(channel_names), *shape), dtype=np.uint16)
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
    # 2x2 grid of 8x8 tiles -> 16x16 stitched; native must match.
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(16, 16))
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

    # Without rotation: shape stays (H=4, W=8, T=2). Native must match
    # the pre-rotation stitched shape (4, 8).
    store0 = _h5_with_intensity(
        tmp_path / "norot", channel_names=("ch00",), shape=(4, 8)
    )
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

    # With rotate_k=1 (90° CCW): (H=4, W=8, T=2) → (W=8, H=4, T=2).
    # Validation fires on the POST-rotation shape, so native_shape must
    # match the final on-disk (8, 4) output.
    store1 = _h5_with_intensity(
        tmp_path / "rot", channel_names=("ch00",), shape=(8, 4)
    )
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
    # 8x8 matches mock_read_flim_bin's tile shape so native-shape
    # validation passes.
    intensity = np.zeros((3, 8, 8), dtype=np.uint16)
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

    # Patterned mock returns (4, 8, 2); native must match (4, 8).
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(4, 8))
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

    # Patterned mock returns (4, 8, 2); native must match (4, 8).
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(4, 8))
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
    # 2x2 grid of 8x8 tiles -> 16x16 stitched.
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(16, 16))
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


# ── Native-shape validation (U6) ────────────────────────────────────────


def test_add_decay_native_shape_mismatch_reports_error(tmp_path, mock_read_flim_bin):
    """A .bin stitched shape that disagrees with /metadata.native_shape
    surfaces as a per-channel LayerSizeMismatchError in the report."""
    # Native = 16x16 but .bin tiles produce 8x8 stitched (1x1 grid of 8x8).
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(16, 16))
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

    # Per-channel-error path (the use case never raises on per-channel
    # failures -- it collects them).
    assert report.written == ()
    assert "ch00" in report.errors
    assert "native_shape" in report.errors["ch00"]
    # No decay was written.
    with h5py.File(store.path, "r") as f:
        assert "decay" not in f


def test_add_decay_native_shape_match_with_rotate_k1_transpose(tmp_path, monkeypatch):
    """rotate_k=1 transposes (H, W) post-stitch; the validation must
    compare against the POST-rotation shape, not the pre-rotation shape.

    Regression: prior to this fix, U6's native-shape validation fired
    against (out_h, out_w) before Phase 2 rotation ran. A user whose
    .bin tiles stitched to (2048, 2560) and TIFF intensity at (2560, 2048)
    with rotate_k=1 to align was rejected even though the post-rotation
    decay would have matched native_shape exactly.
    """
    # Patterned reader returns non-square (4, 8, 2) tiles so the stitch
    # is intentionally not square — exercises the transpose code path.
    def _patterned_read(path, **kwargs):
        h, w, t = 4, 8, 2
        arr = np.zeros((h, w, t), dtype=np.uint16)
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

    # 1x1 grid of (4, 8) -> stitched (4, 8). rotate_k=1 (90° CCW) -> (8, 4).
    # native_shape declares the POST-rotation orientation: (8, 4).
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(8, 4))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=1),
        flim_config=FlimConfig(bin_x=8, bin_y=4, bin_t=2),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        rotate_k=1,
    )

    assert report.written == ("ch00",)
    assert report.errors == {}
    # Final stored shape matches native_shape after rotation.
    with h5py.File(store.path, "r") as f:
        assert f["decay/ch00"].shape == (8, 4, 2)


def test_add_decay_native_shape_match_succeeds(tmp_path, mock_read_flim_bin):
    """The same flow with matching native_shape lands as before."""
    # Native = (8, 8) matches the mock .bin tile shape.
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(8, 8))
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
    assert report.errors == {}


# ── Registered-geometry append (U7) ─────────────────────────────────────


def _tile_id_reader():
    """A patterned .bin reader that embeds the tile id (_s<idx>) into the
    array so placement can be verified pixel-for-pixel.

    Returns 8x8x2 tiles whose every pixel equals ``idx + 1`` (so tile 0 = 1,
    tile 1 = 2, ...). Distinct constant values make the overlap-winner check
    unambiguous.
    """
    import re as _re

    def _read(path, **kwargs):
        m = _re.search(r"_s(\d+)", str(path))
        idx = int(m.group(1)) if m else 0
        arr = np.full((8, 8, 2), idx + 1, dtype=np.uint16)
        return {
            "array": arr,
            "intensity": arr[..., 0].copy(),
            "metadata": {"shape": (8, 8, 2)},
        }

    return _read


def _write_registered_geometry(store, offsets, *, reference_channel="ch00",
                               overlap=0.25, disconnected=()):
    """Persist registered overlap-stitch geometry via the store boundary."""
    from percell4.domain.io.models import StitchProvenanceRecord

    prov = StitchProvenanceRecord(
        reference_channel=reference_channel,
        overlap=str(overlap),
        library="grid_stitching@test",
        quality_json="{}",
        n_tiles=str(len(offsets)),
        importer_version="test",
        timestamp_utc="2026-06-24T00:00:00+00:00",
    )
    store.write_stitch_geometry(
        np.asarray(offsets, dtype=np.int32),
        prov,
        reference_channel=reference_channel,
        overlap=overlap,
        disconnected=disconnected,
    )


def test_add_decay_registered_reuses_persisted_offsets(tmp_path, monkeypatch):
    """AE2: append to a dataset with persisted registered geometry places
    decay tiles at the persisted (y0, x0); /decay (H, W) == native_shape;
    registration is NEVER recomputed."""
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _tile_id_reader(),
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _tile_id_reader(),
    )

    # Guard: estimate_tile_offsets must NOT be called on the append path.
    def _no_register(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("registration must not run on decay append")

    monkeypatch.setattr(
        "percell4.domain.io.assembler.estimate_tile_offsets", _no_register
    )

    # 2-tile horizontal mosaic: 8x8 tiles, tile 1 shifted right by 6 px
    # (2 px overlap). canvas_from_offsets -> (8, 6 + 8) = (8, 14).
    offsets = [(0, 0), (0, 6)]
    from percell4.domain.io.assembler import canvas_from_offsets

    native = canvas_from_offsets(np.asarray(offsets), (8, 8))
    assert native == (8, 14)

    store = _h5_with_intensity(
        tmp_path, channel_names=("ch00",), shape=native
    )
    _write_registered_geometry(store, offsets)

    src = tmp_path / "bin"
    # _s0 -> tile 0 (value 1), _s1 -> tile 1 (value 2); offset=0 so token
    # "00" maps to "ch00".
    _make_bin_files(src, ["exp_s0_ch00.bin", "exp_s1_ch00.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=2),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=2,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
    )

    assert report.written == ("ch00",), report.errors
    assert report.errors == {}

    with h5py.File(store.path, "r") as f:
        decay = f["decay/ch00"][...]
    # Final (H, W) equals native_shape == canvas_from_offsets.
    assert decay.shape == (8, 14, 2)
    # Tile 0 (value 1) at columns 0-5 (its non-overlapped region).
    assert (decay[:, 0:6, 0] == 1).all()
    # Tile 1 (value 2) at columns 6-13.
    assert (decay[:, 6:14, 0] == 2).all()
    # Overlap columns 6-7: tile 1 (higher index) wins.
    assert (decay[:, 6:8, 0] == 2).all()


def test_add_decay_registered_offsets_absent_raises(tmp_path, mock_read_flim_bin,
                                                    monkeypatch):
    """AE4: a dataset flagged stitch_registered=True but with tile_offsets
    absent → the append RAISES (no silent grid fallback)."""
    from percell4.store import StitchGeometry

    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(8, 8))
    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    # Simulate the registered-but-offsets-absent corruption by patching the
    # store's read_stitch_geometry to report registered=True / offsets=None.
    monkeypatch.setattr(
        "percell4.store.DatasetStore.read_stitch_geometry",
        lambda self: StitchGeometry(
            registered=True, offsets=None,
            reference_channel="ch00", overlap=0.25,
        ),
    )

    with pytest.raises(ValueError, match="stitch_registered"):
        add_decay_to_dataset(
            h5_path=store.path,
            source_dir=src,
            token_config=TokenConfig(),
            tile_config=TileConfig(grid_rows=1, grid_cols=1),
            flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
            cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        )

    # No decay was written.
    with h5py.File(store.path, "r") as f:
        assert "decay" not in f


def test_add_decay_non_registered_uses_grid_path(tmp_path, mock_read_flim_bin):
    """Back-compat: a dataset with NO registered geometry runs the unchanged
    grid placement path (read_stitch_geometry reports registered=False)."""
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(16, 16))
    # No stitch geometry written → read_stitch_geometry().registered is False.
    assert store.read_stitch_geometry().registered is False

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
    )

    assert report.written == ("ch00",)
    with h5py.File(store.path, "r") as f:
        # 2x2 grid of 8x8 tiles → 16x16 stitched (edge-to-edge grid).
        assert f["decay/ch00"].shape == (16, 16, 4)


def test_add_decay_registered_rotate_k_odd_guard_transpose(tmp_path, monkeypatch):
    """rotate_k odd on a registered dataset: the native_shape guard derives
    out_h/out_w from canvas_from_offsets and still transposes for the odd
    rotation, so a correctly-declared transposed native_shape matches."""
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _tile_id_reader(),
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _tile_id_reader(),
    )

    # 2-tile horizontal mosaic → registered canvas (8, 14). rotate_k=1
    # (90° CCW) transposes to (14, 8): native_shape declares the POST-rotation
    # orientation.
    offsets = [(0, 0), (0, 6)]
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(14, 8))
    _write_registered_geometry(store, offsets)

    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch00.bin", "exp_s1_ch00.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=2),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=2,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
        rotate_k=1,
    )

    assert report.written == ("ch00",), report.errors
    assert report.errors == {}
    with h5py.File(store.path, "r") as f:
        # Pre-rotation registered canvas (8, 14) → post-rotation (14, 8).
        assert f["decay/ch00"].shape == (14, 8, 2)


# ── FIX C: registered append completeness + disconnected winner ──────────


def test_add_decay_registered_missing_tile_raises(tmp_path, monkeypatch):
    """FIX C: a registered append that omits a registered tile is reported as
    an error — partial registered decay is unsupported (it would leave the
    missing tile's region empty and mis-resolve overlaps).
    """
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _tile_id_reader(),
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _tile_id_reader(),
    )

    # 2-tile registered mosaic, canvas (8, 14) — but supply only tile 0.
    offsets = [(0, 0), (0, 6)]
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(8, 14))
    _write_registered_geometry(store, offsets)

    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch00.bin"])  # tile 1 (_s1) deliberately absent

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=2),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=2,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
    )

    # The per-channel error reports the missing tile; nothing was written.
    assert report.written == ()
    assert "ch00" in report.errors
    assert "requires all 2 tiles" in report.errors["ch00"]
    with h5py.File(store.path, "r") as f:
        assert "decay" not in f


def test_add_decay_registered_disconnected_winner(tmp_path, monkeypatch):
    """FIX C: a persisted disconnected tile is demoted (placed first) on the
    append, so its registered neighbour wins the overlap even though the
    disconnected tile has the higher index — reproducing the import exactly.
    """
    monkeypatch.setattr(
        "percell4.application.use_cases.add_decay_to_dataset.read_flim_bin",
        _tile_id_reader(),
    )
    monkeypatch.setattr(
        "percell4.adapters.readers.read_flim_bin",
        _tile_id_reader(),
    )

    # 2-tile horizontal mosaic, canvas (8, 14); tile 1 (higher index) is
    # disconnected, so the overlap must go to registered tile 0.
    offsets = [(0, 0), (0, 6)]
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(8, 14))
    _write_registered_geometry(store, offsets, disconnected=(1,))

    # Sanity: the persisted disconnected set round-trips.
    assert store.read_stitch_geometry().disconnected == (1,)

    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch00.bin", "exp_s1_ch00.bin"])

    report = add_decay_to_dataset(
        h5_path=store.path,
        source_dir=src,
        token_config=TokenConfig(),
        tile_config=TileConfig(grid_rows=1, grid_cols=2),
        flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=2,
                               bin_dtype="uint16", bin_dim_order="YXT"),
        cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=0),
    )

    assert report.written == ("ch00",), report.errors
    with h5py.File(store.path, "r") as f:
        decay = f["decay/ch00"][...]
    assert decay.shape == (8, 14, 2)
    # Overlap columns 6-7: registered tile 0 (value 1) wins because the
    # disconnected tile 1 was placed FIRST (lowest priority).
    assert (decay[:, 6:8, 0] == 1).all()
    # Tile 1's exclusive region 8-13 still shows its own value (2).
    assert (decay[:, 8:14, 0] == 2).all()


# ── FIX F: corrupt-state guards (both states raise) ──────────────────────


def test_add_decay_offsets_present_flag_absent_raises(tmp_path, mock_read_flim_bin):
    """FIX F: stitch/tile_offsets present but stitch_registered absent (a
    partial/aborted registered import) raises before the per-channel loop.
    """
    store = _h5_with_intensity(tmp_path, channel_names=("ch00",), shape=(8, 8))
    # Write the offset array WITHOUT the commit marker — the aborted state.
    store.write_array(
        "stitch/tile_offsets",
        np.array([[0, 0], [0, 6]], dtype=np.int32),
    )
    geom = store.read_stitch_geometry()
    assert geom.offsets is not None and geom.registered is False

    src = tmp_path / "bin"
    _make_bin_files(src, ["exp_s0_ch1.bin"])

    with pytest.raises(ValueError, match="stitch_registered is False"):
        add_decay_to_dataset(
            h5_path=store.path,
            source_dir=src,
            token_config=TokenConfig(),
            tile_config=TileConfig(grid_rows=1, grid_cols=1),
            flim_config=FlimConfig(bin_x=8, bin_y=8, bin_t=4),
            cross_format_rule=ZeroPadOffsetRule(pad_width=2, offset=1),
        )

    # Nothing was written.
    with h5py.File(store.path, "r") as f:
        assert "decay" not in f

