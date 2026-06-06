"""Lazy, per-frame resident buffers for fast large-dataset display.

Loading a large time-lapse ``.h5`` eagerly decompresses the whole stack
(measured ~19 GB, gzip, single-threaded) before the viewer shows anything —
the cause of the multi-minute "Load Dataset…" wait. This module backs each
time-stacked napari layer with a pre-allocated full-shape ``(T, H, W)`` array
whose frames are decoded on demand and in the background, so the viewer can
show timepoint 0 in ~1–2 s and fill the remaining frames without blocking.

Phase 1 fills the buffer with a single background thread (see
``gui/workers.py``); the decode work is expressed as the module-level
:func:`decode_frame_into` primitive, parameterized by path / timepoint / slot,
so Phase 2 can call the same primitive from worker *processes* writing into a
shared-memory buffer without changing the buffer contract.

Pure of Qt and napari — only numpy + the :class:`DatasetStore` read
primitives. 2D / time-invariant resources are *not* buffered here (they are a
single cheap plane); only leading-``T`` resources need the lazy treatment.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from percell4.store import DatasetStore

ResourceKind = Literal["intensity", "labels", "mask"]


@dataclass(frozen=True)
class ResourceSpec:
    """Describes one time-stacked ``(T, H, W)`` display resource.

    ``hdf5_path`` is the dataset path read per frame (``"intensity"``,
    ``"labels/<name>"`` or ``"masks/<name>"``). ``channel_idx`` selects a
    channel out of a ``(T, C, H, W)`` intensity frame; it is ``None`` for a
    single-channel ``(T, H, W)`` intensity layer and for labels/masks.
    """

    layer_name: str
    kind: ResourceKind
    hdf5_path: str
    name: str  # bare resource name for labels/masks (read_labels/read_mask); "" for intensity
    channel_idx: int | None
    dtype: np.dtype
    frame_shape: tuple[int, int]


def decode_frame_into(
    out: NDArray,
    t: int,
    *,
    path: str,
    hdf5_path: str,
    channel_idx: int | None,
    view_bin: int,
) -> None:
    """Decode one timepoint of ``hdf5_path`` into ``out[t]``.

    Opens its **own** :class:`DatasetStore` handle (h5py handles are not
    shareable across threads/processes), reads a single frame via
    ``read_array_frame`` (only that frame's chunks are decompressed), selects
    ``channel_idx`` if given, and writes it into the pre-allocated slot.

    Takes only primitives plus the destination array, so it is safe to call
    from a worker process in Phase 2 (where ``out`` becomes a shared-memory
    view created from the same name/shape/dtype).
    """
    store = DatasetStore(path)
    frame = store.read_array_frame(hdf5_path, t, view_bin=view_bin)
    if channel_idx is not None:
        frame = frame[channel_idx]
    out[t] = frame


class LazyResidentBuffer:
    """Full-shape resident arrays for a dataset's time-stacked layers.

    Allocates one zeroed ``(T, *frame_shape)`` array per :class:`ResourceSpec`
    and tracks a single per-timepoint ``ready`` vector — a frame is ready once
    every resource has decoded that frame. :meth:`fill_frame` decodes one
    timepoint across all resources (the intensity frame is read once and
    sliced per channel); it is idempotent and thread-safe, so the background
    filler and an on-demand main-thread request never corrupt or double-write
    a slot.
    """

    def __init__(
        self,
        path: str | Path,
        n_timepoints: int,
        specs: list[ResourceSpec],
        view_bin: int = 1,
    ) -> None:
        if n_timepoints < 1:
            raise ValueError(f"n_timepoints must be >= 1, got {n_timepoints}")
        self.path = str(path)
        self.n_timepoints = int(n_timepoints)
        self.specs = list(specs)
        self.view_bin = int(view_bin)
        self.arrays: dict[str, NDArray] = {}
        self.ready: NDArray[np.bool_] = np.zeros(self.n_timepoints, dtype=bool)
        # One lock per timepoint so concurrent fills of *different* frames
        # don't serialize, but a double-fill of the *same* frame does.
        self._locks = [threading.Lock() for _ in range(self.n_timepoints)]
        self.allocate()

    def allocate(self) -> None:
        """Allocate (or re-allocate) zeroed full-shape arrays for every spec."""
        self.arrays = {
            spec.layer_name: np.zeros(
                (self.n_timepoints, *spec.frame_shape), dtype=spec.dtype
            )
            for spec in self.specs
        }
        self.ready[:] = False

    def is_ready(self, t: int) -> bool:
        return bool(self.ready[t])

    def pending_frames(self) -> list[int]:
        """Timepoints not yet decoded, in ascending order."""
        return [t for t in range(self.n_timepoints) if not self.ready[t]]

    def fill_frame(self, t: int, store: DatasetStore | None = None) -> None:
        """Decode timepoint ``t`` into every resource slot, then mark it ready.

        Idempotent: returns immediately if ``t`` is already ready. ``store``
        lets a caller reuse an open ``open_read`` session for efficiency; when
        omitted a fresh per-resource handle is opened via
        :func:`decode_frame_into`.
        """
        if not 0 <= t < self.n_timepoints:
            raise IndexError(
                f"timepoint={t} out of range [0, {self.n_timepoints})"
            )
        with self._locks[t]:
            if self.ready[t]:
                return
            if store is not None:
                self._fill_with_store(t, store)
            else:
                for spec in self.specs:
                    decode_frame_into(
                        self.arrays[spec.layer_name],
                        t,
                        path=self.path,
                        hdf5_path=spec.hdf5_path,
                        channel_idx=spec.channel_idx,
                        view_bin=self.view_bin,
                    )
            self.ready[t] = True

    def close(self) -> None:
        """Release the resident arrays.

        Phase 1 backing is plain numpy, so this just drops the references and
        marks every frame not-ready (a single invalidation point bound to
        dataset switch / viewer clear). Phase 2 overrides this to ``close()``
        and ``unlink()`` the shared-memory segment.
        """
        self.arrays = {}
        self.ready[:] = False

    def _fill_with_store(self, t: int, store: DatasetStore) -> None:
        """Fill frame ``t`` reusing one open store, reading intensity once."""
        intensity_specs = [s for s in self.specs if s.kind == "intensity"]
        if intensity_specs:
            frame = store.read_array_frame(
                "intensity", t, view_bin=self.view_bin
            )
            for spec in intensity_specs:
                plane = frame if spec.channel_idx is None else frame[spec.channel_idx]
                self.arrays[spec.layer_name][t] = plane
        for spec in self.specs:
            if spec.kind == "labels":
                self.arrays[spec.layer_name][t] = store.read_labels(
                    spec.name, view_bin=self.view_bin, timepoint=t
                )
            elif spec.kind == "mask":
                self.arrays[spec.layer_name][t] = store.read_mask(
                    spec.name, view_bin=self.view_bin, timepoint=t
                )


def plan_resources(
    store: DatasetStore, view_bin: int = 1
) -> tuple[int, list[ResourceSpec], dict[str, NDArray]]:
    """Inspect a dataset and split it into lazy + eager display resources.

    Returns ``(n_timepoints, lazy_specs, eager_layers)`` where:

    * ``lazy_specs`` describe every time-stacked ``(T, …)`` resource that a
      :class:`LazyResidentBuffer` should own (intensity channels, time-lapse
      labels and masks).
    * ``eager_layers`` is ``{layer_name: 2D array}`` for resources that are a
      single cheap plane (non-time-lapse datasets, or 2D/time-invariant labels
      and masks on a time-lapse dataset) — read here and added directly.

    Reads only metadata + per-frame shapes to build specs (no full-stack
    decode); eager 2D planes are small and read in full.
    """
    from percell4.domain.io.layout import split_intensity_layers

    meta = store.metadata
    channel_names = meta.get("channel_names", [])
    n_timepoints = int(meta.get("n_timepoints", 1) or 1)

    lazy_specs: list[ResourceSpec] = []
    eager_layers: dict[str, NDArray] = {}

    # Non-time-lapse: nothing benefits from lazy framing — read eagerly, same
    # as the historical path (the dataset is a single plane / channel stack).
    if n_timepoints <= 1:
        intensity = store.read_array("intensity", view_bin=view_bin)
        for name, arr in split_intensity_layers(
            intensity, channel_names, n_timepoints
        ):
            eager_layers[name] = arr
        _add_eager_labels_masks(store, view_bin, eager_layers)
        return n_timepoints, lazy_specs, eager_layers

    # Time-lapse: intensity channels become lazy specs. Read frame 0 once to
    # learn per-channel frame shape and dtype without decoding the stack.
    frame0 = store.read_array_frame("intensity", 0, view_bin=view_bin)
    for name, plane in split_channels_for_frame(frame0, channel_names):
        ch_idx = None if frame0.ndim == 2 else _channel_index(name, channel_names)
        lazy_specs.append(
            ResourceSpec(
                layer_name=name,
                kind="intensity",
                hdf5_path="intensity",
                name="",
                channel_idx=ch_idx,
                dtype=np.dtype(plane.dtype),
                frame_shape=(plane.shape[-2], plane.shape[-1]),
            )
        )

    mask_names = set(store.list_masks())
    for label_name in store.list_labels():
        if label_name in mask_names:
            continue
        _classify_resource(
            store, "labels", label_name, f"labels/{label_name}",
            n_timepoints, view_bin, lazy_specs, eager_layers,
        )
    for mask_name in store.list_masks():
        _classify_resource(
            store, "mask", mask_name, f"masks/{mask_name}",
            n_timepoints, view_bin, lazy_specs, eager_layers,
        )

    return n_timepoints, lazy_specs, eager_layers


def split_channels_for_frame(
    frame: NDArray, channel_names: list[str]
) -> list[tuple[str, NDArray]]:
    """Split a single intensity frame into ``[(layer_name, 2D plane)]``.

    ``frame`` is ``(H, W)`` (single channel) or ``(C, H, W)`` (multi). Mirrors
    the display naming in :func:`domain.io.layout.split_channels_2d`.
    """
    from percell4.domain.io.layout import split_channels_2d

    return split_channels_2d(frame, channel_names)


def _channel_index(layer_name: str, channel_names: list[str]) -> int:
    names = list(channel_names) if channel_names is not None else []
    try:
        return names.index(layer_name)
    except ValueError:
        # Fallback name was "ch{i}" — recover the index.
        if layer_name.startswith("ch"):
            try:
                return int(layer_name[2:])
            except ValueError:
                pass
        return 0


def _classify_resource(
    store: DatasetStore,
    kind: ResourceKind,
    name: str,
    hdf5_path: str,
    n_timepoints: int,
    view_bin: int,
    lazy_specs: list[ResourceSpec],
    eager_layers: dict[str, NDArray],
) -> None:
    """Route one labels/mask resource to lazy (time-stacked) or eager (2D)."""
    if store._is_2d_array(hdf5_path):
        # Time-invariant plane — read once, add directly.
        if kind == "labels":
            eager_layers[name] = store.read_labels(name, view_bin=view_bin)
        else:
            eager_layers[name] = store.read_mask(name, view_bin=view_bin)
        return
    frame0 = store.read_array_frame(hdf5_path, 0, view_bin=view_bin)
    lazy_specs.append(
        ResourceSpec(
            layer_name=name,
            kind=kind,
            hdf5_path=hdf5_path,
            name=name,
            channel_idx=None,
            dtype=np.dtype(frame0.dtype),
            frame_shape=(frame0.shape[-2], frame0.shape[-1]),
        )
    )


def _add_eager_labels_masks(
    store: DatasetStore, view_bin: int, eager_layers: dict[str, NDArray]
) -> None:
    """Read all labels (minus masks) and masks as full arrays (non-time-lapse)."""
    mask_names = set(store.list_masks())
    for label_name in store.list_labels():
        if label_name not in mask_names:
            eager_layers[label_name] = store.read_labels(
                label_name, view_bin=view_bin
            )
    for mask_name in store.list_masks():
        eager_layers[mask_name] = store.read_mask(mask_name, view_bin=view_bin)
