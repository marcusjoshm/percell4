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
    flim_params: dict[str, Any] | None = None,
    selected_channels: set[str] | None = None,
    layer_assignments: dict[str, Any] | None = None,
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
    flim_params : FLIM parameters dict with keys:
        'frequency_mhz', 'calibration_phase', 'calibration_modulation',
        'bin_dimensions' (for .bin files: x_dim, y_dim, t_dim, dim_order)

    Returns
    -------
    Number of channels imported.
    """
    source_dir = Path(source_dir)
    output_h5 = Path(output_h5)

    def _progress(current: int, total: int, msg: str) -> None:
        if progress_callback is not None:
            progress_callback(current, total, msg)

    # 1. Scan directory — find TIFFs and .bin files
    _progress(0, 5, "Scanning files...")
    scanner = FileScanner(token_config)
    result = scanner.scan(path=source_dir)

    # Also check for .bin TCSPC files
    bin_files = sorted(source_dir.glob("*.bin"))

    if not result.files and not bin_files:
        raise ValueError(f"No image files found in {source_dir}")

    # Auto-enable FLIM if .bin files found and no flim_params provided
    if bin_files and flim_params is None:
        flim_params = {
            "frequency_mhz": 80.0,
            "calibration_phase": 0.0,
            "calibration_modulation": 1.0,
            "bin_dimensions": {
                "x_dim": 512, "y_dim": 512, "t_dim": 132,
                "dtype": "uint32", "dim_order": "YXT", "header_bytes": 0,
            },
        }

    # 2. Separate TCSPC files from intensity files
    _progress(1, 5, "Organizing files...")
    tcspc_files = []
    intensity_files = []
    for f in result.files:
        stem = f.path.stem.upper()
        if "TCSPC" in stem:
            tcspc_files.append(f)
        else:
            intensity_files.append(f)

    # Build channel groups from intensity files
    intensity_result = ScanResult(files=intensity_files)
    for f in intensity_files:
        if "channel" in f.tokens:
            intensity_result.channels.add(f.tokens["channel"])
        if "tile" in f.tokens:
            intensity_result.tiles.add(f.tokens["tile"])
        if "z_slice" in f.tokens:
            intensity_result.z_slices.add(f.tokens["z_slice"])

    channel_groups = _group_by_channel(intensity_result) if intensity_files else {}

    # Filter to selected channels if specified
    if selected_channels is not None:
        channel_groups = {
            k: v for k, v in channel_groups.items() if k in selected_channels
        }

    # 3. Assemble channels (intensity, labels, masks based on layer_assignments)
    _progress(2, 5, "Assembling images...")
    channel_images: list[np.ndarray] = []
    channel_names: list[str] = []
    label_layers: list[tuple[str, np.ndarray]] = []  # (name, array)
    mask_layers: list[tuple[str, np.ndarray]] = []   # (name, array)

    for ch_key in sorted(channel_groups.keys()):
        files = channel_groups[ch_key]
        default_name = f"ch{ch_key}" if ch_key else "ch0"

        z_groups = _group_by_z(files)

        if len(z_groups) > 1 and z_project_method is not None:
            z_images = []
            for z_key in sorted(z_groups.keys()):
                z_file = z_groups[z_key]
                img = _load_and_stitch(z_file, tile_config)
                z_images.append(img)
            channel_img = project_z(z_images, method=z_project_method)
        else:
            all_files = []
            for z_key in sorted(z_groups.keys()):
                all_files.extend(z_groups[z_key])
            channel_img = _load_and_stitch(all_files, tile_config)

        # Route to correct layer type based on assignment
        assignment = layer_assignments.get(ch_key) if layer_assignments else None
        if assignment is not None:
            layer_type = getattr(assignment, "layer_type", "channel")
            layer_name = getattr(assignment, "name", "") or default_name
        else:
            layer_type = "channel"
            layer_name = default_name

        if layer_type == "segmentation":
            label_layers.append((layer_name, channel_img))
        elif layer_type == "mask":
            mask_layers.append((layer_name, channel_img))
        else:
            channel_names.append(layer_name)
            channel_images.append(channel_img.astype(np.float32))

    # 4. Handle TCSPC data (FLIM)
    _progress(3, 5, "Processing TCSPC data...")
    tcspc_data: dict[str, np.ndarray] = {}  # channel -> (H, W, T) array

    if flim_params and tcspc_files:
        # TCSPC TIFFs with "TCSPC" token — stitch same as intensity
        tcspc_result = ScanResult(files=tcspc_files)
        for f in tcspc_files:
            if "channel" in f.tokens:
                tcspc_result.channels.add(f.tokens["channel"])
        tcspc_channel_groups = _group_by_channel(tcspc_result)

        for ch_key in sorted(tcspc_channel_groups.keys()):
            ch_name = f"ch{ch_key}" if ch_key else "ch0"
            files = tcspc_channel_groups[ch_key]
            # Load and stitch TCSPC tiles
            decay = _load_and_stitch(files, tile_config)
            tcspc_data[ch_name] = decay

    if flim_params and bin_files:
        # .bin files — parse tokens from filenames, stitch tiles, create intensity
        import re

        from percell4.io.readers import read_flim_bin

        bin_dims = flim_params.get("bin_dimensions", {})
        config = token_config or TokenConfig()

        # Parse tokens from .bin filenames (same patterns as TIFFs)
        bin_by_channel: dict[str, dict[int, Path]] = defaultdict(dict)
        for bin_path in bin_files:
            stem = bin_path.stem
            # Extract channel token
            ch = ""
            if config.channel:
                m = re.search(config.channel, stem)
                if m:
                    ch = m.group(1)
            # Extract tile token
            tile_idx = 0
            if config.tile:
                m = re.search(config.tile, stem)
                if m:
                    tile_idx = int(m.group(1))
            bin_by_channel[ch][tile_idx] = bin_path

        # Convert tile indices to 0-based (filenames may use 1-based: _s1, _s2, ...)
        for ch_key in bin_by_channel:
            tile_dict = bin_by_channel[ch_key]
            if tile_dict:
                min_idx = min(tile_dict.keys())
                if min_idx > 0:
                    bin_by_channel[ch_key] = {
                        k - min_idx: v for k, v in tile_dict.items()
                    }

        # We need the store created early for streaming decay writes
        # (decay tiles are too large to stitch in memory)
        _bin_needs_early_store = True

        for ch_key in sorted(bin_by_channel.keys()):
            ch_name = f"ch{ch_key}" if ch_key else "ch0"
            tile_bins = bin_by_channel[ch_key]

            # Read first tile to get dimensions
            first_path = next(iter(tile_bins.values()))
            first_result = read_flim_bin(
                first_path,
                x_dim=bin_dims.get("x_dim", 512),
                y_dim=bin_dims.get("y_dim", 512),
                t_dim=bin_dims.get("t_dim", 132),
                dtype=bin_dims.get("dtype", "float32"),
                dim_order=bin_dims.get("dim_order", "YXT"),
                header_bytes=bin_dims.get("header_bytes", 0),
            )
            tile_h, tile_w, n_bins = first_result["array"].shape

            # Process tiles one at a time — stitch intensity in memory (small),
            # stream decay tiles directly to HDF5 (avoids multi-GB allocation)
            intensity_tiles: dict[int, np.ndarray] = {}

            if tile_config and tile_config.grid_rows * tile_config.grid_cols > 1:
                use_tiling = True
                out_h = tile_config.grid_rows * tile_h
                out_w = tile_config.grid_cols * tile_w
                positions = _tile_positions_from_config(tile_config)
            else:
                use_tiling = False
                out_h, out_w = tile_h, tile_w
                positions = {0: (0, 0)}

            # Track whether we need to write decay to HDF5 via streaming
            _bin_decay_path = f"decay/{ch_name}"
            _bin_decay_written = False

            for tile_idx in sorted(tile_bins.keys()):
                bin_path = tile_bins[tile_idx]
                if tile_idx == next(iter(tile_bins.keys())) and bin_path == first_path:
                    result_bin = first_result  # reuse already-read first tile
                else:
                    result_bin = read_flim_bin(
                        bin_path,
                        x_dim=bin_dims.get("x_dim", 512),
                        y_dim=bin_dims.get("y_dim", 512),
                        t_dim=bin_dims.get("t_dim", 132),
                        dtype=bin_dims.get("dtype", "float32"),
                        dim_order=bin_dims.get("dim_order", "YXT"),
                        header_bytes=bin_dims.get("header_bytes", 0),
                    )

                # Intensity tile (small — keep in memory for stitching)
                intensity_tiles[tile_idx] = result_bin["intensity"]

                # Don't keep decay in memory — it will be re-read tile-by-tile
                # during the streaming HDF5 write phase
                del result_bin

            # Stitch intensity tiles (small, ~35 MB per channel for 6x6 grid)
            if use_tiling:
                stitched_intensity = assemble_tiles(
                    intensity_tiles,
                    grid_rows=tile_config.grid_rows,
                    grid_cols=tile_config.grid_cols,
                    grid_type=tile_config.grid_type,
                    order=tile_config.order,
                )
            else:
                stitched_intensity = next(iter(intensity_tiles.values()))

            channel_images.append(stitched_intensity.astype(np.float32))
            channel_names.append(ch_name)

            # Store info for deferred decay write (tile-by-tile to HDF5)
            tcspc_data[ch_name] = {
                "_streaming": True,
                "tile_bins": tile_bins,
                "bin_dims": bin_dims,
                "tile_h": tile_h,
                "tile_w": tile_w,
                "n_bins": n_bins,
                "out_h": out_h,
                "out_w": out_w,
                "positions": positions,
                "use_tiling": use_tiling,
            }

    # 5. Write to HDF5 (before updating CSV!)
    _progress(4, 5, "Writing HDF5...")
    store = DatasetStore(output_h5)

    all_metadata = {
        "source_dir": str(source_dir),
        "channel_names": channel_names,
        "n_channels": len(channel_images),
    }
    if metadata:
        all_metadata.update(metadata)

    # Add FLIM calibration to metadata
    if flim_params:
        all_metadata["has_flim"] = True
        all_metadata["flim_frequency_mhz"] = flim_params.get("frequency_mhz", 80.0)
        # Store per-channel calibration as separate metadata entries
        ch_cals = flim_params.get("channel_calibrations", {})
        for ch_name, cal in ch_cals.items():
            all_metadata[f"flim_cal_phase_{ch_name}"] = cal.get("phase", 0.0)
            all_metadata[f"flim_cal_mod_{ch_name}"] = cal.get("modulation", 1.0)

    store.create(metadata=all_metadata)

    # Write intensity
    if channel_images:
        if len(channel_images) == 1:
            dims = ["H", "W"]
            store.write_array("intensity", channel_images[0], attrs={"dims": dims})
        else:
            intensity = assemble_channels(channel_images)
            dims = ["C", "H", "W"]
            store.write_array("intensity", intensity, attrs={"dims": dims})

    # Write segmentation label layers
    for name, array in label_layers:
        store.write_labels(name, array)

    # Write mask layers
    for name, array in mask_layers:
        store.write_mask(name, array)

    # Write TCSPC decay data
    for ch_name, decay_info in tcspc_data.items():
        decay_path = f"decay/{ch_name}"

        if isinstance(decay_info, dict) and decay_info.get("_streaming"):
            # Streaming write: read one .bin tile at a time, write to HDF5
            import h5py

            from percell4.io.readers import read_flim_bin

            info = decay_info
            with h5py.File(store.path, "a") as f:
                if decay_path in f:
                    del f[decay_path]

                # Create dataset with full stitched dimensions
                dset = f.create_dataset(
                    decay_path,
                    shape=(info["out_h"], info["out_w"], info["n_bins"]),
                    dtype=np.float32,
                    chunks=(
                        min(64, info["tile_h"]),
                        min(64, info["tile_w"]),
                        info["n_bins"],
                    ),
                    compression="lzf",
                )
                dset.attrs["dims"] = ["H", "W", "T"]
                dset.attrs["channel"] = ch_name

                # Write each tile directly to its region in the dataset
                for tile_idx, bin_path in sorted(info["tile_bins"].items()):
                    tile_data = read_flim_bin(
                        bin_path,
                        x_dim=info["bin_dims"].get("x_dim", 512),
                        y_dim=info["bin_dims"].get("y_dim", 512),
                        t_dim=info["bin_dims"].get("t_dim", 132),
                        dtype=info["bin_dims"].get("dtype", "float32"),
                        dim_order=info["bin_dims"].get("dim_order", "YXT"),
                        header_bytes=info["bin_dims"].get("header_bytes", 0),
                    )["array"].astype(np.float32)

                    if info["use_tiling"] and tile_idx in info["positions"]:
                        row, col = info["positions"][tile_idx]
                        y0 = row * info["tile_h"]
                        x0 = col * info["tile_w"]
                        dset[
                            y0:y0 + info["tile_h"],
                            x0:x0 + info["tile_w"],
                            :,
                        ] = tile_data
                    else:
                        dset[:, :, :] = tile_data

                    del tile_data  # free immediately
        else:
            # In-memory array (from TCSPC TIFFs or single .bin)
            store.write_array(
                decay_path,
                decay_info,
                attrs={"dims": ["H", "W", "T"], "channel": ch_name},
                is_decay=True,
            )

    # 6. Update project.csv
    if project_csv is not None:
        idx = ProjectIndex(project_csv)
        if not idx.exists():
            idx.create()
        idx.add_dataset(str(output_h5), status="complete")

    _progress(5, 5, "Import complete")
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

        # Convert to 0-based tile indices if needed
        if tiles:
            min_idx = min(tiles.keys())
            if min_idx > 0:
                tiles = {k - min_idx: v for k, v in tiles.items()}

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


def _tile_positions_from_config(
    tile_config: TileConfig,
) -> dict[int, tuple[int, int]]:
    """Get tile index → (row, col) mapping from a TileConfig.

    Reuses the assembler's position logic.
    """
    from percell4.io.assembler import _tile_positions

    return _tile_positions(
        tile_config.grid_rows,
        tile_config.grid_cols,
        tile_config.grid_type,
        tile_config.order,
    )
