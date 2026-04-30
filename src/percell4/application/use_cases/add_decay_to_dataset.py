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
from percell4.store import DatasetStore


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
    force: bool = False,
    progress_callback: Callable[[str], None] | None = None,
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

    # Build IntensityChannel records — derive token + base_stem from channel name
    progress("Reading existing intensity channels")
    channel_base_stems = list(metadata.get("channel_base_stems", []))
    intensity_channels: list[IntensityChannel] = []
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

    # Read + stitch + build provenance per channel
    layers: dict[str, np.ndarray] = {}
    provenance: dict[str, ProvenanceRecord] = {}
    selected_rule_name = type(cross_format_rule).__name__

    for ch_name, bindings in by_channel.items():
        progress(f"Reading {ch_name} ({len(bindings)} tile(s))")
        try:
            decay = _read_and_stitch_decay(
                bindings, tile_config, flim_config, token_config,
            )
        except Exception as e:  # noqa: BLE001
            errors[ch_name] = f"read/stitch failed: {e}"
            continue
        if rotate_k:
            # np.rot90 rotates the (axis0, axis1) plane CCW by k*90°. For a
            # decay volume of shape (H, W, T), this rotates the spatial plane
            # while leaving T untouched.
            decay = np.rot90(decay, k=int(rotate_k) % 4, axes=(0, 1))
            decay = np.ascontiguousarray(decay)  # rotation views are non-contiguous
        layers[ch_name] = decay

        # Use first binding for provenance source path + evidence; if multiple
        # tiles, we capture the first as the canonical "source" (rest implied).
        first_binding = bindings[0]
        provenance[ch_name] = ProvenanceRecord(
            source_path=str(first_binding.bin_path.resolve()),
            cross_format_rule=selected_rule_name,
            match_evidence=json.dumps(first_binding.evidence.to_dict()),
            manually_overridden=isinstance(cross_format_rule, ExplicitRule),
            importer_version=_percell4_version,
            timestamp_utc=datetime.now(UTC).isoformat(),
            content_sha256=_sha256_file(first_binding.bin_path),
        )

    # Append
    if layers:
        progress(f"Appending {len(layers)} decay layer(s) to {h5_path.name}")
        try:
            store.append_decay_layers(
                layers=layers,
                provenance=provenance,
                cross_format_rule=cross_format_rule,
                force=force,
            )
        except Exception as e:  # noqa: BLE001
            errors["append"] = str(e)
            return AppendReport(
                bindings=match_result.bindings,
                unmatched=match_result.unmatched,
                ambiguous=match_result.ambiguous,
                errors=errors,
            )

    return AppendReport(
        written=tuple(layers.keys()),
        bindings=match_result.bindings,
        unmatched=match_result.unmatched,
        ambiguous=match_result.ambiguous,
        errors=errors,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


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
