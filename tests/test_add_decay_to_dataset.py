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
    """Replace read_flim_bin with a deterministic synthetic reader."""
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
