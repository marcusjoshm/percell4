"""Tests for tokenless dataset discovery."""

from __future__ import annotations

import pytest

from percell4.domain.io.discovery import discover_tokenless

_PREFIXES = [
    "CellProfiler_U2OS_60min_As_3x4",
    "CellProfiler_U2OS_90min_Washout_2",
    "CellProfiler_U2OS_90min_Washout_4x4",
]
_CHANNELS = ["cells", "DNA", "G3BP1", "SG_mask"]


@pytest.fixture
def flat_named_dir(tmp_path):
    """Flat folder: 3 datasets x 4 name-suffixed channels (incl. SG_mask)."""
    for p in _PREFIXES:
        for c in _CHANNELS:
            (tmp_path / f"{p}_{c}.tif").write_bytes(b"fake tiff")
    return tmp_path


def test_discovers_three_datasets(flat_named_dir):
    datasets, token_config = discover_tokenless(flat_named_dir)
    assert token_config is not None
    assert {ds.name for ds in datasets} == set(_PREFIXES)


def test_each_dataset_has_all_four_channels(flat_named_dir):
    datasets, _ = discover_tokenless(flat_named_dir)
    for ds in datasets:
        assert ds.scan_result is not None
        assert ds.scan_result.channels == {"cells", "DNA", "G3BP1", "SG_mask"}


def test_sg_mask_grouped_with_siblings_not_orphaned(flat_named_dir):
    """The SG_mask files land in their sibling dataset, channel token 'SG_mask'."""
    datasets, _ = discover_tokenless(flat_named_dir)
    by_name = {ds.name: ds for ds in datasets}
    ds = by_name["CellProfiler_U2OS_60min_As_3x4"]
    # No orphan '..._SG' dataset was created.
    assert "CellProfiler_U2OS_60min_As_3x4_SG" not in by_name
    channels = {tok for f in ds.files for tok in [f.tokens.get("channel")]}
    assert "SG_mask" in channels


def test_files_are_scoped_per_dataset(flat_named_dir):
    """Each DatasetSpec carries only its own 4 files (guards N-identical-.h5)."""
    datasets, _ = discover_tokenless(flat_named_dir)
    for ds in datasets:
        assert len(ds.files) == 4
        assert all(ds.name in str(f.path) for f in ds.files)


def test_output_paths_are_prefix_named(flat_named_dir, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    datasets, _ = discover_tokenless(flat_named_dir, output_dir=out)
    names = {ds.output_path.name for ds in datasets}
    assert names == {f"{p}.h5" for p in _PREFIXES}


def test_synthesized_config_reparse_matches_discovery(flat_named_dir):
    """The returned token_config, applied by a fresh scan, reproduces the same
    channel tokens the importer will see (discovery <-> importer parity)."""
    from percell4.domain.io.scanner import FileScanner

    datasets, token_config = discover_tokenless(flat_named_dir)
    scanner = FileScanner(token_config)
    for ds in datasets:
        rescan = scanner.scan(files=[str(f.path) for f in ds.files])
        assert rescan.channels == {"cells", "DNA", "G3BP1", "SG_mask"}


def test_empty_folder_returns_empty(tmp_path):
    datasets, token_config = discover_tokenless(tmp_path)
    assert datasets == []
    assert token_config is None


def test_single_dataset_single_channel(tmp_path):
    (tmp_path / "Exp_A_DNA.tif").write_bytes(b"fake tiff")
    datasets, token_config = discover_tokenless(tmp_path)
    assert len(datasets) == 1
    assert datasets[0].name == "Exp_A"
    assert datasets[0].scan_result.channels == {"DNA"}
