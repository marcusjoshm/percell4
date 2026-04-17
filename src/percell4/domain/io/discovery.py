"""Dataset discovery for batch compress.

Identifies datasets from a root directory using either subdirectory-based
or token-based grouping, returning a list of DatasetSpec objects.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from percell4.domain.io.models import DatasetSpec, DiscoveredFile, ScanResult, TokenConfig
from percell4.domain.io.scanner import FileScanner

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


def discover_flat(
    root: Path,
    token_config: TokenConfig | None = None,
    output_dir: Path | None = None,
) -> list[DatasetSpec]:
    """Discover datasets in a flat directory by stripping known tokens.

    Scans all TIFFs in *root* (non-recursive), strips the known token
    matches (channel, tile, z-slice, timepoint) from each filename, and
    groups files by the remaining stem.  This is the PerCell3-style FOV
    derivation approach.

    Example::

        1hr_Ars_1A_Capture_s00_ch00.tif  →  strip _s00, _ch00
        1hr_Ars_1A_Capture_s00_ch01.tif  →  strip _s00, _ch01
        1hr_Ars_1B_Capture_s00_ch00.tif  →  strip _s00, _ch00

        Groups: "1hr_Ars_1A_Capture" and "1hr_Ars_1B_Capture"
    """
    root = Path(root)
    out = output_dir or root
    config = token_config or TokenConfig()

    scanner = FileScanner(config)
    scan = scanner.scan(path=root)

    groups: dict[str, list[DiscoveredFile]] = defaultdict(list)
    for f in scan.files:
        dataset_name = _derive_dataset_name(f.path.stem, config)
        groups[dataset_name].append(f)

    datasets: list[DatasetSpec] = []
    for name in sorted(groups):
        files = groups[name]
        # Build a ScanResult for this group
        sr = ScanResult(files=files)
        for f in files:
            if "channel" in f.tokens:
                sr.channels.add(f.tokens["channel"])
            if "tile" in f.tokens:
                sr.tiles.add(f.tokens["tile"])
            if "z_slice" in f.tokens:
                sr.z_slices.add(f.tokens["z_slice"])
            if "timepoint" in f.tokens:
                sr.timepoints.add(f.tokens["timepoint"])
        datasets.append(
            DatasetSpec(
                name=name,
                source_dir=root,
                files=tuple(files),
                output_path=out / f"{name}.h5",
                scan_result=sr,
            )
        )

    return datasets


def _derive_dataset_name(stem: str, config: TokenConfig) -> str:
    """Derive dataset name by stripping all matched token patterns from stem.

    Strips the full match (not just the capture group) of each token pattern,
    then cleans up trailing underscores/hyphens.
    """
    result = stem
    for field_name in ("channel", "timepoint", "z_slice", "tile"):
        pattern = getattr(config, field_name)
        if pattern is None:
            continue
        result = re.sub(pattern, "", result)

    # Clean up trailing/leading separators left after stripping
    result = result.strip("_- ")
    # Collapse runs of underscores
    result = re.sub(r"_{2,}", "_", result)
    return result or stem  # fallback to original if everything was stripped


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
