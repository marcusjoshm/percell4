---
title: "refactor: Eliminate shims, temporary fixes, and architectural debt"
type: refactor
date: 2026-04-17
---

# refactor: Eliminate Shims, Temporary Fixes, and Architectural Debt

## Overview

Resolve all 9 pending todos from the hex architecture review. Organized into 4 phases by dependency order: shims first (largest blast radius), then decoupling, then NumPy compat, then polish. Each phase is one commit.

## Phase 1: Eliminate Re-export Shims (todos 024, 028, 030)

**The biggest cleanup.** 20 shim files with 37 stale imports across the codebase.

### Step 1: Update all imports to canonical paths

Batch find-and-replace across all `.py` files:

```
from percell4.measure.measurer    → from percell4.domain.measure.measurer
from percell4.measure.metrics     → from percell4.domain.measure.metrics
from percell4.measure.grouper     → from percell4.domain.measure.grouper
from percell4.measure.particle    → from percell4.domain.measure.particle
from percell4.measure.thresholding → from percell4.domain.measure.thresholding
from percell4.flim.phasor         → from percell4.domain.flim.phasor
from percell4.flim.wavelet_filter → from percell4.domain.flim.wavelet_filter
from percell4.segment.cellpose    → from percell4.adapters.cellpose
from percell4.segment.postprocess → from percell4.domain.segmentation.postprocess
from percell4.segment.roi_import  → from percell4.adapters.roi_import
from percell4.io.assembler        → from percell4.domain.io.assembler
from percell4.io.discovery        → from percell4.domain.io.discovery
from percell4.io.importer         → from percell4.adapters.importer
from percell4.io.models           → from percell4.domain.io.models
from percell4.io.readers          → from percell4.adapters.readers
from percell4.io.scanner          → from percell4.domain.io.scanner
from percell4.gui.launcher        → from percell4.interfaces.gui.main_window
from percell4.gui.cell_table      → from percell4.interfaces.gui.peer_views.cell_table
from percell4.gui.data_plot       → from percell4.interfaces.gui.peer_views.data_plot
from percell4.gui.phasor_plot     → from percell4.interfaces.gui.peer_views.phasor_plot
```

Also update test files (`tests/`).

### Step 2: Delete all 20 shim files

```
rm src/percell4/measure/{measurer,metrics,grouper,particle,thresholding}.py
rm src/percell4/flim/{phasor,wavelet_filter}.py
rm src/percell4/segment/{cellpose,postprocess,roi_import}.py
rm src/percell4/io/{assembler,discovery,importer,models,readers,scanner}.py
rm src/percell4/gui/{launcher,cell_table,data_plot,phasor_plot}.py
```

Keep the `__init__.py` files in the old packages so they remain importable (some may have other files).

### Step 3: Verify

```bash
# No shim files remain:
grep -rl "Re-export shim" src/percell4/ --include="*.py"
# Should return nothing

# No old-path imports remain:
grep -rn "from percell4\.\(measure\|flim\|segment\|io\|gui\.launcher\|gui\.cell_table\|gui\.data_plot\|gui\.phasor_plot\)\." src/percell4/ tests/ --include="*.py"
# Should return nothing (only __init__.py if any)

# Import-linter passes:
lint-imports

# All tests pass:
pytest
```

**Resolves:** todos 024, 028, 030

## Phase 2: Decouple GroupedSegPanel (todos 031, partial 024)

Apply the callback injection pattern (documented in `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`) to GroupedSegPanel — the last panel with a launcher reference.

### Changes

**`gui/grouped_seg_panel.py`:** Replace `launcher=None` with:
```python
def __init__(
    self,
    data_model: CellDataModel,
    *,
    get_store: Callable[[], Any | None],
    get_viewer_window: Callable[[], Any | None],
    show_status: Callable[[str], None] = lambda _: None,
    parent: QWidget | None = None,
) -> None:
```

**`interfaces/gui/task_panels/analysis_panel.py`:** Remove `launcher=None` transitional parameter. Wire GroupedSegPanel with callbacks:
```python
self._grouped_seg_panel = GroupedSegPanel(
    self.data_model,
    get_store=self._get_store,           # already exists on AnalysisPanel
    get_viewer_window=self._get_viewer_window,  # already exists
    show_status=self._show_status,       # already exists
)
```

**`interfaces/gui/main_window.py`:** Remove `launcher=self` from AnalysisPanel construction.

**Resolves:** todo 031, plus the `launcher=` portion of 024

## Phase 3: Fix NumPy 2.0 dtcwt Compatibility (todo 029)

Replace the monkey-patches with a vendored compat module.

### Option chosen: Centralized compat module

Create `src/percell4/_compat.py` imported once at app startup:

```python
"""NumPy 2.0 compatibility shims for third-party packages."""
import numpy as np

if not hasattr(np, "asfarray"):
    np.asfarray = lambda a, dtype=np.float64: np.asarray(a, dtype=dtype)
if not hasattr(np, "issubsctype"):
    np.issubsctype = lambda arg1, arg2: np.issubdtype(np.result_type(arg1), arg2)
```

Import it from `app.py` and `interfaces/cli/run_pipeline.py` (the two entry points) before anything else. Remove the shim from `domain/flim/wavelet_filter.py`.

This is still a monkey-patch but it's:
- Centralized (one file, not buried in a domain module)
- Import-order-safe (loaded at app entry, before any dtcwt use)
- Easy to find and delete when dtcwt releases a NumPy 2.0-compatible version

**Resolves:** todo 029

## Phase 4: Polish (todos 025, 026, 027, 032)

### 4A: Exception hierarchy (todo 025)

Create `src/percell4/domain/errors.py`:
```python
class PercellError(Exception): ...
class NoDatasetError(PercellError): ...
class NoSegmentationError(PercellError): ...
class NoMaskError(PercellError): ...
class NoChannelError(PercellError): ...
```

Update use cases to raise specific types instead of bare `ValueError`.

### 4B: Consistent use case returns (todo 026)

Standardize: all use cases return a `@dataclass` Result. Update `CloseDataset` (currently returns None) and `MeasureCells` (currently returns raw DataFrame).

### 4C: Minor quality fixes (todo 027)

- `Session.set_measurements`: hoist `frozenset(df["label"].tolist())` to one variable
- `NullViewerAdapter`: add `ViewerPort` reference comment
- `ExportImages.execute`: add `handle: DatasetHandle` type annotation
- `hdf5_store.py`: extract `_split_channels` helper for duplicated logic

### 4D: Remove deprecated get_data_model (todo 032)

Update `ThresholdQCQueueEntry` to accept Session. Remove `get_data_model()` from `WorkflowHost` protocol and launcher.

## Acceptance Criteria

- [ ] Zero re-export shim files in the codebase
- [ ] Zero `self._launcher` references in any task panel or GroupedSegPanel
- [ ] Zero `np.asfarray =` or `np.issubsctype =` in domain code
- [ ] NumPy compat shim centralized in `_compat.py`
- [ ] All use cases raise typed exceptions (not bare ValueError)
- [ ] All use cases return typed Result dataclasses
- [ ] `get_data_model()` removed from WorkflowHost protocol
- [ ] Import-linter: 3 contracts kept, 0 broken
- [ ] All tests pass

## Execution Order

| Phase | Todos Resolved | Risk | Effort |
|-------|---------------|------|--------|
| 1: Shim elimination | 024, 028, 030 | Medium (many import changes) | Large |
| 2: GroupedSegPanel decoupling | 031 | Low | Small |
| 3: NumPy compat centralization | 029 | Low | Small |
| 4: Polish | 025, 026, 027, 032 | Low | Medium |

## References

- Callback injection pattern: `docs/solutions/architecture-decisions/decouple-task-panels-callback-injection.md`
- NumPy 2.0 dtcwt doc: `docs/solutions/build-errors/numpy2-dtcwt-removed-functions.md`
- Session bridge pattern: `docs/solutions/architecture-decisions/session-bridge-event-forwarding.md`
