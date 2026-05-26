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

from percell4.adapters.readers import read_tiff
from percell4.domain.io.assembler import (
    assemble_channels,
    assemble_tiles,
    project_z,
    stack_timepoints,
)
from percell4.domain.io.cross_format import (
    IntensityChannel,
    match_bin_to_intensity,
)
from percell4.domain.io.models import (
    BaseStemRule,
    CompositeRule,
    ScanResult,
    TileConfig,
    TokenConfig,
    ZeroPadOffsetRule,
)
from percell4.domain.io.scanner import FileScanner
from percell4.domain.io.timepoints import (
    count_timepoints,
    ordered_timepoint_tokens,
)
from percell4.domain.io.view_bin import (
    majority_vote_mask,
    mode_labels,
    sum_bin_2d,
)
from percell4.project import ProjectIndex
from percell4.store import DatasetStore, SourceShapeMismatchError


def _validate_and_collect_source_shape(
    channel_images: list,
    label_layers: list,
    mask_layers: list,
) -> tuple[int, int] | None:
    """Validate that every source array shares a single ``(H, W)``.

    Returns the agreed shape, or ``None`` when there are no source arrays.
    Raises :class:`SourceShapeMismatchError` listing the disagreeing
    layers when at least one source disagrees.

    Channel arrays may be 2D ``(H, W)`` or 3D (rare; ``stitched_intensity``
    sometimes carries a leading singleton). Labels and masks are always
    2D. We compare on the trailing two axes uniformly.
    """
    shapes: list[tuple[str, tuple[int, int]]] = []
    for i, arr in enumerate(channel_images):
        if arr.ndim < 2:
            raise SourceShapeMismatchError(
                f"channel[{i}] has ndim={arr.ndim}; expected >= 2"
            )
        shapes.append((f"channel[{i}]", (arr.shape[-2], arr.shape[-1])))
    for name, arr in label_layers:
        shapes.append((f"labels/{name}", (arr.shape[-2], arr.shape[-1])))
    for name, arr in mask_layers:
        shapes.append((f"masks/{name}", (arr.shape[-2], arr.shape[-1])))
    if not shapes:
        return None
    reference = shapes[0][1]
    mismatches = [s for s in shapes if s[1] != reference]
    if mismatches:
        lines = [f"  {n}: {s}" for n, s in shapes]
        raise SourceShapeMismatchError(
            "Compress aborted: source layers disagree on (H, W).\n"
            + "\n".join(lines)
            + "\nAll source TIFFs and .bin tiles in one compress run must "
            "stitch + z-project to the same (H, W). Resolve the offending "
            "files and re-run."
        )
    return reference


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
    files: list | None = None,
    creation_bin: int = 1,
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
    creation_bin : sum-bin every source layer by ``creation_bin`` × ``creation_bin``
        on the post-z-project, post-stitch (H, W) plane before writing.
        Defines the dataset's ``/metadata.native_shape``. ``1`` = no
        binning (byte-identical to the pre-binning behavior for the
        array payloads; only the two new metadata keys are added).

    Returns
    -------
    Number of channels imported.

    Raises
    ------
    SourceShapeMismatchError
        When source layers (channels, labels, masks, .bin synthesized
        intensity) disagree on pre-bin (H, W). Stitch + z-project must
        bring every source into agreement before the bin step.
    ValueError
        On bad inputs (no files, ``creation_bin < 1``).
    """
    if creation_bin < 1:
        raise ValueError(
            f"creation_bin must be >= 1, got {creation_bin}"
        )
    source_dir = Path(source_dir)
    output_h5 = Path(output_h5)

    def _progress(current: int, total: int, msg: str) -> None:
        if progress_callback is not None:
            progress_callback(current, total, msg)

    # 1. Scan directory — find TIFFs and .bin files
    _progress(0, 5, "Scanning files...")
    scanner = FileScanner(token_config)
    if files is not None:
        # Use pre-discovered file list (avoids re-scanning the whole directory)
        result = scanner.scan(files=[str(f.path) if hasattr(f, "path") else str(f) for f in files])
    else:
        result = scanner.scan(path=source_dir)

    if not result.files:
        raise ValueError(f"No image files found in {source_dir}")

    bin_files = sorted(
        f.path for f in result.files if f.path.suffix.lower() == ".bin"
    )

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

    # 2. Separate TIFF intensity vs TCSPC TIFFs (.bin handled separately below)
    _progress(1, 5, "Organizing files...")
    tcspc_files = []
    intensity_files = []
    for f in result.files:
        if f.path.suffix.lower() == ".bin":
            continue
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
        if "timepoint" in f.tokens:
            intensity_result.timepoints.add(f.tokens["timepoint"])
        if "tile" in f.tokens:
            intensity_result.tiles.add(f.tokens["tile"])
        if "z_slice" in f.tokens:
            intensity_result.z_slices.add(f.tokens["z_slice"])

    # Time-lapse axis: the leading T axis is added only when more than one
    # timepoint token is present across the intensity files. Single-timepoint
    # (or no _t token) datasets keep today's exact 2D / (C, H, W) layout.
    n_timepoints = count_timepoints(intensity_result.timepoints)

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

        if n_timepoints > 1:
            # Time-lapse: assemble one plane per timepoint, then stack on a
            # leading T axis. Numeric (not lexical) timepoint ordering comes
            # from the canonical helper so _t2 precedes _t10.
            tp_groups = _group_by_timepoint(files)
            tp_tokens = ordered_timepoint_tokens(tp_groups.keys())
            if len(tp_tokens) != n_timepoints:
                raise SourceShapeMismatchError(
                    f"Channel {default_name!r} covers {len(tp_tokens)} "
                    f"timepoint(s) but the dataset has {n_timepoints}. Every "
                    "channel must cover the same timepoints; a missing frame "
                    "would mis-stack the time axis.\n"
                    f"  found: {tp_tokens}"
                )
            planes = [
                _assemble_plane(tp_groups[tp], tile_config, z_project_method)
                for tp in tp_tokens
            ]
            channel_img = stack_timepoints(planes)
        else:
            channel_img = _assemble_plane(files, tile_config, z_project_method)

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

        from percell4.adapters.readers import read_flim_bin

        bin_dims = flim_params.get("bin_dimensions", {})
        config = token_config or TokenConfig()

        # Build IntensityChannel records (name, token, base_stem) — fed to the
        # cross-format matcher. ``base_stem`` is the TIFF stem with the channel
        # token stripped (used by BaseStemRule when a .bin carries no channel
        # token, e.g. ``DA Halo 20uL 1.bin`` paired with ``DA Halo 20uL 1_ch00.tif``).
        intensity_channels: list[IntensityChannel] = []
        seen_tokens: set[str] = set()
        if config.channel:
            for f in intensity_files:
                ch_tok = f.tokens.get("channel", "")
                if not ch_tok or ch_tok in seen_tokens:
                    continue
                seen_tokens.add(ch_tok)
                base = re.sub(config.channel, "", f.path.stem).strip("_- ") or None
                intensity_channels.append(IntensityChannel(
                    name=f"ch{ch_tok}",
                    token=ch_tok,
                    base_stem=base,
                ))

        # Match .bin files to intensity channels via the canonical pure-domain
        # function. The default rule is a Composite that first tries
        # ZeroPadOffsetRule(0, 0) — exact channel-token equality, the
        # historical importer behavior — then falls back to BaseStemRule for
        # .bin files that carry no channel token. The user-microscope case
        # (TIFF ``_s00_ch00`` ↔ BIN ``s1_ch1`` with offset+1) is enabled by
        # selecting ZeroPadOffsetRule(2, 1) from the dialog (U4); the importer
        # default preserves byte-identical behavior with the pre-refactor block.
        match_rule = CompositeRule(rules=(
            ZeroPadOffsetRule(pad_width=0, offset=0),
            BaseStemRule(),
        ))
        match_result = match_bin_to_intensity(
            list(bin_files),
            intensity_channels,
            match_rule,
            config,
        )

        # Convert MatchResult → bin_by_channel structure the streaming code
        # expects below. Bin files that bound to a channel use the channel's
        # token as the key; unmatched/ambiguous bins fall through with empty
        # token (becoming /decay/ch0) — same fallback the historical code had.
        token_for_channel = {c.name: c.token for c in intensity_channels}
        bin_to_token = {
            b.bin_path: token_for_channel.get(b.channel_name, "")
            for b in match_result.bindings
        }
        # When there are no TIFF intensity files (bin-only import), the
        # cross-format matcher has no targets — every bin falls into
        # ``match_result.unmatched`` and ``bin_to_token`` is empty.
        # Restore the pre-U1 behavior for that case: parse each bin's
        # ``_ch(\d+)`` token directly so bins still split into per-channel
        # buckets (ch00, ch01, …) rather than collapsing into a single
        # empty-token bucket. This matches what the historical compress
        # flow produced when only .bin files were imported.
        bin_only_mode = not intensity_channels
        bin_by_channel: dict[str, dict[int, Path]] = defaultdict(dict)
        for bin_path in bin_files:
            stem = bin_path.stem
            if bin_only_mode and config.channel:
                m = re.search(config.channel, stem)
                ch = m.group(1) if m else ""
            else:
                ch = bin_to_token.get(bin_path, "")
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

            # Only synthesize an intensity channel from the .bin sum when
            # there isn't already a TIFF-sourced intensity layer for this
            # channel. Otherwise the TIFF intensity wins and the .bin is
            # decay-only — no duplicate napari layer.
            if ch_name not in channel_names:
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

            # Store info for deferred decay write (tile-by-tile to HDF5).
            # write_decay_streaming expects POST-bin dims (its kwarg
            # ``spatial_bin`` drives the per-tile bin during streaming),
            # so we floor-divide here to match the validate-and-bin step
            # 4b ran on the synthesized intensity.
            tcspc_data[ch_name] = {
                "_streaming": True,
                "tile_bins": tile_bins,
                "bin_dims": bin_dims,
                "tile_h": tile_h // creation_bin,
                "tile_w": tile_w // creation_bin,
                "n_bins": n_bins,
                "out_h": out_h // creation_bin,
                "out_w": out_w // creation_bin,
                "positions": positions,
                "use_tiling": use_tiling,
                "creation_bin": creation_bin,
            }

    # 4b. Source-shape validation + creation_bin application.
    # Every TIFF channel, label, mask, and .bin-synthesized intensity
    # must agree on (H, W) post-z-project, post-stitch and pre-bin. If
    # the user runs Compress over a directory containing mixed
    # resolutions, we abort before any write -- silent partial imports
    # would corrupt the native_shape invariant declared in /metadata.
    pre_bin_shape = _validate_and_collect_source_shape(
        channel_images, label_layers, mask_layers
    )
    if creation_bin > 1 and pre_bin_shape is not None:
        channel_images = [
            sum_bin_2d(arr, creation_bin) for arr in channel_images
        ]
        label_layers = [
            (name, mode_labels(arr.astype(np.int32, copy=False), creation_bin))
            for name, arr in label_layers
        ]
        mask_layers = [
            (name, majority_vote_mask(
                (arr > 0).astype(np.uint8), creation_bin
            ))
            for name, arr in mask_layers
        ]

    # native_shape is the post-bin (H, W) of any of the above arrays.
    native_shape: tuple[int, int] | None = None
    if pre_bin_shape is not None:
        h, w = pre_bin_shape
        native_shape = (h // creation_bin, w // creation_bin)

    # 5. Write to HDF5 (before updating CSV!)
    _progress(4, 5, "Writing HDF5...")
    store = DatasetStore(output_h5)

    all_metadata = {
        "source_dir": str(source_dir),
        "channel_names": channel_names,
        "n_channels": len(channel_images),
        "creation_bin": int(creation_bin),
        "n_timepoints": int(n_timepoints),
    }
    if native_shape is not None:
        all_metadata["native_shape"] = native_shape

    # Persist pixel_size_um from the first intensity file's TIFF
    # resolution tag. Downstream measurement code uses this to emit
    # `<area_col>_um2` sibling columns so areas can be reported in
    # physical units without out-of-band knowledge of the
    # microscope's calibration. The scanner doesn't read TIFF tags
    # (domain/ can't import adapters/), so we read the first
    # intensity file's metadata here and convert via
    # read_tiff_metadata's XResolution + ResolutionUnit handling.
    if intensity_files:
        from percell4.adapters.readers import read_tiff_metadata

        try:
            tiff_meta = read_tiff_metadata(intensity_files[0].path)
            first_px = tiff_meta.get("pixel_size_um")
        except Exception:
            first_px = None
        if first_px is not None and first_px > 0:
            # Pre-bake creation_bin into the persisted value so
            # measurements made on the binned (H, W) plane stay
            # consistent — every binned pixel is creation_bin pixels
            # wide in the source.
            scaled = float(first_px) * max(1, int(creation_bin))
            all_metadata["pixel_size_um"] = scaled

    if metadata:
        all_metadata.update(metadata)

    # Persist the stitching configuration so subsequent add-layer flows on
    # this dataset can replicate compress's tile placement byte-for-byte.
    # Without this, the user has to remember the Pattern/Start they picked
    # in the Compress dialog and re-enter them in the Add Layer dialog —
    # any drift (e.g., snake_by_column vs row_by_row) puts decay tiles in
    # different (h, w) positions and the spatial Filtered phasor diverges.
    if tile_config is not None:
        all_metadata["stitch_grid_rows"] = int(tile_config.grid_rows)
        all_metadata["stitch_grid_cols"] = int(tile_config.grid_cols)
        all_metadata["stitch_grid_type"] = str(tile_config.grid_type)
        all_metadata["stitch_order"] = str(tile_config.order)

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

    # Write intensity. With a time axis (n_timepoints > 1) each channel image
    # is already (T, H, W); single-channel writes (T, H, W) and multi-channel
    # stacks on axis=1 to (T, C, H, W). Without a time axis the layout is the
    # historical (H, W) / (C, H, W).
    if channel_images:
        time_lapse = n_timepoints > 1
        if len(channel_images) == 1:
            dims = ["T", "H", "W"] if time_lapse else ["H", "W"]
            store.write_array("intensity", channel_images[0], attrs={"dims": dims})
        elif time_lapse:
            intensity = np.stack(channel_images, axis=1)  # (T, C, H, W)
            dims = ["T", "C", "H", "W"]
            store.write_array("intensity", intensity, attrs={"dims": dims})
        else:
            intensity = assemble_channels(channel_images)
            dims = ["C", "H", "W"]
            store.write_array("intensity", intensity, attrs={"dims": dims})

    # Write segmentation label layers
    for name, array in label_layers:
        store.write_labels(name, array)

    # Write mask layers (binarize: any nonzero value → 1)
    for name, array in mask_layers:
        binary = (array > 0).astype(np.uint8)
        store.write_mask(name, binary)

    # Write TCSPC decay data
    for ch_name, decay_info in tcspc_data.items():
        decay_path = f"decay/{ch_name}"

        if isinstance(decay_info, dict) and decay_info.get("_streaming"):
            # Streaming write delegated to the shared helper so that compress
            # and the add-layer append flow take exactly the same code path
            # for byte-identical output on identical inputs.
            info = decay_info
            write_decay_streaming(
                h5_path=store.path,
                channel_name=ch_name,
                tile_bins=info["tile_bins"],
                bin_dims=info["bin_dims"],
                tile_h=info["tile_h"],
                tile_w=info["tile_w"],
                n_bins=info["n_bins"],
                out_h=info["out_h"],
                out_w=info["out_w"],
                positions=info["positions"],
                use_tiling=info["use_tiling"],
                spatial_bin=info.get("creation_bin", 1),
            )
            continue

        # Legacy path retained for the (unused-in-practice) non-streaming
        # case so the loop body stays a single shape.
        if isinstance(decay_info, dict) and decay_info.get("_streaming"):
            import h5py
            from percell4.adapters.readers import read_flim_bin
            info = decay_info
            with h5py.File(store.path, "a") as f:
                if decay_path in f:
                    del f[decay_path]
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


def _group_by_timepoint(files: list) -> dict[str, list]:
    """Group files by timepoint token (the digits captured by ``_t(\\d+)``).

    Files with no timepoint token group under the empty-string key — the
    single-timepoint degenerate case the caller handles by not stacking.
    """
    groups: dict[str, list] = defaultdict(list)
    for f in files:
        tp = f.tokens.get("timepoint", "")
        groups[tp].append(f)
    return dict(groups)


def _assemble_plane(
    files: list,
    tile_config: TileConfig | None,
    z_project_method: str | None,
) -> np.ndarray:
    """Assemble one channel's files for a single timepoint into a 2D plane.

    Groups by z-slice and either z-projects (when multiple z and a method is
    given) or stitches/loads. This is the per-(channel, timepoint) unit of
    assembly; the caller stacks planes across timepoints when needed.
    """
    z_groups = _group_by_z(files)
    if len(z_groups) > 1 and z_project_method is not None:
        z_images = []
        for z_key in sorted(z_groups.keys()):
            z_images.append(_load_and_stitch(z_groups[z_key], tile_config))
        return project_z(z_images, method=z_project_method)
    all_files = []
    for z_key in sorted(z_groups.keys()):
        all_files.extend(z_groups[z_key])
    return _load_and_stitch(all_files, tile_config)


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

    # Multiple files but no tile_config and no recognized z-slice tokens
    # (the z-slice branch in _assemble_plane runs first when z tokens
    # are present). Silently returning files[0] used to land here and
    # discard the rest, which silently truncated multi-tile datasets to
    # a single tile. Raise instead so the caller has to be explicit.
    names = ", ".join(f.path.name for f in files)
    raise ValueError(
        f"_load_and_stitch received {len(files)} files for one channel "
        "but no tile_config and no z-slice tokens were detected. "
        "Either configure tile stitching (TileConfig) for the importer "
        "or check the filename token regex so z-slices are recognized. "
        f"Files: {names}"
    )


def _tile_positions_from_config(
    tile_config: TileConfig,
) -> dict[int, tuple[int, int]]:
    """Get tile index → (row, col) mapping from a TileConfig.

    Reuses the assembler's position logic.
    """
    from percell4.domain.io.assembler import _tile_positions

    return _tile_positions(
        tile_config.grid_rows,
        tile_config.grid_cols,
        tile_config.grid_type,
        tile_config.order,
    )


def write_decay_streaming(
    h5_path: str | Path,
    channel_name: str,
    tile_bins: dict[int, Path],
    bin_dims: dict[str, Any],
    tile_h: int,
    tile_w: int,
    n_bins: int,
    out_h: int,
    out_w: int,
    positions: dict[int, tuple[int, int]],
    use_tiling: bool,
    spatial_bin: int = 1,
) -> None:
    """Stream-write a TCSPC decay channel to ``/decay/<channel_name>`` in an
    .h5 file, tile by tile.

    Single source of truth for `.bin` decay writes — used by both the
    initial-import (compress) flow and the append-via-add-layer flow so
    that identical inputs (TileConfig + bin_dims + tile_bins) produce
    byte-identical output. Mirrors what compress's importer used to do
    inline.

    Existing data at ``/decay/<channel_name>`` is overwritten — caller is
    responsible for any conflict-checking (e.g.,
    ``DatasetStore.append_decay_layers`` raises ``LayerAlreadyExists`` for
    that, but this helper sits below that check by design — it's the
    write primitive).

    ``spatial_bin`` sums non-overlapping k×k blocks per tile after read,
    before placement (T-axis untouched). Use to match a higher-resolution
    TCSPC source to a downsampled TIFF /intensity — e.g., k=3 reduces a
    512×512 tile to 170×170, dropping the 2 residual pixels per axis.
    Sum (not mean) preserves Poisson photon counts. ``tile_h``, ``tile_w``,
    ``out_h`` and ``out_w`` are the POST-binning dimensions.
    """
    import h5py

    from percell4.adapters.readers import read_flim_bin

    if spatial_bin < 1:
        raise ValueError(f"spatial_bin must be >= 1, got {spatial_bin}")

    decay_path = f"decay/{channel_name}"
    with h5py.File(h5_path, "a") as f:
        if decay_path in f:
            del f[decay_path]
        # Invalidate stale phasor for this channel — without this, the GUI
        # phasor plot shows the OLD computed (g, s) maps even after the
        # underlying /decay layer was overwritten, which looks like a
        # broken import. compute_phasor must be re-run to refresh.
        phasor_path = f"phasor/{channel_name}"
        if phasor_path in f:
            del f[phasor_path]
        dset = f.create_dataset(
            decay_path,
            shape=(out_h, out_w, n_bins),
            dtype=np.float32,
            chunks=(min(64, tile_h), min(64, tile_w), n_bins),
            compression="lzf",
        )
        dset.attrs["dims"] = ["H", "W", "T"]
        dset.attrs["channel"] = channel_name
        if spatial_bin > 1:
            dset.attrs["spatial_bin"] = spatial_bin

        for tile_idx, bin_path in sorted(tile_bins.items()):
            tile_data = read_flim_bin(
                bin_path,
                x_dim=bin_dims.get("x_dim", 512),
                y_dim=bin_dims.get("y_dim", 512),
                t_dim=bin_dims.get("t_dim", 132),
                dtype=bin_dims.get("dtype", "float32"),
                dim_order=bin_dims.get("dim_order", "YXT"),
                header_bytes=bin_dims.get("header_bytes", 0),
            )["array"].astype(np.float32)

            if spatial_bin > 1:
                tile_data = _spatial_bin_tile(tile_data, spatial_bin)

            if use_tiling and tile_idx in positions:
                row, col = positions[tile_idx]
                y0 = row * tile_h
                x0 = col * tile_w
                dset[y0:y0 + tile_h, x0:x0 + tile_w, :] = tile_data
            else:
                dset[:, :, :] = tile_data
            del tile_data


def _spatial_bin_tile(tile: np.ndarray, k: int) -> np.ndarray:
    """Sum non-overlapping k×k spatial blocks of a (H, W, T) tile.

    Truncates any leftover pixels (H % k, W % k) so the result is
    (H // k, W // k, T). Sum-binning preserves total photon counts —
    required for valid Poisson statistics downstream in phasor
    computation. T-axis untouched.
    """
    if k <= 1:
        return tile
    h, w = tile.shape[:2]
    h_trunc = (h // k) * k
    w_trunc = (w // k) * k
    truncated = tile[:h_trunc, :w_trunc]
    h_b, w_b = h_trunc // k, w_trunc // k
    return truncated.reshape(h_b, k, w_b, k, -1).sum(axis=(1, 3))
