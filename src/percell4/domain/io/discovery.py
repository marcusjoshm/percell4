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
from percell4.domain.io.tokenless import build_channel_pattern, derive_channel_names

_IMAGE_EXTENSIONS = {".tif", ".tiff", ".bin"}

# A TokenConfig that parses no tokens — used to enumerate a flat folder's files
# before the channel vocabulary has been derived.
_NO_TOKENS = TokenConfig(channel=None, timepoint=None, z_slice=None, tile=None)


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

    # Check if root also has loose images alongside subdirectories
    root_tiffs = [
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    ]

    datasets: list[DatasetSpec] = []

    # Loose image files in root become their own dataset
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


def discover_tokenless(
    root: Path,
    output_dir: Path | None = None,
) -> tuple[list[DatasetSpec], TokenConfig | None]:
    """Discover datasets from a flat folder of name-suffixed TIFFs — no token.

    Derives the channel-name vocabulary structurally from the filenames (the
    trailing remainder after the shared dataset prefix; see
    ``domain/io/tokenless``), synthesizes an internal channel regex from it, and
    delegates grouping to :func:`discover_flat` with that regex so the shared
    leading prefix becomes each ``.h5`` name and the trailing name becomes the
    channel.  Multi-underscore names (``SG_mask``) stay whole.

    Returns ``(datasets, token_config)``.  The synthesized ``token_config`` is
    returned so the caller can thread the *identical* regex into
    ``import_dataset`` — discovery and the importer's re-parse then agree
    byte-for-byte.  Returns ``([], None)`` when the folder has no image files.

    Raises
    ------
    ValueError
        If the derived vocabulary is too large to encode as a token pattern
        (propagated from :func:`build_channel_pattern`); the dialog surfaces this
        rather than importing silently.
    """
    root = Path(root)
    out = output_dir or root

    # Enumerate the flat set of image files without parsing any tokens yet.
    scan = FileScanner(_NO_TOKENS).scan(path=root)
    stems = [f.path.stem for f in scan.files]
    names, _ = derive_channel_names(stems)
    if not names:
        return [], None

    pattern = build_channel_pattern(names)
    token_config = TokenConfig(
        channel=pattern, timepoint=None, z_slice=None, tile=None
    )
    datasets = discover_flat(root, token_config, out)
    return datasets, token_config


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
