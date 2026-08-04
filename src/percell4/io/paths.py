"""Path helpers that keep OS sidecar files out of data discovery.

macOS cannot store extended attributes natively on exFAT/FAT/SMB
volumes, so it writes them into an AppleDouble companion file named
``._<original name>`` right next to the real one. The companion carries
the same extension as the file it shadows, which means every
``glob("*.h5")`` / ``glob("*.parquet")`` picks it up and the reader then
chokes on what is really a 4 KB blob of metadata::

    OSError: Unable to synchronously open file (file signature not found)
    ArrowInvalid: Parquet magic bytes not found in footer

These files are never PerCell data. Route directory scans through
:func:`scan_files` (or filter explicit path lists through
:func:`drop_sidecars`) so they are dropped before anything tries to open
them.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

APPLEDOUBLE_PREFIX = "._"

# Metadata files the OS drops into data folders. They rarely collide with
# a data extension the way AppleDouble companions do, but they are never
# ours either.
_SIDECAR_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def is_sidecar(path: str | Path) -> bool:
    """True if ``path`` is an OS-generated sidecar, not real data."""
    name = Path(path).name
    return name.startswith(APPLEDOUBLE_PREFIX) or name in _SIDECAR_NAMES


def drop_sidecars(paths: Iterable[str | Path]) -> list[Path]:
    """Return ``paths`` as ``Path`` objects with sidecars removed.

    Order is preserved — use this for explicit path lists (argv, file
    dialogs, drag-and-drop) where the caller's ordering is meaningful.
    """
    return [Path(p) for p in paths if not is_sidecar(p)]


def scan_files(
    folder: str | Path, *patterns: str, recursive: bool = False
) -> list[Path]:
    """Sidecar-free matches for ``patterns`` in ``folder``, sorted.

    Matches across all patterns are merged and de-duplicated, so
    ``scan_files(d, "*.h5", "*.hdf5")`` returns one alphabetical list
    rather than one run per pattern.
    """
    base = Path(folder)
    globber = base.rglob if recursive else base.glob
    found = {p for pattern in patterns for p in globber(pattern)}
    return sorted(p for p in found if not is_sidecar(p))
