---
title: "np.prod(shape) overflows int32 on Windows — large-array byte-size calc went negative"
date: 2026-06-07
category: logic-errors
module: percell4.adapters.parallel_decode
problem_type: logic_error
component: tooling
applies_to:
  - "src/percell4/adapters/parallel_decode.py"
canonical_source: src/percell4/adapters/parallel_decode.py
symptoms:
  - "On Windows, loading a large stitched .h5 froze at ~8% (mid intensity decode); the same build loaded a smaller file fine, and the same large file loaded fine on macOS."
  - "Log showed shm_alloc=-4.53GB (negative) and ValueError: 'size' must be a positive integer from multiprocessing.shared_memory.SharedMemory(create=True, size=nbytes)."
root_cause: platform_integer_overflow
resolution_type: code_fix
severity: high
related_components: [numpy, shared-memory, multiprocessing, dataset-load, windows, hdf5]
tags:
  - numpy
  - np-prod
  - int32-overflow
  - windows
  - c-long
  - shared-memory
  - dataset-load
  - platform-specific
  - silent-failure
---

# np.prod(shape) overflows int32 on Windows

## Problem

A user reported `Nutlin3a_Merged.h5` (a 36-timepoint stitched timecourse) froze
at **~8%** on the "Loading dataset…" bar on their **Windows** PC, while
`Untreated_Merged.h5` (6 timepoints) loaded fine on the same machine, and
**both** files loaded fine on the developer's macOS box. 64 GB RAM, ample
pagefile — not a memory problem.

Diagnostic logging on the load path printed:

```
[PC4-LOAD] decode 'intensity' shape=(36, 2, 6686, 6567) dtype=float32 frames=36 workers=12 shm_alloc=-4.53GB
[PC4-LOAD] FAILED allocating -4.53GB shared memory for 'intensity': ValueError: 'size' must be a positive integer
```

The byte count was **negative**.

## Root cause

`adapters/parallel_decode.py::decode_array_parallel` sized the shared-memory
block for the full `(T,C,H,W)` decode with:

```python
nbytes = int(np.prod(shape)) * np.dtype(dtype).itemsize
```

`np.prod` over a tuple of Python ints accumulates in NumPy's **default integer
type**, which is the platform C `long`:

- **Windows:** C `long` is **32-bit** (even on 64-bit Windows — the LLP64 data
  model). The accumulator is `int32`.
- **macOS / Linux:** C `long` is **64-bit** (LP64). The accumulator is `int64`.

For Nutlin3a the element count is `36 × 2 × 6686 × 6567 = 3,161,301,264`, which
exceeds `2**31 = 2,147,483,648`. On Windows this **wraps negative** in int32;
`int(...)` faithfully carries the negative value through, and `× 4` yields
`-4.53 GB`. `SharedMemory(create=True, size=nbytes)` then rejects the negative
size and the load aborts — at 8%, because intensity is the first array and the
progress bar had only ticked through the manifest setup.

Why the others worked:

- **Untreated (6 tp):** `6 × 2 × 6641 × 6574 = 523,895,208` elements — under
  `2**31`, so no overflow even in int32. Loaded fine on Windows.
- **macOS (any size here):** int64 accumulator, no overflow at these scales.

So the bug only triggers at the **intersection** of Windows + an element count
above ~2.1 billion — which is exactly why it stayed invisible through macOS
development until a large timecourse hit a Windows machine.

## Resolution

Use Python's `math.prod` (arbitrary-precision `int`, never overflows) instead of
`np.prod`:

```python
# np.prod uses a C-long accumulator (int32 on Windows) and silently overflows
# past ~2.1e9 elements. math.prod is a Python bigint — always exact.
nbytes = math.prod(shape) * np.dtype(dtype).itemsize
```

Confirmed: `math.prod((36, 2, 6686, 6567)) * 4 = 12,645,205,056` (12.65 GB,
positive).

## Lessons (the compounding part)

- **`np.prod` / `np.sum` over shapes is unsafe for byte/size math on Windows.**
  The default accumulator follows C `long` = 32-bit on Windows (LLP64). Any
  element-count or byte-count that can exceed `2**31` must use `math.prod`,
  Python `int`, or an explicit `np.prod(shape, dtype=np.int64)`. Prefer
  `math.prod` — it can't be wrong.
- **"Works on my Mac" is not "works."** LP64 (mac/Linux) vs LLP64 (Windows)
  silently changes integer width. Overflow-prone arithmetic must be tested at
  Windows scale, not just developed on macOS.
- **A size/count computed for an allocation should be asserted positive.** A
  negative `nbytes` is a guaranteed bug; a cheap `assert nbytes > 0` (or letting
  `SharedMemory` raise, as here) converts a silent wrap into a loud failure.
- **The fix was found by logging the real path, not theorizing.** RAM, pagefile,
  GPU, and codec were all plausible and all wrong; printing the computed
  `shm_alloc` value made the negative number — and thus the overflow — obvious in
  one run. (See also
  `large-file-load-metadata-read-full-decode-2026-06-07.md`: profile the real
  entry path.)

## Related documentation

- [`large-file-load-metadata-read-full-decode-2026-06-07.md`](large-file-load-metadata-read-full-decode-2026-06-07.md)
  — the same load path; documents the parallel shared-memory decode this
  overflow was sizing.
- [NumPy default integer types](https://numpy.org/doc/stable/reference/arrays.scalars.html#numpy.int_)
  — `np.int_` (C `long`) is platform-dependent: 32-bit on Windows, 64-bit on
  Unix.
