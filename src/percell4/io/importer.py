"""Import pipeline: TIFF directory → HDF5 dataset.

Orchestrates scanning, assembly, and writing. Writes HDF5 first,
then updates project.csv (orphan .h5 files are harmless; orphan
CSV rows are confusing).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from percell4.io.assembler import assemble_channels, assemble_tiles, project_z
from percell4.io.models import ScanResult, TileConfig, TokenConfig
from percell4.io.readers import read_tiff
from percell4.io.scanner import FileScanner
from percell4.project import ProjectIndex
from percell4.store import DatasetStore


def import_dataset(
    source_dir: str | Path,
    output_h5: str | Path,
    token_config: TokenConfig | None = None,
    tile_config: TileConfig | None = None,
    project_csv: str | Path | None = None,
    z_project_method: str | None = "mip",
    metadata: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> int:
    """Import a directory of TIFFs into a single .h5 dataset.

    Parameters
    ----------
    source_dir : directory containing TIFF files
    output_h5 : path for the output .h5 file
    token_config : regex patterns for filename tokens
    tile_config : tile stitching grid configuration (None = no stitching)
    project_csv : path to project.csv (None = don't update)
    z_project_method : 'mip', 'mean', 'sum', or None to keep full z-stack
    metadata : additional metadata to store in .h5
    progress_callback : fn(current, total, message) for GUI progress

    Returns
    -------
    Number of channels imported.
    """
    source_dir = Path(source_dir)
    output_h5 = Path(output_h5)

    def _progress(current: int, total: int, msg: str) -> None:
        if progress_callback is not None:
            progress_callback(current, total, msg)

    # 1. Scan directory
    _progress(0, 4, "Scanning files...")
    scanner = FileScanner(token_config)
    result = scanner.scan(path=source_dir)

    if not result.files:
        raise ValueError(f"No TIFF files found in {source_dir}")

    # 2. Group files by channel and z-slice
    _progress(1, 4, "Organizing files...")
    channel_groups = _group_by_channel(result)

    # 3. Assemble each channel
    _progress(2, 4, "Assembling images...")
    channel_images: list[np.ndarray] = []
    channel_names: list[str] = []

    for ch_key in sorted(channel_groups.keys()):
        files = channel_groups[ch_key]
        channel_names.append(f"ch{ch_key}" if ch_key else "ch0")

        # Group by z-slice within this channel
        z_groups = _group_by_z(files)

        if len(z_groups) > 1 and z_project_method is not None:
            # Multiple z-slices: load and project
            z_images = []
            for z_key in sorted(z_groups.keys()):
                z_file = z_groups[z_key]
                img = _load_and_stitch(z_file, tile_config)
                z_images.append(img)
            channel_img = project_z(z_images, method=z_project_method)
        else:
            # Single z or keep full stack
            all_files = []
            for z_key in sorted(z_groups.keys()):
                all_files.extend(z_groups[z_key])
            channel_img = _load_and_stitch(all_files, tile_config)

        channel_images.append(channel_img.astype(np.float32))

    # 4. Write to HDF5 (before updating CSV!)
    _progress(3, 4, "Writing HDF5...")
    store = DatasetStore(output_h5)

    all_metadata = {
        "source_dir": str(source_dir),
        "channel_names": channel_names,
        "n_channels": len(channel_images),
    }
    if metadata:
        all_metadata.update(metadata)

    store.create(metadata=all_metadata)

    if len(channel_images) == 1:
        dims = ["H", "W"]
        store.write_array("intensity", channel_images[0], attrs={"dims": dims})
    else:
        intensity = assemble_channels(channel_images)
        dims = ["C", "H", "W"]
        store.write_array("intensity", intensity, attrs={"dims": dims})

    # 5. Update project.csv
    if project_csv is not None:
        idx = ProjectIndex(project_csv)
        if not idx.exists():
            idx.create()
        idx.add_dataset(str(output_h5), status="complete")

    _progress(4, 4, "Import complete")
    return len(channel_images)


def _group_by_channel(result: ScanResult) -> dict[str, list]:
    """Group discovered files by channel token."""
    groups: dict[str, list] = defaultdict(list)
    for f in result.files:
        ch = f.tokens.get("channel", "")
        groups[ch].append(f)
    return dict(groups)


def _group_by_z(files: list) -> dict[str, list]:
    """Group files by z-slice token."""
    groups: dict[str, list] = defaultdict(list)
    for f in files:
        z = f.tokens.get("z_slice", "")
        groups[z].append(f)
    return dict(groups)


def _load_and_stitch(files: list, tile_config: TileConfig | None) -> np.ndarray:
    """Load files and optionally stitch tiles."""
    if len(files) == 1:
        data = read_tiff(str(files[0].path))
        return data["array"]

    if tile_config is not None and tile_config.grid_rows * tile_config.grid_cols > 1:
        # Stitch tiles
        tiles: dict[int, np.ndarray] = {}
        for f in files:
            tile_idx = int(f.tokens.get("tile", "0"))
            data = read_tiff(str(f.path))
            tiles[tile_idx] = data["array"]

        return assemble_tiles(
            tiles,
            grid_rows=tile_config.grid_rows,
            grid_cols=tile_config.grid_cols,
            grid_type=tile_config.grid_type,
            order=tile_config.order,
        )

    # Multiple files but no tile config — just return the first
    # (could be multiple z-slices grouped together)
    data = read_tiff(str(files[0].path))
    return data["array"]
