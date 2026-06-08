"""Parallel HDF5 decode into shared memory for fast eager dataset loading.

The GUI's eager display read (full intensity + label/mask stacks) is
single-threaded gzip decompression — measured ~60s for a 36-timepoint
whole-slide dataset. gzip decode does **not** parallelize across threads (the
HDF5 library serializes its calls), but it does across *processes*: each worker
process owns its own file handle and writes decoded frames straight into one
shared-memory block, so only frame indices cross the process boundary, never
pixels. Measured ~5.3x end-to-end (60s → 11s) at 10 workers, output
byte-identical to ``DatasetStore.read_array``.

Constraints baked in (macOS ``spawn``):
- The worker fn and helpers are **module-level** so they pickle by qualified
  name. This module imports only ``h5py`` / ``numpy`` / stdlib — never Qt or
  napari — so spawned children start fast.
- Each worker opens its **own** ``h5py.File`` (handles are not fork/pickle
  safe), cached per process and reused across that worker's frames.
- The parent **owns** the shared-memory block: it creates it, copies the
  result out, then ``close()`` + ``unlink()`` exactly once. Workers only
  ``close()`` (detach) — never ``unlink()``.
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import shared_memory

import h5py
import hdf5plugin  # noqa: F401 — register Blosc filter in spawned workers
import numpy as np
from numpy.typing import NDArray

# Per-process h5py handle cache (reused across a worker's frames).
_WORKER_FILE: dict[str, h5py.File] = {}


def _worker_file(path: str) -> h5py.File:
    f = _WORKER_FILE.get(path)
    if f is None:
        f = h5py.File(path, "r")
        _WORKER_FILE[path] = f
    return f


def _decode_frame(
    path: str,
    hdf5_path: str,
    frame: int,
    shm_name: str,
    full_shape: tuple[int, ...],
    dtype_str: str,
) -> int:
    """Worker: decode one timepoint of ``hdf5_path`` into the shared block.

    Runs in a spawned process. Returns the frame index (so progress callbacks
    can report it); pixel data never crosses the process boundary.
    """
    # --- DEBUG (temporary): surface worker-process failures in the cmd window.
    # Worker exceptions otherwise only re-raise through future.result() in the
    # parent and can be opaque (e.g. BrokenProcessPool if the OS OOM-kills us).
    import sys as _sys
    import traceback as _tb

    try:
        shm = shared_memory.SharedMemory(name=shm_name)
        try:
            view = np.ndarray(full_shape, dtype=np.dtype(dtype_str), buffer=shm.buf)
            view[frame] = _worker_file(path)[hdf5_path][frame]
            return frame
        finally:
            shm.close()  # detach mapping; never unlink in a worker
    except BaseException:  # noqa: BLE001 — debug: report then re-raise
        print(
            f"[PC4-LOAD][worker pid={os.getpid()}] FAILED decoding "
            f"{hdf5_path} frame {frame} (shm={shm_name}):",
            file=_sys.stderr,
            flush=True,
        )
        _tb.print_exc()
        _sys.stderr.flush()
        raise


def default_worker_count(n_frames: int | None = None) -> int:
    """Worker count: host CPUs (measured best == #cores), capped by frames."""
    n = os.cpu_count() or 4
    if n_frames is not None:
        n = min(n, max(1, n_frames))
    return n


def array_meta(path: str, hdf5_path: str) -> tuple[tuple[int, ...], np.dtype, bool]:
    """Return ``(shape, dtype, is_time_stacked)`` from HDF5 metadata only."""
    with h5py.File(path, "r") as f:
        obj = f[hdf5_path]
        shape = tuple(int(x) for x in obj.shape)
        dtype = obj.dtype
        dims = obj.attrs.get("dims")
        is_ts = dims is not None and len(dims) > 0 and str(dims[0]) == "T"
    return shape, dtype, is_ts


def make_executor(max_workers: int) -> ProcessPoolExecutor:
    """A spawn-context process pool (explicit spawn so it's GUI-safe)."""
    return ProcessPoolExecutor(
        max_workers=max_workers, mp_context=mp.get_context("spawn")
    )


def decode_array_parallel(
    path: str,
    hdf5_path: str,
    executor: ProcessPoolExecutor | None = None,
    progress_cb: Callable[[int], None] | None = None,
) -> NDArray:
    """Decode a (possibly time-stacked) HDF5 array using worker processes.

    For a leading-``T`` stack, submits one task per timepoint to ``executor``
    (created internally if ``None``); workers decode straight into one
    shared-memory block. Returns a plain numpy **copy** — the shared block is
    freed before returning, so the caller owns ordinary RAM with no
    shared-memory lifetime to manage. Non-time-stacked arrays (2D planes,
    ``(C,H,W)``) are small and read serially.

    Output is byte-identical to ``DatasetStore.read_array(hdf5_path)`` at
    view_bin=1. ``progress_cb`` is called once per decoded frame with the count
    completed so far.
    """
    import sys as _sys  # DEBUG (temporary)

    shape, dtype, is_ts = array_meta(path, hdf5_path)
    n_t = shape[0] if is_ts else 1

    if not is_ts or n_t <= 1:
        # Small / non-stacked — serial read (no parallel benefit).
        print(
            f"[PC4-LOAD] decode '{hdf5_path}' serial (non-stacked) "
            f"shape={shape} dtype={dtype}",
            file=_sys.stderr,
            flush=True,
        )
        with h5py.File(path, "r") as f:
            arr = f[hdf5_path][()]
        if progress_cb:
            progress_cb(1)
        return arr

    # NOTE: use math.prod (arbitrary-precision Python int), NOT np.prod —
    # np.prod defaults to a C-long accumulator, which is int32 on Windows and
    # silently overflows once the element count exceeds ~2.1e9 (e.g. a 36-tp
    # (36,2,6686,6567) stack = 3.16e9 elements), yielding a negative nbytes and
    # "'size' must be a positive integer" from SharedMemory. math.prod is safe.
    nbytes = math.prod(shape) * np.dtype(dtype).itemsize
    own_executor = executor is None
    n_workers = default_worker_count(n_t)
    print(
        f"[PC4-LOAD] decode '{hdf5_path}' shape={shape} dtype={dtype} "
        f"frames={n_t} workers={n_workers} "
        f"shm_alloc={nbytes / 1e9:.2f}GB own_executor={own_executor}",
        file=_sys.stderr,
        flush=True,
    )
    ex = executor or make_executor(n_workers)
    # Allocating the full (T,C,H,W) shared block can itself fail on a low-RAM
    # box (12.6GB for a 36-tp intensity stack). Report that distinctly.
    try:
        shm = shared_memory.SharedMemory(create=True, size=nbytes)
    except Exception as _exc:  # noqa: BLE001 — debug: report then re-raise
        print(
            f"[PC4-LOAD] FAILED allocating {nbytes / 1e9:.2f}GB shared memory "
            f"for '{hdf5_path}': {type(_exc).__name__}: {_exc}",
            file=_sys.stderr,
            flush=True,
        )
        raise
    try:
        view = np.ndarray(shape, dtype=dtype, buffer=shm.buf)
        futures = [
            ex.submit(_decode_frame, path, hdf5_path, t, shm.name, shape, dtype.str)
            for t in range(n_t)
        ]
        done = 0
        for fut in as_completed(futures):
            try:
                fut.result()  # raises if a worker failed
            except Exception as _exc:  # noqa: BLE001 — debug: report then re-raise
                print(
                    f"[PC4-LOAD] worker future FAILED on '{hdf5_path}' after "
                    f"{done}/{n_t} frames: {type(_exc).__name__}: {_exc}",
                    file=_sys.stderr,
                    flush=True,
                )
                raise
            done += 1
            if progress_cb:
                progress_cb(done)
        print(
            f"[PC4-LOAD] decode '{hdf5_path}' complete ({done}/{n_t} frames)",
            file=_sys.stderr,
            flush=True,
        )
        result = np.array(view)  # copy out of shared memory before freeing it
    finally:
        if own_executor:
            ex.shutdown(wait=True)
        shm.close()
        shm.unlink()
    return result
