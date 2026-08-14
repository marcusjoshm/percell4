"""AppleDouble sidecars must never reach a data reader.

macOS writes ``._<name>`` companions next to every file carrying
extended attributes on exFAT/FAT/SMB volumes. They share the data
extension, so an unfiltered ``glob("*.h5")`` hands h5py a 4 KB xattr
blob and the open dies with "file signature not found".
"""

from __future__ import annotations

from pathlib import Path

from percell4.io.paths import drop_sidecars, is_sidecar, scan_files


def test_is_sidecar_flags_appledouble_companions() -> None:
    assert is_sidecar("._dish_1.h5")
    assert is_sidecar(Path("/data/exfat/._dish_1.parquet"))
    assert is_sidecar("/data/.DS_Store")


def test_is_sidecar_passes_real_data_files() -> None:
    assert not is_sidecar("dish_1.h5")
    assert not is_sidecar(Path("/data/PerCell_U2OS_60min_As_3x4.parquet"))
    # A leading dot alone is not the AppleDouble marker.
    assert not is_sidecar(".hidden.h5")


def test_scan_files_skips_sidecars(tmp_path: Path) -> None:
    (tmp_path / "dish_1.h5").write_bytes(b"data")
    (tmp_path / "dish_2.hdf5").write_bytes(b"data")
    (tmp_path / "._dish_1.h5").write_bytes(b"xattr blob")
    (tmp_path / "._dish_2.hdf5").write_bytes(b"xattr blob")

    found = scan_files(tmp_path, "*.h5", "*.hdf5")

    assert [p.name for p in found] == ["dish_1.h5", "dish_2.hdf5"]


def test_scan_files_dedupes_overlapping_patterns(tmp_path: Path) -> None:
    (tmp_path / "a.h5").write_bytes(b"data")

    found = scan_files(tmp_path, "*.h5", "*.h5", "a.*")

    assert [p.name for p in found] == ["a.h5"]


def test_scan_files_recursive_reaches_subfolders(tmp_path: Path) -> None:
    nested = tmp_path / "group_a"
    nested.mkdir()
    (nested / "field.bin").write_bytes(b"data")
    (nested / "._field.bin").write_bytes(b"xattr blob")

    found = scan_files(tmp_path, "*.bin", recursive=True)

    assert [p.name for p in found] == ["field.bin"]


def test_scan_files_on_missing_folder_returns_empty(tmp_path: Path) -> None:
    assert scan_files(tmp_path / "nope", "*.h5") == []


def test_drop_sidecars_preserves_caller_order() -> None:
    given = ["/d/z.h5", "/d/._z.h5", "/d/a.h5"]

    assert drop_sidecars(given) == [Path("/d/z.h5"), Path("/d/a.h5")]
