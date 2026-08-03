---
title: Multi-channel dataset load crashes on numpy channel_names truthiness
date: 2026-05-22
category: runtime-errors
module: src/percell4/store.py
problem_type: runtime_error
component: tooling
symptoms:
  - "Loading a multi-channel .h5 dataset in percell4-gui crashes on open"
  - "ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()"
  - "Crash originates at src/percell4/domain/io/layout.py:60 in split_intensity_layers"
  - "Single-channel datasets load fine while multi-channel datasets fail"
  - "Bug latent until percell4-batch --channel-names produced the first multi-channel dataset"
root_cause: wrong_api
resolution_type: code_fix
severity: high
related_components:
  - src/percell4/domain/io/layout.py
  - tests/test_store.py
  - tests/test_io/test_layout.py
tags:
  - numpy
  - h5py
  - truth-value-ambiguous
  - channel-names
  - dataset-loading
  - metadata-normalization
  - multi-channel
---

# Multi-channel dataset load crashes on numpy channel_names truthiness

## Problem

Opening a multi-channel `.h5` dataset in `percell4-gui` crashed instantly with a
numpy "ambiguous truth value" `ValueError`, making any dataset with more than one
channel completely unloadable. Single-channel datasets — which had always been the
only kind in circulation — opened fine, so the defect surfaced only once a new
`percell4-batch --channel-names DAPI,GFP,RFP` feature produced the first real
multi-channel file fed through the GUI's load path.

## Symptoms

- GUI fails to open any multi-channel dataset; single-channel datasets open normally.
- Exact error:
  ```
  ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
  ```
- Traceback tail points at `src/percell4/domain/io/layout.py:60`, the line:
  ```python
  names = list(channel_names or [])
  ```
- Triggered specifically by datasets carrying a `channel_names` metadata attr with
  2+ entries (e.g. `DAPI,GFP,RFP`).

## What Didn't Work

The first hypothesis was that the brand-new `percell4-batch --channel-names` CLI was
writing malformed `channel_names` metadata — the bug appeared right after that
feature shipped, so the new code looked guilty. That was wrong. A direct
reproduction disproved it: create a 3-channel store, then read it back —

```python
store.set_metadata({"channel_names": ["DAPI", "GFP", "RFP"]})
cn = store.metadata["channel_names"]   # -> numpy.ndarray(['DAPI','GFP','RFP'])
list(cn or [])                          # -> ValueError: ambiguous truth value
```

The CLI had written perfectly correct data. The crash came from the *read/display*
path: `channel_names or []` evaluating the truthiness of a multi-element numpy
array. The defect was a pre-existing latent flaw that single-channel datasets (a
1-element array, which has unambiguous truthiness) had always masked.

## Solution

Two layers — a central normalization at the read boundary, plus a defensive guard in
the consumers.

**1. `DatasetStore.metadata` (`src/percell4/store.py`)** normalizes `channel_names`
to a plain `list[str]`, mirroring the existing `native_shape` → tuple normalization a
few lines above:

```python
# Normalize channel_names to a Python list[str]: h5py returns a numpy
# string array for a multi-element sequence attr, whose truthiness is
# ambiguous (``arr or []`` raises). Decode bytes too.
if attrs.get("channel_names") is not None:
    cn = attrs["channel_names"]
    if isinstance(cn, (str, bytes)):
        cn = [cn]
    elif hasattr(cn, "tolist"):
        cn = cn.tolist()
    attrs["channel_names"] = [
        c.decode() if isinstance(c, bytes) else str(c) for c in cn
    ]
```

**2. `domain/io/layout.py`** — both `split_channels_2d` and `split_intensity_layers`
replaced the truthiness test with an explicit `is not None` check:

```python
# before
names = list(channel_names or [])
# after
names = list(channel_names) if channel_names is not None else []
```

## Why This Works

h5py does not round-trip Python lists. Any list written to an HDF5 attribute comes
back as a numpy `ndarray` (a multi-element string attr returns a numpy string
array). The `x or default` idiom relies on `bool(x)`, but `bool()` of a numpy array
with more than one element is *ambiguous by design* — numpy raises rather than guess
whether you meant "any element truthy" or "all elements truthy." So
`channel_names or []` works for `None`, an empty array, and a **single**-element
array (which has a well-defined truth value), but raises the moment the array has 2+
elements. That is exactly why single-channel datasets never tripped it and the bug
stayed latent until the first multi-channel file existed.

Fixing it centrally in `DatasetStore.metadata` is the right layer: it is the single
read boundary where h5py's array representation re-enters Python. Normalizing there
means *every* downstream consumer — not just these two split functions — receives a
clean `list[str]` and never has to reason about numpy truthiness again. The
layout-level guards are belt-and-suspenders for any array that reaches those
functions via a different path.

## Prevention

- **Never use `x or default` on a value that might be a numpy array.** Use an
  explicit `x if x is not None else default`, or check `len(x)`. The `or` idiom
  silently works for scalars and 1-element arrays, then explodes on larger ones — a
  classic latent trap that test data with a single element will never catch.
- **Normalize sequence attributes at the read boundary.** Treat the HDF5 read site
  (`DatasetStore.metadata`) as the place to coerce h5py's numpy representations back
  into plain Python types. `native_shape` → `tuple`, `channel_names` → `list[str]`,
  scalar counts → `int`. Consumers should never see raw numpy.
- **Remember that any list written to an h5py attr returns as an `ndarray`** (and
  bytes for strings under some encodings — hence the `.decode()` in the normalizer).
  Write-then-read is not symmetric.
- **Regression tests added:**
  - `tests/test_store.py::test_metadata_channel_names_normalized_to_str_list` —
    asserts `store.metadata["channel_names"]` is a `list[str]` for both the
    multi-channel (`["DAPI","GFP","RFP"]`) and single-channel (`["mNG"]`) cases.
  - `tests/test_io/test_layout.py::test_channel_names_as_numpy_array_does_not_crash`
    — passes a raw `np.array(["DAPI","GFP","RFP"])` straight into both
    `split_intensity_layers` and `split_channels_2d`, asserting no crash and correct
    layer names — locking in the defensive guard against the exact numpy-array input
    that caused the original `ValueError`.

All 206 store/io/adapter tests pass.

## Related Issues

- [tiff-pending-channel-name-prefix-mismatch](../logic-errors/tiff-pending-channel-name-prefix-mismatch-2026-05-21.md)
  — sibling `channel_names` contract bug from one day earlier. That one is a
  producer/consumer string-*value* mismatch (`"02"` vs `"ch02"`); this one is a
  producer-side *type* defect (`channel_names` returned as an ndarray). Both center
  on `DatasetStore.metadata["channel_names"]` as a cross-module contract and both
  pin it with a `tests/test_store.py` assertion.
- [in-session-hdf5-staleness-multi-vector](../logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md)
  — shares the "the h5py metadata read boundary is where surprising values enter
  Python; normalize there" theme (`store.py`, `DatasetStore` metadata reads).
