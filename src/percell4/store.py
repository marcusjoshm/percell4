"""HDF5-based dataset storage for PerCell4.

Each dataset is a single .h5 file containing images, labels, masks,
measurements, and metadata. DatasetStore provides read/write access
with crash-safe per-operation file handling for writes and an optional
session mode for efficient repeated reads.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd
from numpy.typing import NDArray

from percell4.domain.io.cross_format import deserialize_rule, serialize_rule
from percell4.domain.io.models import (
    CrossFormatRule,
    ExplicitRule,
    ProvenanceRecord,
)

# Chunk cache size for session reads (64 MB)
_READ_CACHE_BYTES = 64 * 1024 * 1024


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
    if "intensity" in f:
        shape = f["intensity"].shape
        if len(shape) >= 2:
            native_shape = (int(shape[-2]), int(shape[-1]))
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
    return {"native_shape": native_shape, "creation_bin": creation_bin}


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


class CrossFormatRuleConflictError(Exception):
    """Raised when an append would persist a rule different from one already stored."""


# Backwards-compat aliases — the names without the Error suffix were used in
# the first round of tests. Keep them around so callers writing
# ``from percell4.store import LayerAlreadyExists`` still work.
LayerAlreadyExists = LayerAlreadyExistsError
CrossFormatRuleConflict = CrossFormatRuleConflictError


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
    """Return compression keyword arguments for dataset creation."""
    if is_decay:
        return {"compression": "lzf"}
    return {"compression": "gzip", "compression_opts": 4, "shuffle": True}


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

    def read_array(self, hdf5_path: str) -> NDArray:
        """Read a numpy array from the specified HDF5 path."""
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            obj = f[hdf5_path]
            if not isinstance(obj, h5py.Dataset):
                raise KeyError(f"{hdf5_path} is a group, not a dataset")
            return obj[()]
        finally:
            self._close_if_not_session(f)

    def read_channel(self, hdf5_path: str, channel_idx: int) -> NDArray:
        """Read a single channel plane from a 2D or 3D array.

        For 2D arrays, ``channel_idx`` must be 0 and the full array is returned.
        For 3D ``(C, H, W)`` arrays, returns only ``array[channel_idx]`` without
        loading the other channels — useful for phases that only need one channel
        on each dataset.
        """
        f = self._open_read()
        try:
            if hdf5_path not in f:
                raise KeyError(f"Dataset not found: {hdf5_path}")
            ds = f[hdf5_path]
            if ds.ndim == 2:
                if channel_idx != 0:
                    raise IndexError(
                        f"channel_idx={channel_idx} out of range for 2D array"
                    )
                return ds[()]
            if ds.ndim == 3:
                n_channels = ds.shape[0]
                if not 0 <= channel_idx < n_channels:
                    raise IndexError(
                        f"channel_idx={channel_idx} out of range [0, {n_channels})"
                    )
                return ds[channel_idx, ...]
            raise ValueError(
                f"read_channel expects 2D or 3D array, got {ds.ndim}D at {hdf5_path}"
            )
        finally:
            self._close_if_not_session(f)

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

    def write_labels(self, name: str, array: NDArray) -> int:
        """Write a segmentation label array at /labels/<name>.

        Enforces int32 dtype. Returns element count.
        """
        if array.ndim != 2:
            raise ValueError(f"Labels must be 2D, got {array.ndim}D")
        array = array.astype(np.int32, copy=False)
        return self.write_array(
            f"labels/{name}", array, attrs={"dims": ["H", "W"]}
        )

    def read_labels(self, name: str) -> NDArray[np.int32]:
        """Read a segmentation label array from /labels/<name>."""
        return self.read_array(f"labels/{name}")

    def list_labels(self) -> list[str]:
        """List all label set names under /labels/."""
        return self.list_groups("labels")

    # ── Convenience: masks ────────────────────────────────────

    def write_mask(self, name: str, array: NDArray) -> int:
        """Write a mask (binary or multi-label) at /masks/<name>.

        Enforces uint8 dtype. Values 0-255 supported:
        - Binary: 0=outside, 1=inside
        - Multi-label: 0=outside, 1..N=ROI labels
        Returns element count.
        """
        if array.ndim != 2:
            raise ValueError(f"Mask must be 2D, got {array.ndim}D")
        array = array.astype(np.uint8, copy=False)
        return self.write_array(
            f"masks/{name}", array, attrs={"dims": ["H", "W"]}
        )

    def read_mask(self, name: str) -> NDArray[np.uint8]:
        """Read a mask from /masks/<name>."""
        return self.read_array(f"masks/{name}")

    def list_masks(self) -> list[str]:
        """List all mask names under /masks/."""
        return self.list_groups("masks")

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
            if "creation_bin" in attrs:
                attrs["creation_bin"] = int(attrs["creation_bin"])
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
