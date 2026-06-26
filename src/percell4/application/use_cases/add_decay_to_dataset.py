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
from percell4.domain.io.assembler import _tile_positions, canvas_from_offsets
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
from percell4.domain.io.timepoints import ordered_timepoint_tokens
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
    # Read registered overlap-stitch geometry FRESH from the store (never a
    # cached handle.metadata snapshot — same staleness-safe pattern as the
    # native_shape read below). When the dataset was never registered (or
    # predates the feature) this reads back ``registered=False`` /
    # ``offsets=None`` and the existing grid path runs unchanged (R6/back-compat).
    geom = store.read_stitch_geometry()
    # AE4: a dataset flagged registered but missing its persisted offsets is a
    # dataset-level corruption — refuse the whole append rather than silently
    # falling back to grid placement (which would mis-align /decay against the
    # registered /intensity). This is dataset-wide, so it raises before the
    # per-channel loop (never swallowed into a per-channel report error).
    if geom.registered and geom.offsets is None:
        raise ValueError(
            "dataset is flagged stitch_registered=True but stitch/tile_offsets "
            "is absent; cannot place decay at the registered geometry. "
            "Re-import via the Compress dialog to a fresh output path."
        )
    # The complementary corruption (FIX F): the offset array is present but the
    # commit marker is absent — a partial/aborted registered import. Placing
    # decay on either path (grid or registered) would risk mis-aligning against
    # an /intensity that may itself be half-written. Refuse here too, before the
    # per-channel loop, so the per-channel except can't swallow it.
    if geom.offsets is not None and not geom.registered:
        raise ValueError(
            "stitch/tile_offsets present but stitch_registered is False — "
            "partial/aborted registered import; re-import to a fresh path."
        )
    channel_names = list(metadata.get("channel_names", []))
    if not channel_names:
        return AppendReport(
            errors={"intensity": "no /intensity channels in dataset (channel_names empty)"}
        )

    # Acquisition-timepoint count (intensity-derived, authoritative). A
    # time-lapse dataset (n_timepoints > 1) gets a 4-D (T_acq, H, W, T_bins)
    # /decay, one .bin set per timepoint bound by its _t<N> token — see the
    # per-timepoint binding in Phase 1. Single-timepoint stays 3-D, unchanged.
    n_timepoints = int(metadata.get("n_timepoints", 1) or 1)

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
            # ── Per-timepoint binding ───────────────────────────────────
            # Group this channel's bindings by acquisition timepoint (one .bin
            # set per timepoint; single-padded, 1-based _t<N> tokens). Numeric-
            # sorted tokens map positionally to frame index 0..N-1 (so _t1 ->
            # frame 0). Single-timepoint datasets keep one frame (token absent).
            tp_groups: dict[str | None, list] = {}
            for b in bindings:
                tok = _extract_timepoint_token(b.bin_path.stem, token_config)
                tp_groups.setdefault(tok, []).append(b)

            if n_timepoints > 1:
                tokens = [t for t in tp_groups if t is not None]
                ordered = ordered_timepoint_tokens(tokens)
                if len(ordered) != n_timepoints:
                    raise ValueError(
                        f"found {len(ordered)} decay timepoint(s) {ordered} "
                        f"but the dataset has {n_timepoints} timepoints; one "
                        ".bin set per timepoint is required (no partial "
                        "time-lapse decay)."
                    )
                frames = [
                    (t_acq, _build_tile_map(tp_groups[tok], token_config))
                    for t_acq, tok in enumerate(ordered)
                ]
            else:
                all_bindings = [b for bs in tp_groups.values() for b in bs]
                frames = [(0, _build_tile_map(all_bindings, token_config))]

            # Dimensions + placement are computed ONCE from frame 0 and reused
            # verbatim for every timepoint (single persisted geometry, zero
            # stage drift) so /intensity[t] and /decay[t] resolve every overlap
            # pixel to the same tile.
            first_ttp = frames[0][1]
            first_result = read_flim_bin(next(iter(first_ttp.values())), **bin_dims)
            tile_h, tile_w, n_bins = first_result["array"].shape
            del first_result

            # ── Placement: registered (overlap-aware) vs grid ──────────
            # Registered: reuse the persisted offsets VERBATIM (never recompute,
            # R5/R6/AE2); rotate_k is applied PER TILE so the mosaic lands at
            # native_shape directly (odd rotate_k transposes each square tile, so
            # the canvas uses the POST-rotation tile shape). Grid: the fixed
            # edge-to-edge path, whole-image-rotated in Phase 2 (back-compat).
            if geom.registered:
                offsets = np.asarray(geom.offsets)
                if (int(rotate_k) % 2) == 1:
                    placed_h, placed_w = tile_w, tile_h
                else:
                    placed_h, placed_w = tile_h, tile_w
                out_h, out_w = canvas_from_offsets(offsets, (placed_h, placed_w))
                use_tiling = True
                positions = {}
            else:
                offsets = None
                placed_h, placed_w = tile_h, tile_w
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

            # Source-shape validation (once): the FINAL stored (H, W) must equal
            # /metadata.native_shape. Registered -> per-tile rotate, compare
            # directly; grid -> Phase-2 whole-image rotate, odd rotate_k
            # transposes (H, W). flip_axis is shape-invariant.
            native_shape = metadata.get("native_shape")
            if native_shape is not None:
                if geom.registered or (int(rotate_k) % 2) == 0:
                    final_h, final_w = out_h, out_w
                else:
                    final_h, final_w = out_w, out_h
                if tuple(native_shape) != (final_h, final_w):
                    if geom.registered:
                        raise LayerSizeMismatchError(
                            f"Registered decay canvas ({out_h}, {out_w}) from the "
                            f"persisted tile offsets (rotate_k={rotate_k} applied "
                            f"per tile) does not match dataset native_shape "
                            f"{tuple(native_shape)}. The registered /intensity "
                            "geometry disagrees with native_shape — re-import "
                            "/intensity and /decay together so they share one "
                            "canvas."
                        )
                    raise LayerSizeMismatchError(
                        f"Source TCSPC stitched shape is ({out_h}, {out_w})"
                        + (
                            f" -> ({final_h}, {final_w}) after rotate_k={rotate_k}"
                            if rotate_k
                            else ""
                        )
                        + f"; dataset native_shape is {tuple(native_shape)}. "
                        "Re-import via Compress dialog with the matching "
                        "creation_bin, or pre-bin the .bin files externally."
                    )

            # Write each acquisition frame through the shared streamer with the
            # SAME geometry. n_timepoints > 1 -> 4-D (T_acq, H, W, T_bins);
            # n_timepoints == 1 -> legacy 3-D (byte-identical).
            for t_acq, ttp in frames:
                if geom.registered:
                    # COMPLETENESS (FIX C): each frame must cover EVERY
                    # registered tile, else its empty regions / mis-resolved
                    # overlaps would misalign /decay[t] from /intensity[t].
                    expected = set(range(len(offsets)))
                    present = set(ttp)
                    if present != expected:
                        raise ValueError(
                            f"registered decay append requires all "
                            f"{len(offsets)} tiles per timepoint; frame {t_acq} "
                            f"got {len(present)} (missing "
                            f"{sorted(expected - present)}, extra "
                            f"{sorted(present - expected)})."
                        )
                    pixel_offsets: dict[int, tuple[int, int]] | None = {
                        idx: (int(offsets[idx][0]), int(offsets[idx][1]))
                        for idx in ttp
                    }
                else:
                    pixel_offsets = None
                write_decay_streaming(
                    h5_path=h5_path,
                    channel_name=ch_name,
                    tile_bins=ttp,
                    bin_dims=bin_dims,
                    tile_h=placed_h,
                    tile_w=placed_w,
                    n_bins=n_bins,
                    out_h=out_h,
                    out_w=out_w,
                    positions=positions,
                    use_tiling=use_tiling,
                    pixel_offsets=pixel_offsets,
                    disconnected=geom.disconnected if geom.registered else (),
                    tile_rotate_k=int(rotate_k) if geom.registered else 0,
                    tile_flip_axis=flip_axis if geom.registered else None,
                    n_acq=n_timepoints,
                    timepoint=t_acq,
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
    # GRID PATH ONLY: the registered path already applied rotate_k PER TILE
    # before placement (a whole-image rotate would transpose the registered
    # mosaic off native_shape — the overlap shape-mismatch bug).
    if rotate_k and not geom.registered:
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
    # — /intensity is never touched. GRID PATH ONLY (the registered path
    # flipped each tile before placement).
    if flip_axis is not None and not geom.registered:
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
        # 4-D time-lapse decay (T_acq, H, W, T_bins): the spatial H/W axes are at
        # [1, 2]; legacy 3-D (H, W, T_bins) at [0, 1]. flip_axis is 0=H, 1=W.
        flip_ax = axis + 1 if arr.ndim == 4 else axis
        arr = np.flip(arr, axis=flip_ax)
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
        # 4-D time-lapse decay rotates its spatial plane at axes [1, 2]; legacy
        # 3-D at [0, 1]. T_acq (leading) and T_bins (trailing) are preserved.
        spatial = (1, 2) if arr.ndim == 4 else (0, 1)
        arr = np.rot90(arr, k=k, axes=spatial)
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


def _extract_timepoint_token(stem: str, token_config: TokenConfig) -> str | None:
    """The timepoint token (e.g. ``"1"`` from ``_t1``) of a .bin stem, or None.

    Single-padded, 1-based tokens are returned verbatim; numeric ordering and the
    0-based frame-index mapping are done by the caller via
    ``ordered_timepoint_tokens`` (positional), so ``_t1`` -> frame 0.
    """
    if not token_config.timepoint:
        return None
    m = re.search(token_config.timepoint, stem)
    return m.group(1) if m else None


def _build_tile_map(bindings, token_config: TokenConfig) -> dict[int, Path]:
    """Map normalized 0-based tile index -> .bin Path for one frame's bindings.

    Mirrors the compress path: extract the ``_s<idx>`` tile token, then shift the
    minimum index to 0 so single-padded 1-based tile tokens align with the
    persisted offsets / grid positions.
    """
    tile_to_path: dict[int, Path] = {}
    for b in bindings:
        idx = _extract_tile_index(b.bin_path.stem, token_config)
        if idx is None:
            raise ValueError(
                f"tile token missing from {b.bin_path.name}; cannot stitch"
            )
        tile_to_path[idx] = b.bin_path
    if tile_to_path:
        min_idx = min(tile_to_path)
        if min_idx > 0:
            tile_to_path = {k - min_idx: v for k, v in tile_to_path.items()}
    return tile_to_path


def _sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes. Pragmatic single-pass read."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
