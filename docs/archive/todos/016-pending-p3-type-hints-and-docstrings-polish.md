---
status: pending
priority: p3
issue_id: "016"
tags: [code-review, type-hints, docs]
dependencies: []
---

# Polish type hints and docstrings on new public surface

## Problem Statement

Several new public-ish APIs have incomplete type hints or docstrings that will make Phase 2 callers guess at the contract:

1. **`LauncherWindow.get_viewer_window`** has no return type hint. Its sibling `get_data_model` uses a string literal `"CellDataModel"`. Pattern should be consistent.

2. **`run_cellpose(..., model=None)`** and **`build_cellpose_model`** do not annotate `model`. The public type should at least be `Any` or, with a `TYPE_CHECKING`-guarded import, `cellpose.models.CellposeModel`.

3. **`DatasetStore.read_channel`** docstring does not say whether the returned array is a view or a copy, or what dtype contract callers should expect.

4. **`WorkflowDatasetEntry.compress_plan: dict[str, Any] | None`** is a typed black hole. A `TypedDict` (`CompressPlan`) or at minimum a `# TODO(phase2): promote to CompressPlan dataclass` comment helps future readers know what shape to expect.

5. **`read_channel` error message** for 2D nonzero index does not include the HDF5 path (the 3D error does include the path for the range check). Inconsistent.

## Findings

- **kieran-python-reviewer** (S9, S10, S11, N4, N15, N16): collected type-hint / docstring polish items.

## Proposed Solutions

### Option A — Apply all five (Recommended)

```python
# 1. launcher.py
if TYPE_CHECKING:
    from percell4.gui.viewer import ViewerWindow

def get_viewer_window(self) -> "ViewerWindow":
    ...
```

```python
# 2. segment/cellpose.py
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # cellpose is lazy-imported; type as Any to avoid import cost

def build_cellpose_model(
    model_type: str = "cpsam",
    gpu: bool = False,
) -> Any:
    ...

def run_cellpose(
    image: NDArray,
    ...
    model: Any = None,
) -> NDArray[np.int32]:
    ...
```

```python
# 3. store.py — expand read_channel docstring
def read_channel(self, hdf5_path: str, channel_idx: int) -> NDArray:
    """Read a single channel plane from a 2D or 3D array.

    ...

    Returns
    -------
    A new numpy array (copy, not view) in the dataset's stored dtype.
    For intensity channels that is typically ``float32``.
    """
```

```python
# 4. models.py
# Add a TODO comment (the full TypedDict can wait for Phase 2 when the real
# shape is known):
class WorkflowDatasetEntry:
    ...
    compress_plan: dict[str, Any] | None = None
    # TODO(phase2): promote to CompressPlan TypedDict / frozen dataclass
```

```python
# 5. store.py — read_channel 2D error includes path
raise IndexError(
    f"channel_idx={channel_idx} out of range for 2D array at {hdf5_path}"
)
```

- **Pros**: All small, independent, and improve discoverability.
- **Cons**: Nothing meaningful.
- **Effort**: Small.

### Option B — Apply only 1 and 2 (critical type hints)

- **Pros**: Covers the return/parameter type holes.
- **Cons**: Leaves documentation gaps.

## Recommended Action

Option A.

## Technical Details

**Affected files:**
- `src/percell4/gui/launcher.py` — `get_viewer_window` signature
- `src/percell4/segment/cellpose.py` — `build_cellpose_model`, `run_cellpose` signatures
- `src/percell4/store.py` — `read_channel` docstring and error message
- `src/percell4/workflows/models.py` — TODO comment

## Acceptance Criteria

- [ ] All new public methods have return type hints
- [ ] `model=` parameter on `run_cellpose` and `build_cellpose_model` is annotated (Any is acceptable)
- [ ] `read_channel` docstring mentions copy vs view + dtype
- [ ] `read_channel` 2D error includes the HDF5 path
- [ ] `compress_plan` has a TODO comment referencing Phase 2

## Work Log

- 2026-04-10 — Collected from kieran-python-reviewer polish items.

## Resources

- Review source: kieran-python-reviewer S9, S10, S11, N4, N15, N16
