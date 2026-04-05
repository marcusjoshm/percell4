"""Dataset discovery for batch compress.

Identifies datasets from a root directory using either subdirectory-based
or token-based grouping, returning a list of DatasetSpec objects.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from percell4.io.models import DatasetSpec, DiscoveredFile, TokenConfig
from percell4.io.scanner import FileScanner

_TIFF_EXTENSIONS = {".tif", ".tiff"}


def discover_by_subdirectory(
    root: Path,
    token_config: TokenConfig | None = None,
    output_dir: Path | None = None,
) -> list[DatasetSpec]:
    """Discover datasets where each immediate subdirectory is one dataset.

    If root itself contains TIFFs with no subdirectories, it is treated as
    a single dataset.  Subdirectories that contain no TIFFs are skipped.
    """
    root = Path(root)
    scanner = FileScanner(token_config)
    out = output_dir or root

    # Check for immediate child directories
    child_dirs = sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
    )

    # If no child dirs, treat root as single dataset
    if not child_dirs:
        return _scan_single(root, scanner, out)

    # Check if root also has loose TIFFs alongside subdirectories
    root_tiffs = [
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in _TIFF_EXTENSIONS
    ]

    datasets: list[DatasetSpec] = []

    # Loose TIFFs in root become their own dataset
    if root_tiffs:
        scan = scanner.scan(files=[str(f) for f in root_tiffs])
        if scan.files:
            datasets.append(
                DatasetSpec(
                    name=root.name,
                    source_dir=root,
                    files=tuple(scan.files),
                    output_path=out / f"{root.name}.h5",
                    scan_result=scan,
                )
            )

    # Each child directory is a dataset
    for child in child_dirs:
        scan = scanner.scan(path=child)
        if not scan.files:
            continue
        datasets.append(
            DatasetSpec(
                name=child.name,
                source_dir=child,
                files=tuple(scan.files),
                output_path=out / f"{child.name}.h5",
                scan_result=scan,
            )
        )

    return datasets


def discover_by_token(
    root: Path,
    group_token: str,
    token_config: TokenConfig | None = None,
    output_dir: Path | None = None,
) -> list[DatasetSpec]:
    """Discover datasets by grouping files in a flat directory by a regex token.

    The *group_token* regex must have one capture group. Files whose names
    don't match the pattern are collected into a group named ``_ungrouped``.
    """
    root = Path(root)
    out = output_dir or root

    compiled = re.compile(group_token)
    if compiled.groups < 1:
        raise ValueError(
            f"group_token {group_token!r} must have at least one capture group"
        )

    scanner = FileScanner(token_config)
    scan = scanner.scan(path=root)

    groups: dict[str, list[DiscoveredFile]] = defaultdict(list)
    for f in scan.files:
        match = re.search(compiled, f.path.stem)
        group_name = match.group(1) if match else "_ungrouped"
        groups[group_name].append(f)

    datasets: list[DatasetSpec] = []
    for name in sorted(groups):
        files = groups[name]
        datasets.append(
            DatasetSpec(
                name=name,
                source_dir=None,
                files=tuple(files),
                output_path=out / f"{name}.h5",
            )
        )

    return datasets


def _scan_single(
    root: Path, scanner: FileScanner, out: Path
) -> list[DatasetSpec]:
    """Scan root as a single dataset."""
    scan = scanner.scan(path=root)
    if not scan.files:
        return []
    return [
        DatasetSpec(
            name=root.name,
            source_dir=root,
            files=tuple(scan.files),
            output_path=out / f"{root.name}.h5",
            scan_result=scan,
        )
    ]
