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

    # Time-lapse FLIM guard (U20): /decay has no acquisition-time axis, so
    # appending TCSPC decay to a multi-timepoint dataset would silently stitch a
    # single volume that mis-aligns with the time-stacked intensity. Surface the
    # limitation instead of collapsing it. Full time-lapse FLIM is deferred.
    n_timepoints = int(metadata.get("n_timepoints", 1) or 1)
    if n_timepoints > 1:
        return AppendReport(
            errors={
                "decay": (
                    f"Time-lapse FLIM is not yet supported: this dataset has "
                    f"{n_timepoints} timepoints and /decay has no acquisition-time "
                    "axis. Appending TCSPC .bin decay to a time-lapse dataset is "
                    "deferred."
                )
            }
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

            # ── Placement: registered (overlap-aware) vs grid ──────────
            # When the dataset carries registered geometry, reuse the
            # persisted per-tile pixel offsets VERBATIM — never recompute
            # registration here (R5/R6/AE2). Otherwise the existing fixed
            # edge-to-edge grid path runs unchanged (back-compat, AE3).
            if geom.registered:
                # offsets-present is guaranteed here (AE4 already raised above
                # when registered-but-absent). Build pixel_offsets keyed by the
                # SAME 0-based normalized tile index used for tile_to_path
                # (offsets[index] is the persisted (y0, x0) top-left corner).
                offsets = np.asarray(geom.offsets)
                # COMPLETENESS (FIX C): a registered append must cover EVERY
                # registered tile. The canvas (out_h/out_w) is derived from the
                # full persisted geometry, so a partial set of tiles would
                # leave registered regions empty AND mis-resolve overlaps the
                # import placed against the missing tiles — silently misaligning
                # /decay from /intensity. Partial registered decay is unsupported.
                expected = set(range(len(offsets)))
                present = set(tile_to_path)
                if present != expected:
                    raise ValueError(
                        f"registered decay append requires all {len(offsets)} "
                        f"tiles; got {len(present)} "
                        f"(missing {sorted(expected - present)}, "
                        f"extra {sorted(present - expected)})."
                    )
                pixel_offsets: dict[int, tuple[int, int]] = {
                    idx: (int(offsets[idx][0]), int(offsets[idx][1]))
                    for idx in tile_to_path
                }
                # The LASX orientation fix (rotate_k/flip) is applied PER TILE on
                # the registered path — the persisted offsets live in the
                # /intensity frame, so each .bin tile is oriented to match its
                # .tiff counterpart BEFORE placement and the assembled /decay
                # reproduces native_shape directly. A whole-image rotation
                # (correct only for the regular grid path) would instead
                # transpose the registered mosaic off native_shape. An odd
                # rotate_k transposes each tile, so the canvas is derived from
                # the POST-rotation tile shape (a no-op for square tiles).
                if (int(rotate_k) % 2) == 1:
                    placed_h, placed_w = tile_w, tile_h
                else:
                    placed_h, placed_w = tile_h, tile_w
                out_h, out_w = canvas_from_offsets(offsets, (placed_h, placed_w))
                use_tiling = True
                positions = {}
            else:
                placed_h, placed_w = tile_h, tile_w
                pixel_offsets = None
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

            # Source-shape validation: the FINAL stored (H, W) must equal
            # /metadata.native_shape.
            #  - Registered path: rotate_k is applied PER TILE, so (out_h, out_w)
            #    is already the final, native-oriented canvas — compare directly.
            #  - Grid path: Phase 2 below whole-image-rotates each stitched
            #    /decay, so odd ``rotate_k`` transposes (H, W); the check uses
            #    the POST-rotation shape so the canonical "stitch + rotate to fit
            #    native" workflow (e.g. .bin tiles stitched at (2048, 2560) with
            #    rotate_k=1 to land at native (2560, 2048)) isn't rejected.
            # flip_axis is shape-invariant -- only rotate_k matters here.
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

            # Delegate the actual decay write to the shared helper --
            # compress and add-layer go through the SAME streaming write.
            # spatial_bin defaults to 1; only the importer's compress
            # path passes a larger value (see U7).
            write_decay_streaming(
                h5_path=h5_path,
                channel_name=ch_name,
                tile_bins=tile_to_path,
                bin_dims=bin_dims,
                # POST-rotation tile shape on the registered path (== tile_h/w
                # for square tiles or rotate_k=0); the streamer rotates each
                # tile to this shape before placement.
                tile_h=placed_h,
                tile_w=placed_w,
                n_bins=n_bins,
                out_h=out_h,
                out_w=out_w,
                positions=positions,
                use_tiling=use_tiling,
                pixel_offsets=pixel_offsets,
                # Thread the persisted disconnected set through (FIX C) so the
                # append reproduces the import's overlap winner EXACTLY: any
                # tile the solve demoted is placed first (lowest priority) here
                # too. ``()`` on the grid path (geom.disconnected is empty).
                disconnected=geom.disconnected if geom.registered else (),
                # Registered path corrects LASX orientation PER TILE (before
                # placement); the grid path defers to the Phase-2 whole-image
                # rotate/flip below, so it passes the no-op identity here.
                tile_rotate_k=int(rotate_k) if geom.registered else 0,
                tile_flip_axis=flip_axis if geom.registered else None,
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
