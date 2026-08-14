---
title: AppleDouble ._ sidecars on exFAT drives break every file scan
date: 2026-08-04
category: runtime-errors
module: src/percell4/io/paths.py
problem_type: runtime_error
component: tooling
symptoms:
  - "failed to read channel names from /Volumes/<drive>/.../._<dataset>.h5"
  - "OSError: Unable to synchronously open file (file signature not found)"
  - "staging concat failed / ArrowInvalid: Parquet magic bytes not found in footer"
  - "A whole export dies after every dataset has already been measured"
  - "Only reproduces on external exFAT/FAT/SMB drives, never on the internal APFS disk"
root_cause: environment_assumption
resolution_type: code_fix
severity: high
related_components:
  - src/percell4/workflows/phases.py
  - src/percell4/gui/workflows/single_cell/config_dialog.py
  - src/percell4/interfaces/cli/_batch_report.py
  - src/percell4/domain/io/scanner.py
tags:
  - macos
  - exfat
  - appledouble
  - extended-attributes
  - file-discovery
  - glob
  - h5py
  - pyarrow
---

# AppleDouble `._` sidecars on exFAT drives break every file scan

## Problem

Running the single-cell workflow against datasets on an external exFAT drive
spammed the console with h5py open failures, then killed the export outright —
after every dataset had already been segmented, thresholded and measured. The
same run against the internal APFS disk was clean.

## Symptoms

Two failures with one cause:

```
failed to read channel names from /Volumes/NX-74205/.../._PerCell_A549 ..._Rep_3.h5
OSError: Unable to synchronously open file (file signature not found)
```

```
staging concat failed
pyarrow.lib.ArrowInvalid: Parquet magic bytes not found in footer.
Either the file is corrupted or this is not a parquet file.
```

The staging folder explains it:

```
-rwx------  4096    ._PerCell_U2OS_60min_As_3x4.parquet
-rwx------@ 53216   PerCell_U2OS_60min_As_3x4.parquet
```

macOS cannot store extended attributes natively on exFAT/FAT/SMB, so it writes
them into an AppleDouble companion named `._<original name>` beside the real
file. That companion carries the **same extension** as the file it shadows, so
`glob("*.h5")` and `glob("*.parquet")` match it and hand the reader a 4 KB blob
of metadata.

The `._` files are also invisible: `ls -la` on a macOS 15 `fskit` exFAT mount
does not list them even though `Path.glob` returns them, which is why the folder
looks clean while the scan sees twice as many files.

## What Didn't Work

Re-running the workflow. The sidecars are recreated the moment anything sets an
xattr on the output file, so each run reproduced the failure identically, and
`export_run` deletes `staging/` only on success — so the run was unrecoverable
past the measure phase every time.

## Solution

One helper module, `src/percell4/io/paths.py`, and every disk scan routed
through it:

- `is_sidecar(path)` — true for `._*` companions and `.DS_Store`-class files
- `drop_sidecars(paths)` — order-preserving filter for explicit path lists
  (argv, file dialogs)
- `scan_files(folder, *patterns, recursive=False)` — sorted, de-duplicated,
  sidecar-free directory scan

Call sites converted: workflow staging concat (`phases.py`), all six GUI
"add folder of .h5" pickers, the TIFF/`.bin` import scans, `FileScanner`, the
`_resolve_paths` helpers behind the batch CLIs, the project registry's orphan
scan, and FLIM-FRET discovery.

## Why This Works

The filter lives at discovery time, so a sidecar never reaches a reader that
would have to produce a confusing error about it. Filtering by the `._` name
prefix — not by size or by trying the open and catching — is exact: AppleDouble
companions are defined by that prefix, and no real PerCell artifact uses it.

`scan_files` also merges patterns into one de-duplicated, sorted list, which
replaced the repeated `sorted(glob("*.h5")) + sorted(glob("*.hdf5"))` idiom.

## Prevention

**Never call `Path.glob`/`rglob` directly to find data files.** Use
`percell4.io.paths.scan_files`, or `drop_sidecars` when the paths come from
somewhere other than a directory scan. A new `glob("*.<ext>")` in discovery code
is the regression to watch for in review.

Tests: `tests/test_io/test_paths.py` covers the helper;
`test_export_run_ignores_appledouble_staging_sidecars` in
`tests/test_workflows/test_phases.py` locks the export path against a
`._DS1.parquet` planted in `staging/`.

## Related Issues

Anything reading user data from removable media is exposed to the same class of
problem — Windows `Thumbs.db` / `desktop.ini` are covered by the same helper.
