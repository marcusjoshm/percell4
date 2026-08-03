---
status: complete
priority: p2
issue_id: "023"
tags: [code-review, performance, adapters]
dependencies: []
---

# HDF5 adapter opens/closes file per operation — FIXED

## Resolution

Added `dict[Path, DatasetStore]` cache to `Hdf5DatasetRepository`. The `_store()` method now reuses cached instances instead of creating a new `DatasetStore` per call. Multiple reads within a use case (e.g., `MeasureCells.execute()` calling `read_channel_images`, `read_labels`, `read_mask`, `read_group_columns`) now share the same store instance.

Added `close(handle)` method to evict a store from the cache on dataset close.
