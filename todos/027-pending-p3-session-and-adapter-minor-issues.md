---
status: pending
priority: p3
issue_id: "027"
tags: [code-review, cleanup, quality]
dependencies: []
---

# Minor quality issues across Session and adapters

## Problem Statement

Several small quality issues found by the Python reviewer:

1. **Session.set_measurements** computes `frozenset(df["label"].tolist())` twice (once for filter pruning, once for selection pruning). Hoist to one variable.
2. **NullViewerAdapter** doesn't declare protocol conformance — no import of or reference to ViewerPort. Readers can't verify conformance without tracing call sites.
3. **ExportImages.execute** has untyped `handle` parameter (should be `DatasetHandle`).
4. **Hdf5DatasetRepository** duplicates channel-splitting logic between `build_view` (lines 42-54) and `read_channel_images` (lines 77-88). The `build_view` variant has an inconsistent `shape[0] <= 20` guard that `read_channel_images` lacks.
5. **Domain type aliases** (`ChannelName = str`, `LayerName = str`, `CellId = int`) provide zero type safety. Consider `typing.NewType` for Python 3.12.

## Acceptance Criteria

- [ ] `set_measurements` computes valid label set once
- [ ] `NullViewerAdapter` includes a `ViewerPort` reference for verification
- [ ] `ExportImages.execute` has `handle: DatasetHandle` type annotation
- [ ] Channel-splitting logic extracted into shared helper in hdf5_store.py
- [ ] Domain type aliases upgraded to NewType (optional — evaluate benefit)
