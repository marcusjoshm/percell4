"""Use case: add TCSPC decay layers to an existing .h5 dataset.

Orchestrates the append flow: discover .bin files in a source directory,
read existing intensity channel names from the store metadata, match each
.bin to a channel via the cross-format matcher, pre-flight check for
existing /decay groups, stitch tiles, build provenance records, and call
``DatasetStore.append_decay_layers``.

Pure-Python — no Qt, no h5py-direct (uses ``DatasetStore``). Returns an
``AppendReport`` describing what landed and what didn't. Per-channel
errors collect into ``AppendReport.errors`` rather than raising.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from percell4 import __version__ as _percell4_version
from percell4.adapters.importer import write_decay_streaming
from percell4.adapters.readers import read_flim_bin
from percell4.domain.io.assembler import _tile_positions
from percell4.domain.io.cross_format import (
    IntensityChannel,
    match_bin_to_intensity,
)
from percell4.domain.io.models import (
    CrossFormatRule,
    ExplicitRule,
    FlimConfig,
    ProvenanceRecord,
    TileConfig,
    TokenConfig,
)
from percell4.store import DatasetStore, LayerSizeMismatchError


@dataclass(frozen=True)
class AppendReport:
    """Result of running ``add_decay_to_dataset``.

    ``written`` lists channel names whose decay layer landed. ``unmatched``
    holds .bin paths the matcher couldn't bind. ``ambiguous`` holds .bin
    paths that matched multiple channels (caller should re-submit with
    ``ExplicitRule`` overrides). ``errors`` is a per-channel-or-stage
    string keyed by channel name (or ``"intensity"`` / ``"scan"`` for
    pre-flight stages).
    """

    written: tuple[str, ...] = ()
    bindings: tuple = ()  # tuple of BindingResult
    unmatched: tuple[Path, ...] = ()
    ambiguous: tuple[tuple[Path, tuple[str, ...]], ...] = ()
    errors: dict[str, str] = field(default_factory=dict)


def add_decay_to_dataset(
    h5_path: str | Path,
    source_dir: str | Path,
    token_config: TokenConfig,
    tile_config: TileConfig,
    flim_config: FlimConfig,
    cross_format_rule: CrossFormatRule,
    *,
    rotate_k: int = 0,
    flip_axis: int | None = None,
    force: bool = False,
    progress_callback: Callable[[str], None] | None = None,
    intensity_channels: list[IntensityChannel] | None = None,
) -> AppendReport:
    """Append .bin TCSPC layers to an existing .h5 dataset.

    See module docstring for the full flow. Returns an ``AppendReport``;
    never raises on per-channel errors.

    ``rotate_k`` rotates each stitched decay volume by ``k * 90°`` counter-
    clockwise in the (H, W) plane (T-axis untouched). The microscope's LASX
    export rotates ``.bin`` tiles relative to their matching ``.tiff`` —
    ``rotate_k`` lets the dialog correct that before append. ``k=0`` is no
    rotation; ``k=1`` is 90° CCW; ``k=2`` is 180°; ``k=3`` is 90° CW (= 270°
    CCW).

    ``flip_axis`` mirrors the stitched decay volume along the given spatial
    axis, applied AFTER rotation. ``None`` is no flip; ``0`` mirrors top
    ↔ bottom (vertical flip / flipud); ``1`` mirrors left ↔ right
    (horizontal flip / fliplr). T-axis untouched. Both rotation and flip
    only touch /decay/<ch> — /intensity is never modified.

    Source-shape validation: the stitched .bin output shape (rows*tile_h,
    cols*tile_w) must equal ``/metadata.native_shape`` -- the dataset-wide
    binning model locks native at compress and refuses to silently
    accept a mismatched ancillary import. Mismatches produce a per-channel
    error in the returned report. The user resolves them by re-importing
    via the Compress dialog with the right creation_bin.

    ``intensity_channels`` lets the caller supply the IntensityChannel
    records directly, bypassing token derivation from the channel-name
    digit suffix. Required when channels carry semantic names (e.g.,
    ``CA-SiR``, ``mNG``) where the digit-suffix heuristic produces an
    empty token and the matcher would have nothing to compare against.
    The dialog passes its per-channel-token-override map this way.
    """
    h5_path = Path(h5_path)
    source_dir = Path(source_dir)
    progress = progress_callback or (lambda _: None)

    progress("Scanning source directory")
    bin_files = sorted(p for p in source_dir.rglob("*.bin") if p.is_file())
    if not bin_files:
        return AppendReport(
            errors={"scan": f"no .bin files found under {source_dir}"}
        )

    store = DatasetStore(h5_path)
    metadata = store.metadata
    channel_names = list(metadata.get("channel_names", []))
    if not channel_names:
        return AppendReport(
            errors={"intensity": "no /intensity channels in dataset (channel_names empty)"}
        )

    # Build IntensityChannel records — caller-supplied list (from the
    # dialog's per-channel token overrides) wins over digit-suffix
    # derivation. Required when channels have semantic names like
    # CA-SiR / mNG / mTQ2 that have no parseable token.
    progress("Reading existing intensity channels")
    if intensity_channels is None:
        channel_base_stems = list(metadata.get("channel_base_stems", []))
        intensity_channels = []
        for i, name in enumerate(channel_names):
            token = _extract_channel_token(name, token_config) or ""
            base_stem = channel_base_stems[i] if i < len(channel_base_stems) else None
            intensity_channels.append(
                IntensityChannel(name=name, token=token, base_stem=base_stem)
            )

    # Match
    progress("Matching .bin files to intensity channels")
    match_result = match_bin_to_intensity(
        bin_files, intensity_channels, cross_format_rule, token_config
    )

    # Pre-flight: detect existing /decay/<name> conflicts
    existing_decay = set(store.list_groups("decay"))
    errors: dict[str, str] = {}
    bindings_to_write = list(match_result.bindings)
    if not force:
        kept = []
        for binding in bindings_to_write:
            if binding.channel_name in existing_decay:
                errors[binding.channel_name] = (
                    f"decay layer already exists for {binding.channel_name}; "
                    "re-run with Replace to overwrite"
                )
            else:
                kept.append(binding)
        bindings_to_write = kept

    # Group bindings by channel (multiple .bin files per channel = tiles)
    by_channel: dict[str, list] = {}
    for binding in bindings_to_write:
        by_channel.setdefault(binding.channel_name, []).append(binding)

    # Build the bin_dims dict the shared streaming helper expects (the
    # exact same shape compress passes). This is the SOURCE OF TRUTH for
    # how .bin tiles are read — no per-tile transformations beyond what
    # read_flim_bin already does.
    bin_dims = {
        "x_dim": flim_config.bin_x or 512,
        "y_dim": flim_config.bin_y or 512,
        "t_dim": flim_config.bin_t or 132,
        "dtype": flim_config.bin_dtype or "uint16",
        "dim_order": flim_config.bin_dim_order or "YXT",
        "header_bytes": flim_config.bin_header_bytes or 0,
    }

    written: list[str] = []
    selected_rule_name = type(cross_format_rule).__name__

    # ── Phase 1: stitch all tiles for every channel ─────────────────────
    # Stream each channel's tiles into its /decay/<ch> dataset via the
    # shared write_decay_streaming helper. No rotation applied yet —
    # the entire stitched image is the input to Phase 2.
    for ch_name, bindings in by_channel.items():
        progress(f"Stitching {ch_name} ({len(bindings)} tile(s))")
        try:
            # Build tile_idx → Path map (same logic compress uses)
            tile_to_path: dict[int, Path] = {}
            for b in bindings:
                idx = _extract_tile_index(b.bin_path.stem, token_config)
                if idx is None:
                    raise ValueError(
                        f"tile token missing from {b.bin_path.name}; cannot stitch"
                    )
                tile_to_path[idx] = b.bin_path
            # Normalize 1-based → 0-based
            if tile_to_path:
                min_idx = min(tile_to_path.keys())
                if min_idx > 0:
                    tile_to_path = {k - min_idx: v for k, v in tile_to_path.items()}

            # Read first tile to determine dimensions (compress does the same)
            first_path = next(iter(tile_to_path.values()))
            first_result = read_flim_bin(first_path, **bin_dims)
            tile_h, tile_w, n_bins = first_result["array"].shape
            del first_result

            use_tiling = tile_config.grid_rows * tile_config.grid_cols > 1
            if use_tiling:
                out_h = tile_config.grid_rows * tile_h
                out_w = tile_config.grid_cols * tile_w
                positions = _tile_positions(
                    tile_config.grid_rows,
                    tile_config.grid_cols,
                    tile_config.grid_type,
                    tile_config.order,
                )
            else:
                out_h, out_w = tile_h, tile_w
                positions = {0: (0, 0)}

            # Source-shape validation: the stitched (H, W) must equal
            # /metadata.native_shape. Mismatched ancillary imports are
            # the user's signal to re-import via Compress with a
            # different creation_bin -- the .h5 declares its own native
            # at compress and we don't accept layers at any other size.
            native_shape = metadata.get("native_shape")
            if native_shape is not None and tuple(native_shape) != (out_h, out_w):
                raise LayerSizeMismatchError(
                    f"Source TCSPC stitched shape is ({out_h}, {out_w}); "
                    f"dataset native_shape is {tuple(native_shape)}. "
                    "Re-import via Compress dialog with the matching "
                    "creation_bin, or pre-bin the .bin files externally."
                )

            # Delegate the actual decay write to the shared helper --
            # compress and add-layer go through the SAME streaming write.
            # spatial_bin defaults to 1; only the importer's compress
            # path passes a larger value (see U7).
            write_decay_streaming(
                h5_path=h5_path,
                channel_name=ch_name,
                tile_bins=tile_to_path,
                bin_dims=bin_dims,
                tile_h=tile_h,
                tile_w=tile_w,
                n_bins=n_bins,
                out_h=out_h,
                out_w=out_w,
                positions=positions,
                use_tiling=use_tiling,
            )

        except Exception as e:  # noqa: BLE001
            errors[ch_name] = f"read/stitch failed: {e}"
            continue

        written.append(ch_name)

    # ── Phase 2: rotate every fully-stitched channel image as a whole ───
    # Reads /decay/<ch> (now a complete stitched image), rotates the
    # (H, W) plane by k*90° CCW with the T-axis preserved per pixel,
    # writes back. Runs only after every channel finished Phase 1, so
    # rotation always operates on the complete stitched output rather
    # than on a partially-written or per-tile region. ONLY /decay is
    # rotated — never the user's existing /intensity channels (which may
    # be TIFF source data that must not be modified by an append flow).
    if rotate_k:
        k = int(rotate_k) % 4
        for ch_name in list(written):
            progress(f"Rotating {ch_name} by {k * 90}° CCW")
            try:
                _rotate_decay_in_place(h5_path, ch_name, k)
            except Exception as e:  # noqa: BLE001
                errors[ch_name] = (
                    errors.get(ch_name, "") + f" rotation failed: {e}"
                ).strip()

    # ── Phase 2b: flip every fully-stitched (and possibly rotated) channel
    # Mirror the (H, W) plane along the given axis (0 = vertical, 1 =
    # horizontal). T-axis preserved. Applied after rotation so users can
    # compose the two when their LASX export needs both. ONLY /decay
    # — /intensity is never touched.
    if flip_axis is not None:
        for ch_name in list(written):
            progress(f"Flipping {ch_name} along axis {flip_axis}")
            try:
                _flip_decay_in_place(h5_path, ch_name, int(flip_axis))
            except Exception as e:  # noqa: BLE001
                errors[ch_name] = (
                    errors.get(ch_name, "") + f" flip failed: {e}"
                ).strip()

    # ── Phase 3: write provenance for every successfully-stitched channel
    for ch_name in list(written):
        bindings = by_channel[ch_name]
        first_binding = bindings[0]
        prov = ProvenanceRecord(
            source_path=str(first_binding.bin_path.resolve()),
            cross_format_rule=selected_rule_name,
            match_evidence=json.dumps(first_binding.evidence.to_dict()),
            manually_overridden=isinstance(cross_format_rule, ExplicitRule),
            importer_version=_percell4_version,
            timestamp_utc=datetime.now(UTC).isoformat(),
            content_sha256=_sha256_file(first_binding.bin_path),
        )
        _write_provenance_attrs(h5_path, ch_name, prov)

    # Persist cross_format_rule to /metadata so subsequent flows can read it
    if written and cross_format_rule is not None and not isinstance(cross_format_rule, ExplicitRule):
        from percell4.domain.io.cross_format import serialize_rule
        try:
            store.set_metadata({"cross_format_rule": serialize_rule(cross_format_rule)})
        except Exception:  # noqa: BLE001
            pass

    return AppendReport(
        written=tuple(written),
        bindings=match_result.bindings,
        unmatched=match_result.unmatched,
        ambiguous=match_result.ambiguous,
        errors=errors,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _write_provenance_attrs(h5_path, ch_name: str, prov: ProvenanceRecord) -> None:
    """Write a ProvenanceRecord to ``/provenance/decay/<ch_name>`` attrs."""
    import h5py
    with h5py.File(h5_path, "a") as f:
        path = f"provenance/decay/{ch_name}"
        if path in f:
            del f[path]
        grp = f.require_group(path)
        for key, val in prov.to_attrs().items():
            grp.attrs[key] = val


def _flip_decay_in_place(h5_path, ch_name: str, axis: int) -> None:
    """Flip /decay/<ch> along the given spatial axis (0 = H, 1 = W).

    T-axis untouched; phasor histogram is flip-invariant. Also
    invalidates any stale /phasor/<ch>.
    """
    if axis not in (0, 1):
        return
    import h5py
    with h5py.File(h5_path, "a") as f:
        path = f"decay/{ch_name}"
        if path not in f:
            return
        arr = f[path][...]
        arr = np.flip(arr, axis=axis)
        arr = np.ascontiguousarray(arr)
        attrs = dict(f[path].attrs)
        del f[path]
        phasor_path = f"phasor/{ch_name}"
        if phasor_path in f:
            del f[phasor_path]
        from percell4.store import _choose_chunks, _compression_kwargs
        f.create_dataset(
            path,
            data=arr.astype(np.float32, copy=False),
            chunks=_choose_chunks(arr.shape, is_decay=True),
            **_compression_kwargs(is_decay=True),
        )
        for k_, v in attrs.items():
            f[path].attrs[k_] = v


def _rotate_decay_in_place(h5_path, ch_name: str, k: int) -> None:
    """Read /decay/<ch>, rotate the (H, W) plane by k*90° CCW, write back.

    T-axis untouched — phasor histogram is invariant under this rotation,
    only the spatial layout changes (so napari overlays align with TIFF
    intensity when LASX rotated the .bin tiles).

    Also invalidates any stale /phasor/<ch> so the GUI phasor view can't
    show a cached computation made on the pre-rotation decay.
    """
    if k == 0:
        return
    import h5py
    with h5py.File(h5_path, "a") as f:
        path = f"decay/{ch_name}"
        if path not in f:
            return
        arr = f[path][...]
        arr = np.rot90(arr, k=k, axes=(0, 1))
        arr = np.ascontiguousarray(arr)
        attrs = dict(f[path].attrs)
        del f[path]
        # Invalidate stale phasor — must be re-computed against rotated decay
        phasor_path = f"phasor/{ch_name}"
        if phasor_path in f:
            del f[phasor_path]
        from percell4.store import _choose_chunks, _compression_kwargs
        f.create_dataset(
            path,
            data=arr.astype(np.float32, copy=False),
            chunks=_choose_chunks(arr.shape, is_decay=True),
            **_compression_kwargs(is_decay=True),
        )
        for k_, v in attrs.items():
            f[path].attrs[k_] = v


def _extract_channel_token(name: str, token_config: TokenConfig) -> str | None:
    """Parse the channel token out of a channel name like 'ch00' → '00'.

    Channel names produced by the importer follow the convention ``ch<token>``
    (e.g., ``"ch00"``). The plain digit suffix is the token. We prefer the
    digit-suffix path over re-applying ``token_config.channel`` because
    that pattern targets *filename* tokens (with leading underscore) that
    don't apply to bare channel names.
    """
    m = re.search(r"(\d+)$", name)
    return m.group(1) if m else None


def _read_and_stitch_decay(
    bindings: list,
    tile_config: TileConfig,
    flim_config: FlimConfig,
    token_config: TokenConfig,
) -> np.ndarray:
    """Read each binding's .bin, place tiles in grid positions, return stitched (H, W, T)."""
    bin_kwargs = {
        "x_dim": flim_config.bin_x or 512,
        "y_dim": flim_config.bin_y or 512,
        "t_dim": flim_config.bin_t or 132,
        "dtype": flim_config.bin_dtype or "uint16",
        "dim_order": flim_config.bin_dim_order or "YXT",
        "header_bytes": flim_config.bin_header_bytes or 0,
    }

    # Single-tile fast path
    if tile_config.grid_rows == 1 and tile_config.grid_cols == 1:
        result = read_flim_bin(bindings[0].bin_path, **bin_kwargs)
        return np.asarray(result["array"])

    # Multi-tile: read all tiles, place in grid via _tile_positions
    tile_to_path: dict[int, Path] = {}
    for b in bindings:
        idx = _extract_tile_index(b.bin_path.stem, token_config)
        if idx is None:
            raise ValueError(
                f"tile token missing from {b.bin_path.name}; cannot stitch"
            )
        tile_to_path[idx] = b.bin_path

    # Normalize 1-based → 0-based (filenames may use _s1, _s2, …)
    if tile_to_path:
        min_idx = min(tile_to_path.keys())
        if min_idx > 0:
            tile_to_path = {k - min_idx: v for k, v in tile_to_path.items()}

    # Read first tile to determine shape/dtype
    first_path = next(iter(tile_to_path.values()))
    first = read_flim_bin(first_path, **bin_kwargs)
    first_arr = np.asarray(first["array"])
    tile_h, tile_w = first_arr.shape[:2]
    t_dim = first_arr.shape[2] if first_arr.ndim == 3 else 1

    out_h = tile_config.grid_rows * tile_h
    out_w = tile_config.grid_cols * tile_w
    output = np.zeros((out_h, out_w, t_dim), dtype=first_arr.dtype)

    positions = _tile_positions(
        tile_config.grid_rows,
        tile_config.grid_cols,
        tile_config.grid_type,
        tile_config.order,
    )

    for tile_idx, (row, col) in positions.items():
        if tile_idx not in tile_to_path:
            continue
        result = read_flim_bin(tile_to_path[tile_idx], **bin_kwargs)
        arr = np.asarray(result["array"])
        y0 = row * tile_h
        x0 = col * tile_w
        output[y0 : y0 + tile_h, x0 : x0 + tile_w] = arr

    return output


def _extract_tile_index(stem: str, token_config: TokenConfig) -> int | None:
    if not token_config.tile:
        return None
    m = re.search(token_config.tile, stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes. Pragmatic single-pass read."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
