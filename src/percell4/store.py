"""HDF5-based dataset storage for PerCell4.

Each dataset is a single .h5 file containing images, labels, masks,
measurements, and metadata. DatasetStore provides read/write access
with crash-safe per-operation file handling for writes and an optional
session mode for efficient repeated reads.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import h5py
import hdf5plugin  # noqa: F401 — registers the Blosc filter (read + write)
import numpy as np
import pandas as pd
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

from percell4.domain.io.cross_format import deserialize_rule, serialize_rule
from percell4.domain.io.models import (
    CrossFormatRule,
    ExplicitRule,
    ProvenanceRecord,
    StitchProvenanceRecord,
)
from percell4.domain.io.view_bin import (
    majority_vote_mask,
    mean_bin_2d,
    mode_labels,
    sum_bin_2d,
    sum_bin_decay,
)

# Chunk cache size for session reads (64 MB)
_READ_CACHE_BYTES = 64 * 1024 * 1024


def _apply_view_bin(hdf5_path: str, arr: NDArray, view_bin: int) -> NDArray:
    """Dispatch view-bin downsampling by HDF5 path prefix.

    Pure function; takes the already-materialized array. See the table on
    :meth:`DatasetStore.read_array` for the per-prefix rule.
    """
    if view_bin == 1:
        return arr
    # Normalize the path (drop leading slash for consistent prefix checks).
    p = hdf5_path.lstrip("/")
    if p == "intensity" or p.startswith("intensity/"):
        return sum_bin_2d(arr, view_bin)
    if p.startswith("decay/"):
        return sum_bin_decay(arr, view_bin)
    if p.startswith("labels/"):
        return mode_labels(arr, view_bin)
    if p.startswith("masks/"):
        return majority_vote_mask(arr, view_bin)
    if p.startswith("phasor/"):
        # Every leaf under /phasor/<ch>/ is intensive (g, s, lifetime, and
        # their *_filtered counterparts). Mean-bin so magnitudes don't
        # scale with k.
        return mean_bin_2d(arr, view_bin)
    # Unknown path -- pass through. Callers asking for a view bin on a
    # path with no defined rule get raw data; safer than guessing.
    return arr


def _infer_bin_metadata(f: h5py.File) -> dict[str, Any]:
    """Return ``{"native_shape": ..., "creation_bin": ...}`` inferred from
    an open HDF5 file's array contents.

    ``native_shape`` is the last two dims of ``/intensity`` if it exists,
    else the first two dims of the first ``/decay/<ch>`` array, else
    ``None``. ``creation_bin`` defaults to ``1`` when absent from
    ``/metadata.attrs``.

    Pure read of the open file handle -- does not mutate or close.
    """
    native_shape: tuple[int, int] | None = None
    n_timepoints = 1
    if "intensity" in f:
        ds = f["intensity"]
        shape = ds.shape
        if len(shape) >= 2:
            native_shape = (int(shape[-2]), int(shape[-1]))
        # A leading "T" in the dims attr marks a time-lapse stack; its
        # length is the timepoint count. Absent/2D => single timepoint.
        dims = ds.attrs.get("dims")
        if dims is not None and len(dims) > 0 and str(dims[0]) == "T":
            n_timepoints = int(shape[0])
    elif "decay" in f:
        decay_grp = f["decay"]
        children = list(decay_grp.keys())
        if children:
            first = decay_grp[children[0]]
            shape = first.shape
            if len(shape) >= 2:
                native_shape = (int(shape[0]), int(shape[1]))
    creation_bin = 1
    if "metadata" in f and "creation_bin" in f["metadata"].attrs:
        creation_bin = int(f["metadata"].attrs["creation_bin"])
    return {
        "native_shape": native_shape,
        "creation_bin": creation_bin,
        "n_timepoints": n_timepoints,
    }


# Provenance-attribute keys for masks captured by "Apply Current Phasor
# as Mask". Single source of truth so future readers cannot drift from
# the writer in main_window.py.
PHASOR_MASK_ATTR_INTENSITY_THRESHOLD = "phasor_intensity_threshold"
PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_G = "phasor_ref_circle_center_g"
PHASOR_MASK_ATTR_REF_CIRCLE_CENTER_S = "phasor_ref_circle_center_s"
PHASOR_MASK_ATTR_REF_CIRCLE_RADIUS = "phasor_ref_circle_radius"
PHASOR_MASK_ATTR_ACTIVE_MASK = "phasor_active_mask_at_capture"
PHASOR_MASK_ATTR_CLEARED_PIXEL_COUNT = "phasor_cleared_pixel_count"
PHASOR_MASK_ATTR_ACTIVE_CHANNEL = "phasor_active_channel"
PHASOR_MASK_ATTR_CAPTURE_ISO = "phasor_capture_iso8601"


class LayerAlreadyExistsError(Exception):
    """Raised when a payload group already exists and force=False."""


class MetadataConsistencyError(Exception):
    """Raised when /metadata.native_shape disagrees with on-disk array shape.

    The dataset-wide spatial-binning model treats ``/metadata.native_shape``
    as the authoritative native resolution. If a stored value disagrees
    with what we can infer from ``/intensity`` (or ``/decay/<first_ch>``
    when intensity is absent), we refuse to silently overwrite -- a real
    schema bug or a corrupted file is more likely than a benign
    transient. Callers must inspect and decide.
    """


class LayerSizeMismatchError(Exception):
    """Raised when an Add-Layer source shape doesn't equal /metadata.native_shape.

    The dataset-wide binning model locks the dataset's native shape at
    compress time. Subsequent layers added through the Add-Layer path
    (Add-Layer dialog TIFFs, TCSPC append, ROI imports) must match
    exactly -- the .h5 doesn't tolerate mixed-resolution storage.
    Mismatches are the user's signal that they need to either pre-bin
    the source externally or re-compress with a different creation_bin.
    """


class SourceShapeMismatchError(Exception):
    """Raised at compress time when source channels disagree on (H, W).

    The dataset-wide binning model requires all source files in one
    compress operation to share the same pre-bin (H, W); native_shape
    is computed as (H // creation_bin, W // creation_bin) from that
    agreed shape. If one source TIFF or .bin is at a different
    resolution than its peers, the run aborts before writing -- silent
    partial imports would corrupt the native_shape invariant.
    """


class CrossFormatRuleConflictError(Exception):
    """Raised when an append would persist a rule different from one already stored."""


class DimsConsistencyError(Exception):
    """Raised when /intensity's ``dims`` attr disagrees with its shape + counts.

    The canonical corruption: an older Add-Layer Channel write stamped
    ``dims=['C','H','W']`` onto a ``(T, H, W)`` time-lapse array, so the dataset
    reads back as ``n_timepoints == 1`` and every time-aware feature silently
    collapses to frame 0 (the exact silent-collapse the multi-timepoint work
    exists to eliminate). We refuse to open such a dataset silently. Also raised
    when the ``dims`` attribute length doesn't match the array rank.
    """


# Backwards-compat aliases — the names without the Error suffix were used in
# the first round of tests. Keep them around so callers writing
# ``from percell4.store import LayerAlreadyExists`` still work.
LayerAlreadyExists = LayerAlreadyExistsError
CrossFormatRuleConflict = CrossFormatRuleConflictError


@dataclass(frozen=True)
class StitchGeometry:
    """Registered overlap-stitch geometry read back from a dataset.

    ``registered`` is the commit marker (``/metadata.stitch_registered``).
    When a dataset was never registered (or predates the feature), the
    ``stitch/tile_offsets`` dataset is absent and this reads back as
    ``registered=False`` with ``offsets=None`` — the back-compat / grid
    path signal for consumers (R6/AE4).

    ``offsets`` is the ``(N, 2) int32`` array of per-tile ``(y0, x0)``
    top-left corners, with per-axis min exactly 0 (asserted on both write
    and read) so the canvas derivation is unconditionally
    ``max(offset + extent)``.

    ``disconnected`` is the persisted set of tile indices that the import
    solve left at their grid seed (lowest overwrite priority). A later
    decay-only append threads this back through ``write_decay_streaming`` so
    the append reproduces the import's overlap winner exactly. Defaults to
    ``()`` for back-compat with files written before it was persisted.
    """

    registered: bool
    offsets: NDArray | None
    reference_channel: str | None
    overlap: float | None
    disconnected: tuple[int, ...] = ()


def _choose_chunks(shape: tuple[int, ...], is_decay: bool = False) -> tuple[int, ...]:
    """Choose HDF5 chunk shape based on array dimensions.

    - 2D spatial: (256, 256) or smaller if image is small
    - 3D+ with TCSPC: (64, 64, N_bins) — keep full time axis per chunk
    - Other 3D+: (1, 256, 256) — one plane at a time
    """
    ndim = len(shape)
    if ndim == 2:
        return (min(256, shape[0]), min(256, shape[1]))
    if ndim >= 3 and is_decay:
        # TCSPC: spatial chunks of 64x64, full time axis
        return (min(64, shape[0]), min(64, shape[1])) + shape[2:]
    if ndim >= 3:
        # Default: one plane at a time for leading dims
        chunks = [1] * ndim
        chunks[-2] = min(256, shape[-2])
        chunks[-1] = min(256, shape[-1])
        return tuple(chunks)
    return None  # let h5py auto-chunk


def _compression_kwargs(is_decay: bool = False) -> dict[str, Any]:
    """Return compression keyword arguments for dataset creation.

    Images/labels/masks use **Blosc (zstd, clevel 5, bitshuffle)**: measured
    on real intensity it matches gzip-4+shuffle's ratio (~3.4x, no size penalty
    on large files) while decoding ~3x faster — which compounds with the
    parallel decode on load. TCSPC decay keeps ``lzf`` (the existing fast,
    lighter choice for those large per-pixel stacks).

    Note: Blosc and lzf are h5py/registered-filter codecs (not the universal
    gzip). PerCell4 imports ``hdf5plugin`` wherever it reads HDF5 (``store``,
    ``parallel_decode``), so every read path has the Blosc filter registered.
    Existing gzip files keep reading unchanged (gzip is always available).
    """
    if is_decay:
        return {"compression": "lzf"}
    return dict(
        hdf5plugin.Blosc(
            cname="zstd", clevel=5, shuffle=hdf5plugin.Blosc.BITSHUFFLE
        )
    )


class DatasetStore:
    """Read/write interface for a single .h5 dataset file.

    Writes open/close the file per operation (crash-safe).
    Reads can use per-operation mode or a session context manager
    for efficient repeated access with a large chunk cache.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._session_file: h5py.File | None = None

    # ── Session mode for reads ────────────────────────────────

    @contextmanager
    def open_read(self):
        """Context manager for efficient repeated reads.

        Keeps the file open with a large chunk cache. Use for interactive
        sessions where multiple reads happen in quick succession::

            with store.open_read() as s:
                intensity = s.read_array("intensity")
                labels = s.read_labels("cellpose")
        """
        self._session_file = h5py.File(
            self.path, "r", rdcc_nbytes=_READ_CACHE_BYTES
        )
        try:
            yield self
        finally:
            if self._session_file is not None:
                self._session_file.close()
            self._session_file = None

    def _open_read(self) -> h5py.File:
        """Get a file handle for reading (session or per-operation)."""
        if self._session_file is not None:
            return self._session_file
        return h5py.File(self.path, "r")

    def _close_if_not_session(self, f: h5py.File) -> None:
        """Close the file handle if not in session mode."""
        if f is not self._session_file:
            f.close()

    # ── Generic write operations ──────────────────────────────

    def write_array(
        self,
        hdf5_path: str,
        array: NDArray,
        attrs: dict[str, Any] | None = None,
        is_decay: bool = False,
    ) -> int:
        """Write a numpy array to the specified HDF5 path.

        Returns the number of elements written.
        """
        with h5py.File(self.path, "a") as f:
            if hdf5_path in f:
                del f[hdf5_path]
            chunks = _choose_chunks(array.shape, is_decay=is_decay)
            f.create_dataset(
                hdf5_path,
                data=array,
                chunks=chunks,
                **_compression_kwargs(is_decay=is_decay),
            )
            # Store dimension names if provided in attrs
            if attrs:
                for key, val in attrs.items():
                    f[hdf5_path].attrs[key] = val
        return array.size

    def read_array(self, hdf5_path: str, view_bin: int = 1) -> NDArray:
        """Read a numpy array from the specified HDF5 path.

        ``view_bin`` is the session-level view bin (k >= 1). At k=1 the
        array is returned byte-identical to what was written. At k>1 the
        array is downsampled in-memory by the rule appropriate for its
        path:

        ===================================== =====================
        Path prefix                            Rule
        ===================================== =====================
        ``/intensity`` (any rank)              ``sum_bin_2d``
        ``/decay/<ch>``                        ``sum_bin_decay``
        ``/labels/<name>``                     ``mode_labels``
        ``/masks/<name>``                      ``majority_vote_mask``
        ``/phasor/<ch>/{g,s,*_filtered,...}``  ``mean_bin_2d`` (intensive)
        anything else                          pass-through
        ===================================== =====================

        See ``src/percell4/domain/io/view_bin.py`` for the full per-rule
        contract. The on-disk array is unchanged -- this is a read-time
        view only.
        """
        if view_bin < 1:
            raise ValueError(f"view_bin must be >= 1, got {view_bin}")
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{hdf5_path} is a group, not a dataset")
            arr = obj[()]
            if view_bin == 1:
                return arr
            return _apply_view_bin(hdf5_path, arr, view_bin)
        finally:
            self._close_if_not_session(f)

    def read_decay(self, channel: str, view_bin: int = 1) -> NDArray:
        """Read ``/decay/<channel>`` with optional view-bin downsampling.

        Sugar over :meth:`read_array` that exists so callers don't have
        to build the ``f"decay/{channel}"`` path themselves -- and so a
        single grep for ``read_decay`` enumerates every decay-read
        callsite (closes the Single-Write-Boundary gap where decay reads
        used to open ``h5py.File`` directly).
        """
        return self.read_array(f"decay/{channel}", view_bin=view_bin)

    # ── Stitch geometry (registered overlap mosaics) ──────────

    def write_stitch_geometry(
        self,
        offsets: NDArray,
        provenance: StitchProvenanceRecord,
        *,
        reference_channel: str,
        overlap: float,
        disconnected: tuple[int, ...] = (),
    ) -> None:
        """Persist registered overlap-stitch geometry through the store boundary.

        Writes three things, with the commit marker written **strictly
        last** so a crash mid-write leaves an un-registered (recoverable)
        file rather than a half-registered brick:

        1. ``stitch/tile_offsets`` -- the ``(N, 2) int32`` per-tile
           ``(y0, x0)`` array, via :meth:`write_array` (pass-through
           view-bin, lossless, no ``is_decay``/``dims`` requirement).
        2. ``/provenance/stitch`` group attrs from ``provenance.to_attrs()``
           (mirrors how decay provenance is written -- group attrs).
        3. ``/metadata`` attrs ``stitch_reference_channel`` (str),
           ``stitch_overlap`` (float), and ``stitch_disconnected`` (a
           JSON-encoded list of int tile indices), then
           ``stitch_registered = True`` **last** (the commit point,
           R4/U6 ordering).

        ``disconnected`` is the set of tile indices the solve left at their
        grid seed; it is persisted so a later decay-only append can reproduce
        the import's overlap winner exactly.

        ``offsets`` must have per-axis min exactly 0 (the canvas
        derivation depends on it); this is enforced before any write.
        """
        import json

        offsets = np.asarray(offsets, dtype=np.int32)
        if offsets.ndim != 2 or offsets.shape[1] != 2:
            raise ValueError(
                f"offsets must be (N, 2); got shape {offsets.shape}"
            )
        if offsets.size:
            mins = offsets.min(axis=0)
            # Data invariant (the canvas derivation depends on min==0) — a
            # ``raise``, not an ``assert``, so it survives ``python -O``.
            if not (int(mins[0]) == 0 and int(mins[1]) == 0):
                raise ValueError(
                    f"offsets per-axis min must be 0; "
                    f"got {tuple(int(m) for m in mins)}"
                )

        # 1. Offset array (own write boundary).
        self.write_array("stitch/tile_offsets", offsets)

        # 2. Provenance group attrs (mirror /provenance/decay/<ch>).
        with h5py.File(self.path, "a") as f:
            prov_path = "provenance/stitch"
            if prov_path in f:
                del f[prov_path]
            grp = f.require_group(prov_path)
            for key, val in provenance.to_attrs().items():
                grp.attrs[key] = val

        # 3. Metadata scalars, with the commit marker STRICTLY LAST.
        self.set_metadata(
            {
                "stitch_reference_channel": str(reference_channel),
                "stitch_overlap": float(overlap),
                "stitch_disconnected": json.dumps(
                    [int(i) for i in disconnected]
                ),
            }
        )
        self.set_metadata({"stitch_registered": True})

    def read_stitch_geometry(self) -> StitchGeometry:
        """Read registered overlap-stitch geometry back from the dataset.

        The ``registered`` flag is read **first** from ``/metadata``, fully
        independent of whether ``stitch/tile_offsets`` is present, so the two
        corrupt states the consumers guard against are observable rather than
        silently collapsed to ``registered=False``:

        * flag True + offsets absent → ``registered=True, offsets=None``
          (AE4: a crash after the commit marker but before — or after losing —
          the offset array; the consumer refuses the append).
        * offsets present + flag absent → ``registered=False`` with offsets
          present (a partial/aborted registered import; the consumer refuses).

        Flags are read fresh on every call (never cached on the store) so an
        in-session write is always observed. ``stitch_disconnected`` (JSON list
        of ints) round-trips here, defaulting to ``()``.

        Enforces (via ``raise``, ``-O``-safe) that read-back offsets have
        per-axis min 0 — the same invariant enforced on write.
        """
        import json

        meta = self.metadata
        registered_flag = bool(meta.get("stitch_registered", False))
        disconnected_raw = meta.get("stitch_disconnected")
        if disconnected_raw is None:
            disconnected: tuple[int, ...] = ()
        else:
            disconnected = tuple(int(i) for i in json.loads(disconnected_raw))

        try:
            offsets = self.read_array("stitch/tile_offsets")
        except KeyError:
            offsets = None

        if offsets is not None and offsets.size:
            mins = offsets.min(axis=0)
            if not (int(mins[0]) == 0 and int(mins[1]) == 0):
                raise ValueError(
                    f"persisted offsets per-axis min must be 0; "
                    f"got {tuple(int(m) for m in mins)}"
                )
        return StitchGeometry(
            registered=registered_flag,
            offsets=offsets,
            reference_channel=meta.get("stitch_reference_channel"),
            overlap=meta.get("stitch_overlap"),
            disconnected=disconnected,
        )

    def read_channel(
        self,
        hdf5_path: str,
        channel_idx: int,
        view_bin: int = 1,
        timepoint: int | None = None,
    ) -> NDArray:
        """Read a single channel plane from a 2D, 3D, or time-stacked array.

        For 2D arrays, ``channel_idx`` must be 0 and the full array is returned.
        For 3D ``(C, H, W)`` arrays, returns only ``array[channel_idx]`` without
        loading the other channels — useful for phases that only need one channel
        on each dataset.

        On a **time-stacked** array (leading ``dims[0] == 'T'``), ``timepoint``
        is **required**: the frame is sliced first, then ``channel_idx`` is
        indexed on the resulting ``(H, W)`` (from ``(T, H, W)``) or ``(C, H, W)``
        (from ``(T, C, H, W)``) slice. This is the canonical fix for the old
        behavior that treated a leading ``T`` axis as channels — returning frame
        0 on ``(T, H, W)`` or raising "got 4D" on ``(T, C, H, W)``. Passing
        ``timepoint`` on a non-time-stacked array is ignored, so 2D / ``(C,H,W)``
        reads stay byte-identical.

        ``view_bin`` follows the same rule as :meth:`read_array`. Only
        ``/intensity`` paths are expected here, so the downsampler is
        ``sum_bin_2d`` (the slice is 2D by the time we apply the bin).
        """
        if view_bin < 1:
            raise ValueError(f"view_bin must be >= 1, got {view_bin}")
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            ds = f[hdf5_path]
            dims = ds.attrs.get("dims")
            is_time_stacked = (
                dims is not None and len(dims) > 0 and str(dims[0]) == "T"
            )
            if is_time_stacked:
                n_t = ds.shape[0]
                if timepoint is None:
                    raise ValueError(
                        f"{hdf5_path} is time-stacked ({n_t} timepoints); "
                        "read_channel requires an explicit timepoint."
                    )
                if not 0 <= timepoint < n_t:
                    raise IndexError(
                        f"timepoint={timepoint} out of range [0, {n_t})"
                    )
                # (T,H,W) -> (H,W); (T,C,H,W) -> (C,H,W)
                frame = ds[timepoint]
                if frame.ndim == 2:
                    if channel_idx != 0:
                        raise IndexError(
                            f"channel_idx={channel_idx} out of range for a "
                            "single-channel time-stacked array"
                        )
                    arr = frame
                else:
                    n_channels = frame.shape[0]
                    if not 0 <= channel_idx < n_channels:
                        raise IndexError(
                            f"channel_idx={channel_idx} out of range "
                            f"[0, {n_channels})"
                        )
                    arr = frame[channel_idx]
            elif ds.ndim == 2:
                if channel_idx != 0:
                    raise IndexError(
                        f"channel_idx={channel_idx} out of range for 2D array"
                    )
                arr = ds[()]
            elif ds.ndim == 3:
                n_channels = ds.shape[0]
                if not 0 <= channel_idx < n_channels:
                    raise IndexError(
                        f"channel_idx={channel_idx} out of range [0, {n_channels})"
                    )
                arr = ds[channel_idx, ...]
            else:
                raise ValueError(
                    f"read_channel expects 2D or 3D array, got {ds.ndim}D at {hdf5_path}"
                )
            return arr if view_bin == 1 else sum_bin_2d(arr, view_bin)
        finally:
            self._close_if_not_session(f)

    def read_array_frame(
        self, hdf5_path: str, timepoint: int, view_bin: int = 1
    ) -> NDArray:
        """Read a single timepoint frame ``arr[timepoint]`` from a leading-T array.

        Slices on disk so only one frame is loaded (cheap given per-frame
        chunking). The path's view-bin rule is applied to the slice, which
        is one rank lower than the stored array (``(T,H,W)`` -> ``(H,W)``;
        ``(T,C,H,W)`` -> ``(C,H,W)``). For a non-time-stacked array,
        ``timepoint`` must be 0 and the whole array is returned.
        """
        if view_bin < 1:
            raise ValueError(f"view_bin must be >= 1, got {view_bin}")
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{hdf5_path} is a group, not a dataset")
            dims = obj.attrs.get("dims")
            is_time_stacked = (
                dims is not None and len(dims) > 0 and str(dims[0]) == "T"
            )
            if not is_time_stacked:
                if timepoint != 0:
                    raise IndexError(
                        f"timepoint={timepoint} out of range: {hdf5_path} is "
                        "not time-stacked"
                    )
                arr = obj[()]
            else:
                n_t = obj.shape[0]
                if not 0 <= timepoint < n_t:
                    raise IndexError(
                        f"timepoint={timepoint} out of range [0, {n_t})"
                    )
                arr = obj[timepoint]
            if view_bin == 1:
                return arr
            return _apply_view_bin(hdf5_path, arr, view_bin)
        finally:
            self._close_if_not_session(f)

    def _is_2d_array(self, hdf5_path: str) -> bool:
        """True when ``hdf5_path`` holds a 2D dataset. Reads shape metadata
        only — no array data is loaded. Raises ``KeyError`` when the path is
        missing or is a group.
        """
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{hdf5_path} is a group, not a dataset")
            return obj.ndim == 2
        finally:
            self._close_if_not_session(f)

    def array_exists(self, hdf5_path: str) -> bool:
        """True when ``hdf5_path`` is a dataset in the file. Metadata only —
        **never decompresses**. Use for existence/enablement checks instead of
        ``try: read_array(...) except`` (which decodes the whole stack).
        """
        f = self._open_read()
        try:
            return hdf5_path in f and isinstance(f[hdf5_path], h5py.Dataset)
        finally:
            self._close_if_not_session(f)

    def read_array_attrs(self, hdf5_path: str) -> dict[str, Any]:
        """Return a dataset's HDF5 attrs as a plain dict. Metadata only —
        **never decompresses** the array. Returns ``{}`` when the path is
        absent or names a group rather than a dataset.

        Used to read writer-stamped scalars on a derived array (e.g. the
        wavelet ``filter_level`` on ``/phasor/<ch>/g_filtered``) without
        decoding the array itself.
        """
        f = self._open_read()
        try:
            if hdf5_path in f and isinstance(f[hdf5_path], h5py.Dataset):
                return dict(f[hdf5_path].attrs)
            return {}
        finally:
            self._close_if_not_session(f)

    def array_shape(self, hdf5_path: str) -> tuple[int, ...]:
        """Return a dataset's on-disk shape. Reads HDF5 metadata only — **no
        array data is decompressed**. Use this instead of
        ``read_array(path).shape`` for display/inventory: on a large stacked
        array the latter decompresses the whole stack (gigabytes) just to read
        a shape tuple. Raises ``KeyError`` when missing or a group.
        """
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{hdf5_path} is a group, not a dataset")
            return tuple(int(x) for x in obj.shape)
        finally:
            self._close_if_not_session(f)

    def array_dtype(self, hdf5_path: str) -> np.dtype:
        """Return a dataset's on-disk dtype. Reads HDF5 metadata only — **no
        array data is decompressed**. Sibling of :meth:`array_shape` for
        display/inventory tools (e.g. the dataset inspector) that need a
        dtype without paying the cost of decoding a multi-gigabyte stack.
        Raises ``KeyError`` when the path is missing or is a group.
        """
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{hdf5_path} is a group, not a dataset")
            return obj.dtype
        finally:
            self._close_if_not_session(f)

    def labels_shape(self, name: str) -> tuple[int, ...]:
        """Return the on-disk shape of ``/labels/<name>`` without loading data.

        Lets callers distinguish a 2D (time-invariant) label from a
        ``(T, H, W)`` stack cheaply — e.g. the workflow's tracking gate,
        which must not try to track a 2D whole-field segmentation.
        """
        f = self._open_read()
        try:
            path = f"labels/{name}"
            if path not in f:
                raise KeyError(f"Dataset not found: {path}")
            obj = f[path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{path} is a group, not a dataset")
            return tuple(int(x) for x in obj.shape)
        finally:
            self._close_if_not_session(f)

    def masks_shape(self, name: str) -> tuple[int, ...]:
        """Return the on-disk shape of /masks/<name> without loading data.

        Mirror of :meth:`labels_shape` for masks -- lets callers distinguish a
        2D (time-invariant) mask from a ``(T, H, W)`` stack cheaply. Raises
        ``KeyError`` when the path is missing or is a group.
        """
        f = self._open_read()
        try:
            path = f"masks/{name}"
            if path not in f:
                raise KeyError(f"Dataset not found: {path}")
            obj = f[path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{path} is a group, not a dataset")
            return tuple(int(x) for x in obj.shape)
        finally:
            self._close_if_not_session(f)

    def is_time_stacked(self, hdf5_path: str) -> bool:
        """Return ``True`` iff the array at ``hdf5_path`` is time-stacked.

        Reads only the ``dims`` attribute (``dims[0] == 'T'``) -- no array data
        is loaded. The public form of the inline check used by
        :meth:`read_array_frame` and :meth:`read_channel`, so callers (e.g. the
        delete-channel disambiguation) stop re-deriving it from raw shape.
        Returns ``False`` for a missing path, a group, or an array with no/short
        ``dims`` attr.
        """
        f = self._open_read()
        try:
            if hdf5_path not in f:
                return False
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                return False
            dims = obj.attrs.get("dims")
            return dims is not None and len(dims) > 0 and str(dims[0]) == "T"
        finally:
            self._close_if_not_session(f)

    def check_intensity_dims_consistency(self) -> None:
        """Raise :class:`DimsConsistencyError` when /intensity's dims are corrupt.

        Detects the Add-Layer corruption signature at dataset-open time: a 3D
        ``/intensity`` stamped ``dims=['C','H','W']`` whose leading-axis size
        disagrees with the declared channel count -- almost certainly a
        ``(T, H, W)`` time-lapse array mis-stamped as channels, which would make
        the dataset read back as single-timepoint and silently collapse every
        time-aware feature to frame 0. Also flags a ``dims`` attr whose length
        doesn't match the array rank.

        No-op when ``/intensity`` is absent, has no ``dims`` attr, or is
        consistent. Reads only attributes + shape (no array data).
        """
        f = self._open_read()
        try:
            if "intensity" not in f:
                return
            ds = f["intensity"]
            if not isinstance(ds, h5py.Dataset):
                return
            raw_dims = ds.attrs.get("dims")
            if raw_dims is None:
                return
            dims = [str(d) for d in raw_dims]
            ndim = int(ds.ndim)
            shape = tuple(int(x) for x in ds.shape)
            raw_channel_names = (
                f["metadata"].attrs.get("channel_names")
                if "metadata" in f
                else None
            )
        finally:
            self._close_if_not_session(f)

        if len(dims) != ndim:
            raise DimsConsistencyError(
                f"/intensity.dims={dims} has {len(dims)} entries but the array "
                f"is {ndim}D (shape {shape}). The dims attribute is corrupt."
            )

        if raw_channel_names is None:
            n_channels: int | None = None
            channel_names: list = []
        elif isinstance(raw_channel_names, (bytes, str)):
            n_channels = 1
            channel_names = [raw_channel_names]
        else:
            channel_names = list(raw_channel_names)
            n_channels = len(channel_names)

        # A leading 'C' axis must match the channel count. When it matches
        # neither (n_channels known and != shape[0]), the leading axis is almost
        # certainly a mis-stamped time axis.
        if (
            ndim >= 3
            and dims[0] == "C"
            and n_channels is not None
            and n_channels > 0
            and shape[0] != n_channels
        ):
            raise DimsConsistencyError(
                f"/intensity has a leading 'C' axis of size {shape[0]} but the "
                f"dataset declares {n_channels} channel(s) ({channel_names!r}). "
                "This looks like a (T,H,W) time-lapse array mis-stamped as "
                "['C','H','W'] (e.g. by an older Add-Layer write); the dataset "
                "would silently read as single-timepoint. Re-import or correct "
                "the /intensity dims attribute."
            )

    # ── DataFrame operations ──────────────────────────────────

    def write_dataframe(self, hdf5_path: str, df: pd.DataFrame) -> int:
        """Write a pandas DataFrame as a CSV string at the given path.

        Returns the number of rows written.
        """
        with h5py.File(self.path, "a") as f:
            if hdf5_path in f:
                del f[hdf5_path]
            csv_str = df.to_csv(index=False)
            f.create_dataset(hdf5_path, data=csv_str)
        return len(df)

    def read_dataframe(self, hdf5_path: str) -> pd.DataFrame:
        """Read a pandas DataFrame from a CSV string at the given path."""
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            csv_bytes = f[hdf5_path][()]
            if isinstance(csv_bytes, bytes):
                csv_str = csv_bytes.decode("utf-8")
            else:
                csv_str = str(csv_bytes)
            return pd.read_csv(StringIO(csv_str))
        finally:
            self._close_if_not_session(f)

    # ── Convenience: labels ───────────────────────────────────

    def _native_shape_and_timepoints(self) -> tuple[tuple[int, int] | None, int]:
        """Return ``(native_shape | None, n_timepoints)`` for write validation.

        Reads ``/metadata`` and infers from ``/intensity``. Returns
        ``(None, 1)`` when the file doesn't exist yet or carries no spatial
        array, so callers can skip the cross-check in that case.
        """
        if not self.path.exists():
            return None, 1
        meta = self.metadata
        ns = meta.get("native_shape")
        if ns is not None:
            ns = tuple(int(x) for x in ns)
        nt = int(meta.get("n_timepoints", 1) or 1)
        return ns, nt

    def _validate_layer_shape(self, array: NDArray, kind: str) -> list[str]:
        """Validate a label/mask array shape and return its ``dims`` attr.

        Accepts 2D ``(H, W)`` (always) or time-stacked ``(T, H, W)`` where
        the trailing two dims equal the dataset's ``native_shape`` and the
        leading axis equals ``n_timepoints``. Anything else raises.
        """
        if array.ndim == 2:
            return ["H", "W"]
        ns, nt = self._native_shape_and_timepoints()
        # A 3D array is a legitimate time stack only on a time-lapse dataset
        # (n_timepoints > 1). On any other dataset — including an empty one
        # whose timepoint count is unknown (defaults to 1) — keep the old
        # "labels/masks must be 2D" contract.
        if array.ndim != 3 or nt <= 1:
            raise ValueError(
                f"{kind} must be 2D (H,W); got {array.ndim}D. A 3D (T,H,W) "
                "stack is only accepted on a time-lapse dataset "
                "(n_timepoints > 1)."
            )
        th, tw = int(array.shape[-2]), int(array.shape[-1])
        if ns is not None and (th, tw) != ns:
            raise LayerSizeMismatchError(
                f"{kind} (T,H,W) trailing dims {(th, tw)} disagree with "
                f"dataset native_shape {ns}."
            )
        if int(array.shape[0]) != nt:
            raise LayerSizeMismatchError(
                f"{kind} has {int(array.shape[0])} frame(s) but the dataset "
                f"has {nt} timepoints; the time axis would mis-stack."
            )
        return ["T", "H", "W"]

    def _write_resource_frame(
        self,
        prefix: str,
        name: str,
        frame: NDArray,
        timepoint: int,
        dtype: Any,
        kind: str,
        whole_writer,
    ) -> int:
        """Splice a single timepoint's 2D ``frame`` into a labels/masks resource.

        Shared core for :meth:`write_labels_frame` / :meth:`write_mask_frame`.
        Three cases (see those methods for the contract):

        1. **Absent** on a time-lapse dataset: allocate a ``(T, H, W)`` zero
           stack and set frame ``timepoint``.
        2. **Present and 2D** (time-invariant) on a time-lapse dataset:
           broadcast the existing plane to ``(T, H, W)``, splice the frame, and
           write the stack. This irreversibly converts a time-invariant gate
           into a per-frame stack, so it is logged (surfaced), not silent.
        3. **Present and ``(T, H, W)``**: assign ``ds[timepoint] = frame`` in
           place under an ``'a'`` open — no delete+recreate, so the other
           frames' bytes are untouched.

        On a single-timepoint dataset (``n_timepoints == 1``) the call is a 2D
        write at ``timepoint == 0``, byte-identical to ``whole_writer``.
        """
        if frame.ndim != 2:
            raise ValueError(f"{kind} frame must be 2D (H,W); got {frame.ndim}D")
        ns, nt = self._native_shape_and_timepoints()
        if not 0 <= timepoint < nt:
            raise IndexError(f"timepoint={timepoint} out of range [0, {nt})")
        if ns is not None and tuple(int(x) for x in frame.shape) != ns:
            raise LayerSizeMismatchError(
                f"{kind} frame shape {tuple(int(x) for x in frame.shape)} "
                f"disagrees with dataset native_shape {ns}."
            )
        frame = frame.astype(dtype, copy=False)
        path = f"{prefix}/{name}"

        # Single-timepoint dataset: a per-frame write at t=0 is just the 2D write.
        if nt <= 1:
            return whole_writer(name, frame)

        present = False
        is_2d = False
        if self.path.exists():
            with h5py.File(self.path, "r") as f:
                present = path in f
                if present:
                    is_2d = f[path].ndim == 2

        h, w = ns if ns is not None else (int(frame.shape[0]), int(frame.shape[1]))

        if not present:
            # Case 1: allocate a (T,H,W) zero stack, splice the frame, validate-write.
            stack = np.zeros((nt, h, w), dtype=dtype)
            stack[timepoint] = frame
            return whole_writer(name, stack)

        if is_2d:
            # Case 2: promote a 2D time-invariant resource to (T,H,W).
            with h5py.File(self.path, "r") as f:
                existing = np.asarray(f[path][()])
            stack = np.broadcast_to(existing, (nt, h, w)).astype(dtype, copy=True)
            stack[timepoint] = frame
            logger.info(
                "Promoting 2D time-invariant %s '%s' to a (T,H,W) per-frame "
                "stack on first per-frame write (timepoint=%d).",
                kind, name, timepoint,
            )
            return whole_writer(name, stack)

        # Case 3: existing (T,H,W) -- in-place single-frame write, no recreate.
        with h5py.File(self.path, "a") as f:
            f[path][timepoint] = frame
        return int(frame.size)

    def write_labels(
        self,
        name: str,
        array: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> int:
        """Write a segmentation label array at /labels/<name>.

        Enforces int32 dtype. Accepts 2D ``(H, W)`` or time-stacked
        ``(T, H, W)`` (validated against the dataset's ``native_shape`` and
        ``n_timepoints``). Returns element count.

        ``attrs`` (optional) are merged onto the canonical ``{"dims": ...}``
        so Phase-6 Creators can stamp ``created_at_bin`` without bypassing
        the chokepoint.
        """
        dims = self._validate_layer_shape(array, "Labels")
        array = array.astype(np.int32, copy=False)
        merged_attrs: dict[str, Any] = {"dims": dims}
        if attrs:
            merged_attrs.update(attrs)
        return self.write_array(f"labels/{name}", array, attrs=merged_attrs)

    def write_labels_frame(
        self, name: str, frame: NDArray, timepoint: int
    ) -> int:
        """Write a single timepoint's 2D ``frame`` into /labels/<name>.

        The symmetric per-frame counterpart to :meth:`read_labels` with a
        ``timepoint``. Lets interactive editors and per-frame Creators persist
        one frame without re-implementing read-splice-write. ``frame`` is a 2D
        ``(H, W)`` int32 label plane; ``timepoint`` is in ``[0, n_timepoints)``.
        See :meth:`_write_resource_frame` for the absent / 2D-promote / in-place
        cases. Enforces int32 dtype.
        """
        return self._write_resource_frame(
            "labels", name, frame, timepoint, np.int32, "Labels", self.write_labels
        )

    def read_labels(
        self, name: str, view_bin: int = 1, timepoint: int | None = None
    ) -> NDArray[np.int32]:
        """Read a segmentation label array from /labels/<name>.

        ``view_bin`` follows the same rule as :meth:`read_array`. Labels
        downsample via block mode (ties resolve to 0). When ``timepoint``
        is given, returns only that frame of a time-stacked ``(T, H, W)``
        labels resource (``(H, W)``); leave it ``None`` to read the whole
        array (the full stack for time-lapse, or the 2D array otherwise).

        A 2D label on a time-lapse dataset is *time-invariant* (e.g. a
        whole-field gate written by ``percell4-batch-whole-field``): a
        per-timepoint read broadcasts it, returning the same ``(H, W)``
        frame for every ``timepoint``. Without this, per-frame phases
        (threshold / measure) would hit ``read_array_frame``'s
        "not time-stacked" guard for any ``timepoint != 0``.
        """
        path = f"labels/{name}"
        if timepoint is None:
            return self.read_array(path, view_bin=view_bin)
        if self._is_2d_array(path):
            return self.read_array(path, view_bin=view_bin)
        return self.read_array_frame(path, timepoint, view_bin=view_bin)

    def list_labels(self) -> list[str]:
        """List all label set names under /labels/."""
        return self.list_groups("labels")

    # ── Convenience: masks ────────────────────────────────────

    def write_mask(
        self,
        name: str,
        array: NDArray,
        attrs: dict[str, Any] | None = None,
    ) -> int:
        """Write a mask (binary or multi-label) at /masks/<name>.

        Enforces uint8 dtype. Values 0-255 supported:
        - Binary: 0=outside, 1=inside
        - Multi-label: 0=outside, 1..N=ROI labels
        Returns element count.

        Accepts 2D ``(H, W)`` or time-stacked ``(T, H, W)`` (validated
        against the dataset's ``native_shape`` and ``n_timepoints``).

        ``attrs`` (optional) are merged onto the canonical ``{"dims": ...}``
        so Phase-6 Creators can stamp ``created_at_bin`` and any provenance
        keys without bypassing the chokepoint.
        """
        dims = self._validate_layer_shape(array, "Mask")
        array = array.astype(np.uint8, copy=False)
        merged_attrs: dict[str, Any] = {"dims": dims}
        if attrs:
            merged_attrs.update(attrs)
        return self.write_array(f"masks/{name}", array, attrs=merged_attrs)

    def write_mask_frame(
        self, name: str, frame: NDArray, timepoint: int
    ) -> int:
        """Write a single timepoint's 2D ``frame`` into /masks/<name>.

        The symmetric per-frame counterpart to :meth:`read_mask` with a
        ``timepoint``. ``frame`` is a 2D ``(H, W)`` uint8 mask plane;
        ``timepoint`` is in ``[0, n_timepoints)``. See
        :meth:`_write_resource_frame` for the absent / 2D-promote / in-place
        cases. Enforces uint8 dtype.
        """
        return self._write_resource_frame(
            "masks", name, frame, timepoint, np.uint8, "Mask", self.write_mask
        )

    def read_mask(
        self, name: str, view_bin: int = 1, timepoint: int | None = None
    ) -> NDArray[np.uint8]:
        """Read a mask from /masks/<name>.

        ``view_bin`` follows the same rule as :meth:`read_array`. Masks
        downsample via majority vote (>= ceil(k**2 / 2)). When ``timepoint``
        is given, returns only that frame of a time-stacked ``(T, H, W)``
        mask resource.

        A 2D mask on a time-lapse dataset is *time-invariant* (e.g. a whole-field
        or ROI gate): a per-timepoint read broadcasts it, returning the same
        ``(H, W)`` frame for every ``timepoint``. This mirrors
        :meth:`read_labels` — without the broadcast guard a ``timepoint != 0``
        read of a 2D mask hits ``read_array_frame``'s "not time-stacked" guard
        and raises ``IndexError``.
        """
        path = f"masks/{name}"
        if timepoint is None:
            return self.read_array(path, view_bin=view_bin)
        if self._is_2d_array(path):
            return self.read_array(path, view_bin=view_bin)
        return self.read_array_frame(path, timepoint, view_bin=view_bin)

    def list_masks(self) -> list[str]:
        """List all mask names under /masks/."""
        return self.list_groups("masks")

    # ── Convenience: tracks (lineage tables) ──────────────────

    def write_tracks(self, name: str, df: pd.DataFrame) -> int:
        """Write a lineage table at /tracks/<name> (CSV-string, like measurements).

        ``df`` is the per-track lineage table (track_id, tree_id, begin_t,
        end_t, parent_track_id). Stored as a sibling of the matching
        ``/labels/<name>`` tracked segmentation.
        """
        return self.write_dataframe(f"tracks/{name}", df)

    def read_tracks(self, name: str) -> pd.DataFrame:
        """Read the lineage table from /tracks/<name>."""
        return self.read_dataframe(f"tracks/{name}")

    def list_tracks(self) -> list[str]:
        """List all lineage-table names under /tracks/."""
        return self.list_groups("tracks")

    def set_mask_attrs(self, name: str, attrs: dict[str, Any]) -> None:
        """Write HDF5 attributes onto an existing /masks/<name> dataset.

        HDF5 attributes do not accept Python ``None``. Callers must
        substitute sentinel values themselves (e.g., 0.0 for an unset
        intensity threshold, -1.0 for an unset radius, "" for an empty
        string). Keys whose value is ``None`` are skipped.

        Raises ``KeyError`` if /masks/<name> does not exist.
        """
        path = f"masks/{name}"
        with h5py.File(self.path, "a") as f:
            if path not in f:
                raise KeyError(f"Mask not found: {path}")
            ds = f[path]
            for key, val in attrs.items():
                if val is None:
                    continue
                ds.attrs[key] = val

    # ── Groups and metadata ───────────────────────────────────

    def list_groups(self, prefix: str) -> list[str]:
        """List child dataset/group names under a given path."""
        f = self._open_read()
        try:
            if prefix not in f:
                return []
            return list(f[prefix].keys())
        finally:
            self._close_if_not_session(f)

    @property
    def metadata(self) -> dict[str, Any]:
        """Read /metadata/ group attributes as a dict.

        Two keys are guaranteed to be present whenever the dataset has any
        spatial array on disk, even on files written before the
        dataset-wide binning model existed:

        * ``native_shape`` -- ``(H, W)`` at k=1. Inferred from
          ``/intensity.shape[-2:]`` when absent. If neither ``/intensity``
          nor any ``/decay/<ch>`` exists, this key is set to ``None``.
        * ``creation_bin`` -- defaults to ``1`` when absent.

        Inference is in-memory only here -- the file is not rewritten.
        The next :meth:`set_metadata` call persists the inferred values
        (see that method for the consistency-check rule).
        """
        f = self._open_read()
        try:
            if "metadata" in f:
                attrs = dict(f["metadata"].attrs)
            else:
                attrs = {}
            inferred = _infer_bin_metadata(f)
            for key, val in inferred.items():
                attrs.setdefault(key, val)
            # Normalize native_shape to a Python tuple regardless of source
            # (h5py returns numpy arrays for sequence attrs).
            if attrs.get("native_shape") is not None:
                ns = attrs["native_shape"]
                if hasattr(ns, "tolist"):
                    ns = ns.tolist()
                attrs["native_shape"] = tuple(int(x) for x in ns)
            # Normalize channel_names to a Python list[str]: h5py returns a
            # numpy string array for a multi-element sequence attr, whose
            # truthiness is ambiguous (``arr or []`` raises). Decode bytes too.
            if attrs.get("channel_names") is not None:
                cn = attrs["channel_names"]
                if isinstance(cn, (str, bytes)):
                    cn = [cn]
                elif hasattr(cn, "tolist"):
                    cn = cn.tolist()
                attrs["channel_names"] = [
                    c.decode() if isinstance(c, bytes) else str(c) for c in cn
                ]
            if "creation_bin" in attrs:
                attrs["creation_bin"] = int(attrs["creation_bin"])
            if "n_timepoints" in attrs:
                attrs["n_timepoints"] = int(attrs["n_timepoints"])
            if "stitch_overlap" in attrs:
                attrs["stitch_overlap"] = float(attrs["stitch_overlap"])
            if "stitch_registered" in attrs:
                attrs["stitch_registered"] = bool(attrs["stitch_registered"])
            return attrs
        finally:
            self._close_if_not_session(f)

    def set_metadata(self, attrs: dict[str, Any]) -> int:
        """Write attributes to the /metadata/ group. Returns count written.

        As a side effect, persists inferred ``native_shape`` and
        ``creation_bin`` from :attr:`metadata` if they aren't on disk yet.
        Raises :class:`MetadataConsistencyError` if a stored
        ``native_shape`` disagrees with what we infer from the actual
        array on disk -- we never silently overwrite an explicit value.
        """
        with h5py.File(self.path, "a") as f:
            grp = f.require_group("metadata")
            inferred = _infer_bin_metadata(f)
            if "native_shape" in grp.attrs:
                stored = tuple(int(x) for x in grp.attrs["native_shape"])
                if (
                    inferred["native_shape"] is not None
                    and stored != inferred["native_shape"]
                ):
                    raise MetadataConsistencyError(
                        f"Stored /metadata.native_shape={stored} disagrees "
                        f"with on-disk shape={inferred['native_shape']}."
                    )
            else:
                if inferred["native_shape"] is not None:
                    grp.attrs["native_shape"] = inferred["native_shape"]
            grp.attrs.setdefault("creation_bin", inferred["creation_bin"])
            for key, val in attrs.items():
                grp.attrs[key] = val
        return len(attrs)

    # ── File lifecycle ────────────────────────────────────────

    def create(self, metadata: dict[str, Any] | None = None) -> None:
        """Create a new empty .h5 file, optionally with metadata."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(self.path, "w") as f:
            if metadata:
                grp = f.create_group("metadata")
                for key, val in metadata.items():
                    grp.attrs[key] = val

    def exists(self) -> bool:
        """Check if the .h5 file exists."""
        return self.path.exists()

    def delete_item(self, hdf5_path: str) -> bool:
        """Delete a dataset or group at the given HDF5 path. Returns True if deleted."""
        with h5py.File(self.path, "a") as f:
            if hdf5_path in f:
                del f[hdf5_path]
                return True
        return False

    def rename_item(self, old_path: str, new_path: str) -> bool:
        """Rename a dataset or group within the HDF5 file. Returns True if renamed."""
        with h5py.File(self.path, "a") as f:
            if old_path not in f:
                return False
            if new_path in f:
                raise ValueError(f"Target path already exists: {new_path}")
            f.move(old_path, new_path)
            return True

    def append_decay_layers(
        self,
        layers: dict[str, NDArray],
        provenance: dict[str, ProvenanceRecord],
        cross_format_rule: CrossFormatRule | None = None,
        force: bool = False,
    ) -> int:
        """Append per-channel TCSPC decay arrays to an existing dataset.

        Single chokepoint for ``/decay/<channel_name>`` writes — paired with
        a structured ``ProvenanceRecord`` per channel under
        ``/provenance/decay/<channel_name>``. Raises ``LayerAlreadyExists`` if
        any target ``/decay/<name>`` exists and ``force=False``.

        ``cross_format_rule`` (when provided) is persisted to
        ``/metadata.attrs[cross_format_rule]`` on first call. Subsequent calls
        with the same rule are a no-op on the metadata; calls with a different
        rule raise ``CrossFormatRuleConflict`` unless ``force=True``.
        ``ExplicitRule`` is exempt from conflict checks — it represents a
        per-binding override, not a base-rule change.

        Per-channel atomicity is best-effort: each channel's decay write +
        provenance write happen under one open file handle, with explicit
        ``flush()`` + ``fsync()`` between channels. HDF5 power-loss safety is
        not guaranteed (no journaling).
        """
        if set(layers.keys()) != set(provenance.keys()):
            missing = set(layers.keys()) - set(provenance.keys())
            extra = set(provenance.keys()) - set(layers.keys())
            raise ValueError(
                f"layers and provenance must agree on channel names — "
                f"provenance missing: {sorted(missing)}, extra: {sorted(extra)}"
            )

        if not layers:
            return 0

        # Pre-flight: rule conflict check
        if cross_format_rule is not None and not isinstance(cross_format_rule, ExplicitRule):
            existing_serialized = self.metadata.get("cross_format_rule")
            if existing_serialized is not None and not force:
                existing = deserialize_rule(existing_serialized)
                if existing != cross_format_rule:
                    raise CrossFormatRuleConflictError(
                        f"persisted rule {existing!r} differs from {cross_format_rule!r}; "
                        "use force=True to overwrite"
                    )

        # Pre-flight: existence check
        if not force:
            with h5py.File(self.path, "r") as f:
                for name in layers:
                    path = f"decay/{name}"
                    if path in f:
                        raise LayerAlreadyExistsError(name)

        # Write each channel under one open handle with explicit flush+fsync
        # between channels. Best-effort per-channel atomicity. Decay arrays
        # are cast to float32 to match compress's storage format — phasor
        # math runs in float64 either way, but matching dtype keeps disk
        # layout consistent between the two import flows so downstream
        # tools don't see a uint32-vs-float32 discrepancy.
        with h5py.File(self.path, "a") as f:
            for name, decay in layers.items():
                path = f"decay/{name}"
                if path in f:
                    del f[path]
                if decay.dtype != np.float32:
                    decay = decay.astype(np.float32, copy=False)
                chunks = _choose_chunks(decay.shape, is_decay=True)
                f.create_dataset(
                    path,
                    data=decay,
                    chunks=chunks,
                    **_compression_kwargs(is_decay=True),
                )
                # Provenance group + attrs
                prov_path = f"provenance/decay/{name}"
                if prov_path in f:
                    del f[prov_path]
                grp = f.require_group(prov_path)
                for key, val in provenance[name].to_attrs().items():
                    grp.attrs[key] = val
                # Best-effort flush + fsync between channels
                f.flush()
                try:
                    fd = f.id.get_vfd_handle()
                    if isinstance(fd, tuple):
                        fd = fd[0]
                    if isinstance(fd, int) and fd >= 0:
                        os.fsync(fd)
                except (AttributeError, OSError, ValueError):
                    # Some VFDs don't expose a POSIX fd; flush() alone has to suffice.
                    pass

            # Persist the dropdown-level rule to /metadata
            if cross_format_rule is not None and not isinstance(cross_format_rule, ExplicitRule):
                grp = f.require_group("metadata")
                grp.attrs["cross_format_rule"] = serialize_rule(cross_format_rule)

        return len(layers)

    def delete_channel(self, name: str) -> bool:
        """Remove every per-channel surface for ``name``.

        Symmetric pair to :meth:`rename_channel`. Sweeps all four
        per-channel surfaces in one ``h5py.File(..., "a")`` open:

        - ``/decay/<name>`` group (if present)
        - ``/phasor/<name>`` group (if present)
        - ``name`` entry in ``metadata.channel_names`` (if present)
        - ``flim_cal_phase_<name>`` and ``flim_cal_mod_<name>`` attrs
          on ``/metadata`` (if present)

        Returns ``True`` if anything was actually removed on disk,
        ``False`` if the channel was already absent everywhere
        (mirrors :meth:`delete_item`'s contract). Callers that need a
        "must have existed" semantic can branch on the return value.
        """
        deleted_any = False
        with h5py.File(self.path, "a") as f:
            for prefix in ("decay", "phasor"):
                p = f"{prefix}/{name}"
                if p in f:
                    del f[p]
                    deleted_any = True
            if "metadata" in f:
                attrs = f["metadata"].attrs
                names = list(attrs.get("channel_names", []))
                if name in names:
                    names.remove(name)
                    attrs["channel_names"] = names
                    deleted_any = True
                # Per-channel FLIM calibration attrs are removed regardless
                # of whether they contributed to deleted_any — they're
                # bookkeeping that should follow the channel out.
                for key_prefix in ("flim_cal_phase_", "flim_cal_mod_"):
                    key = f"{key_prefix}{name}"
                    if key in attrs:
                        del attrs[key]
        return deleted_any

    def rename_channel(self, old_name: str, new_name: str) -> None:
        """Rename a channel across all per-channel paths and metadata attrs.

        Moves ``/decay/<old>`` and ``/phasor/<old>`` groups, updates the
        ``channel_names`` list, and renames per-channel FLIM calibration
        attrs (``flim_cal_phase_<name>``, ``flim_cal_mod_<name>``). Silent
        no-op for paths/attrs that don't exist.
        """
        if old_name == new_name:
            return
        with h5py.File(self.path, "a") as f:
            for prefix in ("decay", "phasor"):
                old_path = f"{prefix}/{old_name}"
                new_path = f"{prefix}/{new_name}"
                if old_path in f:
                    if new_path in f:
                        raise ValueError(f"Target path already exists: {new_path}")
                    f.move(old_path, new_path)
            if "metadata" in f:
                attrs = f["metadata"].attrs
                names = list(attrs.get("channel_names", []))
                if old_name in names:
                    names[names.index(old_name)] = new_name
                    attrs["channel_names"] = names
                for key_prefix in ("flim_cal_phase_", "flim_cal_mod_"):
                    old_key = f"{key_prefix}{old_name}"
                    new_key = f"{key_prefix}{new_name}"
                    if old_key in attrs:
                        attrs[new_key] = attrs[old_key]
                        del attrs[old_key]

    @staticmethod
    def create_atomic(
        path: str | Path,
        build_fn,
    ) -> None:
        """Create an .h5 file atomically via write-to-temp-then-rename.

        Use for import operations where crash safety matters::

            def build(h5_file):
                h5_file.create_dataset("intensity", data=image)

            DatasetStore.create_atomic("output.h5", build)
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".h5.tmp", dir=path.parent
        )
        os.close(fd)
        try:
            with h5py.File(tmp_path, "w") as f:
                build_fn(f)
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
