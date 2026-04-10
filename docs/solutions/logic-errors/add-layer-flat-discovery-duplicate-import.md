---
title: "Add Layer Dialog Imports Same Data for All Datasets in Flat Discovery"
category: logic-errors
tags: [add-layer, flat-directory-discovery, datasetspec, file-scanning, condition-priority]
module: gui/add_layer_dialog.py
symptom: "Add Layer to Dataset 'Discover TIFFs' in flat directory mode uploads the same image for all checked datasets"
root_cause: "Condition checks ds.source_dir (always true in flat mode) before ds.files, causing full directory re-scan instead of using per-dataset file list"
date: 2026-04-05
---

# Add Layer Dialog Imports Same Data for All Datasets in Flat Discovery

Recurrence of the same bug class documented in [batch-compress-development-lessons.md, Bug #3](batch-compress-development-lessons.md).

## Symptom

In the Add Layer to Dataset dialog, using "Discover TIFFs" with flat directory discovery mode, all checked datasets received the same imported data regardless of which datasets were selected. Every dataset got every file from the directory.

## Root Cause

In `add_layer_dialog.py:_on_import_batch()` (line 514), the condition priority was inverted:

```python
# BUG: source_dir is always truthy for flat discovery
if ds.source_dir:
    scan = scanner.scan(path=ds.source_dir)
else:
    scan = scanner.scan(files=[str(f.path) for f in ds.files])
```

In flat directory discovery, all `DatasetSpec` objects share the same `source_dir` (the flat directory root). Each has a unique `files` tuple containing only its subset. But since `ds.source_dir` is always truthy, the scanner re-scanned the entire directory for every dataset, producing identical results.

## Fix

Flip the condition to check `ds.files` first:

```python
if ds.files:
    scan = scanner.scan(files=[str(f.path) if hasattr(f, "path") else str(f) for f in ds.files])
else:
    scan = scanner.scan(path=ds.source_dir)
```

## Prevention

This is the second occurrence of this exact bug pattern (first in `compress_dialog.py`, now in `add_layer_dialog.py`). The general rule from Bug #3:

> **Discovery produces scoped subsets. Processing must consume those subsets, not re-derive them from the shared parent directory.**

Any code consuming `DatasetSpec` should use `ds.files` as the primary data source. `ds.source_dir` is contextual metadata, not a processing input.

### Detection in code review

- Flag any `if ds.source_dir:` as suspect
- Flag any `scanner.scan(path=...)` inside a loop over discovered datasets
- Verify flat directory mode is tested with 2+ datasets

## Related

- [batch-compress-development-lessons.md](batch-compress-development-lessons.md) — Bug #3 documents the identical root cause in the compress dialog
