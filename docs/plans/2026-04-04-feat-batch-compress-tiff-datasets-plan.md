---
title: "feat: Batch Compress TIFF Datasets"
type: feat
date: 2026-04-04
---

# feat: Batch Compress TIFF Datasets

## Enhancement Summary

**Deepened on:** 2026-04-04
**Sections enhanced:** All
**Research agents used:** Python reviewer, architecture strategist, performance oracle, simplicity reviewer, pattern specialist, race condition reviewer, Qt best practices researcher, framework docs researcher, learnings researcher

### Key Improvements
1. **Critical re-entrancy fix**: Use window-modal `QProgressDialog` + deep-copy config snapshot instead of raw `processEvents()` — prevents data corruption during batch
2. **Data model redesign**: Frozen `DatasetSpec` for IO layer, mutable GUI state separate; `StrEnum` for typed options; `FlimConfig` naming consistency
3. **Performance optimizations**: Stream tiles into pre-allocated grid (halves memory), eliminate double `.bin` reads, fix `assemble_channels` redundant copy, narrow `rglob` pattern
4. **QTreeWidget architecture**: `QStyledItemDelegate` for children, `openPersistentEditor` on parents only; manual tri-state propagation with `blockSignals`; eager population with `setUpdatesEnabled(False)`

### Simplification Consideration
The simplicity reviewer recommends deferring Manual mode (Phase 5) and token-based discovery to a later iteration — they are power-user features that roughly double implementation effort. The core value (batch compress from subdirectories) can ship in 3 phases. This is noted per-phase but the full plan is preserved for completeness.

---

## Overview

Replace the current "Import TIFF Dataset" dialog with a unified "Compress TIFF Dataset" dialog that discovers one or more TIFF datasets in a directory and converts them to `.h5` files. The dialog auto-adapts: one dataset discovered behaves as single compress, multiple datasets discovered shows a tree-based batch selection UI. Compress never loads into the viewer — loading is a separate action via "Load Dataset."

This also renames menu items: "Import TIFF Dataset..." → "Compress TIFF Dataset...", "Load Existing .h5 Dataset..." → "Load Dataset...", and removes the "Batch Import" placeholder.

## Problem Statement / Motivation

The current import workflow has three separate concerns muddled together:
1. **Discovery** — finding TIFF files in a directory
2. **Conversion** — assembling TIFFs into a compressed `.h5` file
3. **Loading** — opening an `.h5` file into the viewer

Users need to process entire experiments (many datasets) at once, not one at a time. The "Batch Import" placeholder has been waiting for this. By separating compress from load and supporting batch discovery, users can convert an entire experiment directory in one operation, then load individual datasets for analysis.

## Proposed Solution

A single `CompressDialog` that replaces `ImportDialog`. It:
1. Discovers datasets via subdirectory scanning or token-based file grouping
2. Presents a `QTreeWidget` with checkboxes for dataset selection
3. Offers Auto mode (all files → channels, auto-naming) and Manual mode (per-file layer type dropdowns, editable names)
4. Provides per-dataset stitching and FLIM config via a side panel
5. Runs compression with a progress dialog
6. Creates `.h5` files without loading them

## Technical Approach

### Architecture

```
CompressDialog (QDialog)
├── Source bar: directory picker + output path + discovery mode toggle
├── Auto/Manual toggle
├── DatasetTreeWidget (QTreeWidget subclass) — checkboxes, tri-state, delegates
├── DatasetConfigPanel (QWidget) — stitching + FLIM config for selected dataset
├── Global settings bar: z-projection, token patterns (collapsible)
└── Action bar: Compress button, Cancel

Batch compression runs on main thread with window-modal QProgressDialog.
Config snapshot (deep copy) taken before compression starts.
io/batch.py is GUI-agnostic — accepts plain progress callback, no Qt imports.
```

### Research Insights: Architecture

**Re-entrancy safety (Critical — from race condition review + learnings):**
- `QApplication.processEvents()` dispatches ALL pending events including button clicks and close events. Without protection, a user clicking "Compress" again during `processEvents()` causes stack re-entrance and potential HDF5 corruption.
- **Solution**: Use a **window-modal `QProgressDialog`** which absorbs all input events for the parent dialog. The user can only interact with the progress dialog (Cancel button). Combined with a `copy.deepcopy()` of `CompressConfig` before starting, the batch compressor works from a frozen snapshot while the dialog state cannot be modified.
- `QProgressDialog.setValue()` internally calls `processEvents()`, so explicit `processEvents()` calls are unnecessary when using it.
- **Set `setMinimumDuration(0)`** — the default is 4000ms, which means short batches never show the dialog.

**io/ layer purity (from architecture review):**
- `io/batch.py` must have zero Qt imports. The `processEvents()` call belongs in the GUI layer (inside the progress callback passed from the dialog, or implicit in `QProgressDialog.setValue()`).
- `io/batch.py` accepts a plain `Callable[[int, int, str], None]` progress callback, same as `import_dataset()`.

**Avoid redundant scanning (from pattern review):**
- `discover_datasets()` calls `FileScanner.scan()` per subdirectory, producing `ScanResult` with `DiscoveredFile` lists. But `import_dataset()` also calls `FileScanner.scan()` internally. To avoid scanning twice, either:
  - Pass pre-scanned files to `import_dataset()` via a new `files` parameter (preferred), or
  - Accept the redundant scan as a simplicity tradeoff

### Key Data Model

```python
from enum import StrEnum

class LayerType(StrEnum):
    CHANNEL = "channel"
    SEGMENTATION = "segmentation"
    MASK = "mask"

class DiscoveryMode(StrEnum):
    SUBDIRECTORY = "subdirectory"
    TOKEN = "token"

class CompressMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"

@dataclass(frozen=True)
class DatasetSpec:
    """Immutable discovery result — what was found on disk."""
    name: str                          # Display name (subdirectory name or token group)
    source_dir: Path | None            # Subdirectory path (subdirectory mode)
    files: tuple[DiscoveredFile, ...]  # Files belonging to this dataset
    output_path: Path                  # Target .h5 path
    tile_config: TileConfig | None = None
    flim_config: FlimConfig | None = None

@dataclass
class DatasetGuiState:
    """Mutable GUI state — user selections for a dataset. Lives in the dialog, not io/."""
    checked: bool = True
    layer_assignments: dict[Path, LayerAssignment] | None = None  # Manual mode only
    tile_config_override: TileConfig | None = None   # User override of detected config
    flim_config_override: FlimConfig | None = None    # User override of detected config

@dataclass(frozen=True)
class FlimConfig:
    """FLIM calibration parameters. Named *Config to match TokenConfig/TileConfig."""
    frequency_mhz: float = 80.0
    channel_calibrations: tuple[tuple[float, float], ...] = ()  # (phase, modulation) per channel

@dataclass(frozen=True)
class LayerAssignment:
    """Per-file layer type and name override."""
    layer_type: LayerType = LayerType.CHANNEL
    name: str = ""                     # User-editable name, defaults to auto-derived

@dataclass
class CompressConfig:
    """Collected settings for a batch compress operation."""
    z_project_method: str = "mip"
    token_config: TokenConfig = field(default_factory=TokenConfig)
    output_dir: Path | None = None     # Override output directory
    datasets: list[DatasetSpec] = field(default_factory=list)
    gui_states: dict[str, DatasetGuiState] = field(default_factory=dict)  # keyed by dataset name

@dataclass(frozen=True)
class DatasetResult:
    name: str
    output_path: Path

@dataclass(frozen=True)
class DatasetError:
    name: str
    error_message: str

@dataclass(frozen=True)
class BatchResult:
    completed: tuple[DatasetResult, ...]
    failed: tuple[DatasetError, ...]
    cancelled: bool = False
```

### Research Insights: Data Model

**Frozen vs mutable split (from Python reviewer + pattern specialist):**
- Existing io/models.py convention: value objects are frozen (`TokenConfig`, `TileConfig`, `DiscoveredFile`), accumulated state is mutable (`ScanResult`).
- `DatasetSpec` is a value object (what was found on disk) — should be frozen. Mutable GUI state (`checked`, `layer_assignments`, config overrides) lives in `DatasetGuiState` in the dialog, not in io/models.py.
- `BatchResult` is a post-hoc result — frozen with tuples, not mutable lists.

**StrEnum for typed options (from Python reviewer):**
- Existing `TileConfig` validates `grid_type` in `__post_init__`. But `StrEnum` is cleaner for Python 3.12 and catches typos at assignment time, not just at construction.
- Apply to `LayerType`, `DiscoveryMode`, `CompressMode`.

**FlimConfig naming (from pattern specialist):**
- Rename `FlimParams` → `FlimConfig` to match `TokenConfig`/`TileConfig` convention.
- Define explicitly as a frozen dataclass (currently just an untyped dict in `import_dataset()`).

**Single property for dialog output (from pattern specialist):**
- `CompressDialog` should expose a single `@property compress_config -> CompressConfig` that materializes all dialog state into one object with no widget references. Launcher calls it once after `exec_()`.

### Implementation Phases

#### Phase 1: Naming Changes + Compress/Load Separation

Rename buttons and decouple compress from load. This is a minimal refactor of the existing code.

**Tasks:**
- [x] Rename "Import TIFF Dataset..." → "Compress TIFF Dataset..." in `gui/launcher.py:253`
- [x] Rename "Load Existing .h5 Dataset..." → "Load Dataset..." in `gui/launcher.py:257`
- [x] Remove "Batch Import" placeholder from `gui/launcher.py:279`
- [x] Modify `_on_import_dataset()` to NOT auto-load after compress — remove the `_on_import_finished()` → `_load_h5_into_viewer()` call chain
- [x] Show a success status message with the output path instead, so the user knows where the `.h5` was created
- [ ] Verify "Load Dataset" still works independently

**Files:**
- `src/percell4/gui/launcher.py` (lines 240-280 button labels, lines 812-920 handlers)

**Success criteria:**
- [x] "Compress TIFF Dataset..." creates `.h5` without loading into viewer
- [x] "Load Dataset..." opens file picker and loads `.h5` into viewer
- [x] No behavior change to the actual compression pipeline

#### Phase 2: Data Models + Dataset Discovery

Build the discovery engine that identifies datasets from a directory.

**Tasks:**
- [x] Add `StrEnum` types (`LayerType`, `DiscoveryMode`, `CompressMode`) to `io/models.py`
- [x] Add `DatasetSpec` (frozen), `FlimConfig` (frozen), `LayerAssignment` (frozen) to `io/models.py`
- [x] Add `DatasetGuiState`, `CompressConfig`, `DatasetResult`, `DatasetError`, `BatchResult` (frozen) to `io/models.py`
- [x] Create discovery functions in `io/scanner.py` (or `io/discovery.py` if logic exceeds ~100 lines):
  - `discover_by_subdirectory(root: Path, token_config: TokenConfig) -> list[DatasetSpec]`
  - `discover_by_token(root: Path, token_config: TokenConfig, group_token: str) -> list[DatasetSpec]`
- [x] **Subdirectory mode**: list immediate children of root that are directories, run `FileScanner.scan()` on each, skip empty results. If root itself contains TIFFs (no subdirectories), treat root as a single dataset.
- [x] **Token mode**: scan all files in root (non-recursive), group by a user-specified group token regex, each group becomes a `DatasetSpec`
- [x] Auto-generate `output_path` for each dataset: `{output_dir or source_parent}/{dataset_name}.h5`
- [x] Validate discovery results: warn on empty datasets, warn on zero-TIFF subdirectories
- [x] Handle edge case: root contains both TIFFs and subdirectories — treat root-level TIFFs as a separate dataset named after root

**Research Insights: Discovery**

- **Two explicit functions instead of mode dispatch** (from Python reviewer): `discover_by_subdirectory()` and `discover_by_token()` have different required parameters. Two functions are clearer than one function with conditionally-relevant args.
- **Narrow rglob pattern** (from performance oracle): Change `rglob("*")` to `rglob("*.tif")` + `rglob("*.tiff")` in `FileScanner.scan()`. This lets the OS filter at the filesystem level, significantly faster on network drives.
- **Simplification note**: Token-based discovery could be deferred to a later iteration if scope needs to be cut. Subdirectory discovery covers the primary use case.

**Files:**
- `src/percell4/io/models.py` — new dataclasses and enums
- `src/percell4/io/scanner.py` — discovery functions (or `io/discovery.py` if substantial)

**Success criteria:**
- [x] `discover_by_subdirectory("/path/to/experiment", ...)` returns a `DatasetSpec` per subdirectory
- [x] `discover_by_token("/path/to/flat_dir", ..., group_token=r"(sample\d+)")` groups files by token
- [x] Empty subdirectories produce a warning, not an error
- [x] Single dataset in root detected correctly

#### Phase 3: CompressDialog — Auto Mode

Build the new dialog, starting with Auto mode only.

**Tasks:**
- [x] Create `gui/compress_dialog.py` with `CompressDialog(QDialog)`
- [x] **Source bar**: directory `QLineEdit` + "Browse..." button, output directory `QLineEdit` + "Browse..." button (defaults to source parent)
- [x] **Discovery mode**: `QComboBox` with "Subdirectory" (default) and "Group by Token"
- [x] When "Group by Token" is selected, show a `QLineEdit` for the grouping regex pattern
- [x] **Auto/Manual toggle**: two `QRadioButton`s — "Auto" (default) and "Manual"
- [x] **Dataset tree** (extract as `DatasetTreeWidget(QTreeWidget)` subclass):
  - Columns: checkbox + name, file count, detected channels, output .h5 name
  - Top-level items = datasets, child items = files (collapsed by default)
  - `Qt.ItemIsUserCheckable` + `Qt.ItemIsAutoTristate` on parent nodes
  - Manual tri-state propagation with `blockSignals` as backup (see research)
  - "Select All" / "Deselect All" buttons above tree
  - Store `DatasetSpec` in `item.data(0, Qt.UserRole)` for each item
- [x] **Global settings**: z-projection `QComboBox`, token patterns section (collapsible `QGroupBox`, default collapsed)
- [x] **Action bar**: "Compress" `QPushButton` + "Cancel" `QPushButton`
- [x] On "Browse" for source: call discovery, populate tree
- [x] On discovery mode change: re-run discovery with generation counter guard
- [x] Single `@property compress_config` returning materialized `CompressConfig`
- [x] Apply the same dark theme styling as existing `ImportDialog._apply_style()`
- [x] Replace `ImportDialog` usage in `launcher.py` — wire "Compress TIFF Dataset..." to open `CompressDialog`

**Research Insights: Tree Widget**

**Delegate strategy (from Qt best practices researcher):**
- Use `QStyledItemDelegate` for file-level tree items (3,500+ items). Delegates paint without creating real widgets — far cheaper than `setItemWidget()`.
- Use `openPersistentEditor()` on parent-level items only (~50) if always-visible combos are needed in Manual mode.
- Performance budget: creating 3,500 `QTreeWidgetItem`s takes ~20-40ms. `setItemWidget` on 3,500 items would take ~500ms+.

**Eager population (from Qt best practices researcher):**
```python
self._tree.setUpdatesEnabled(False)
self._tree.blockSignals(True)
try:
    self._tree.clear()
    for ds in datasets:
        parent = QTreeWidgetItem(self._tree, [ds.name, "", str(len(ds.files))])
        parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        parent.setCheckState(0, Qt.Checked)
        parent.setData(0, Qt.UserRole, ds)
        for f in ds.files:
            child = QTreeWidgetItem(parent, [f.path.name])
            child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
            child.setCheckState(0, Qt.Checked)
finally:
    self._tree.blockSignals(False)
    self._tree.setUpdatesEnabled(True)
```

**Checkbox vs selection — separate signals (from race condition reviewer):**
- `itemChanged` for checkbox state (update `DatasetGuiState.checked`). Filter by `column != 0` to ignore non-checkbox changes.
- `currentItemChanged` for side panel loading. Independent concern.

**Discovery re-run guard (from race condition reviewer):**
```python
def _on_discovery_mode_changed(self):
    self._discovery_generation += 1
    gen = self._discovery_generation
    results = discover_by_subdirectory(...)  # or token
    if gen != self._discovery_generation:
        return  # superseded
    self._populate_tree(results)
```

**Dark theme stylesheet**: Match existing `#1e1e1e` bg, `#4ea8de` accent, `#2a2a2a` inputs. Style `QTreeWidget::indicator` for checkbox visuals (`:checked`, `:unchecked`, `:indeterminate`).

**Files:**
- `src/percell4/gui/compress_dialog.py` — new file
- `src/percell4/gui/launcher.py` — rewire button handler

**Success criteria:**
- [x] Dialog opens, user browses to directory, datasets appear in tree
- [x] User can check/uncheck datasets with tri-state parent propagation
- [x] "Select All" / "Deselect All" work
- [x] Single dataset discovered → tree shows 1 item (no batch complexity)
- [x] Dialog returns a valid `CompressConfig` on accept

#### Phase 4: Batch Compression Engine + Progress

Build the orchestration that compresses multiple datasets with progress.

**Tasks:**
- [ ] Create `io/batch.py` with `batch_compress(datasets, config, progress_callback, cancelled_fn) -> BatchResult`
  - Pure function, no Qt imports, no QObject — just a plain callable
  - Iterates through datasets
  - For each: builds `import_dataset()` args, calls it with progress callback
  - On success: append to completed list
  - On failure: catch exception, append to failed list, continue
  - Check `cancelled_fn()` between datasets AND within progress callback
- [ ] **Pre-flight collision check** in the GUI layer (before calling `batch_compress`):
  - Check all output paths before starting
  - If collisions found: show dialog "N files already exist. Overwrite all?" [Yes / No / Cancel]
  - Filter out skipped datasets before passing to `batch_compress()`
- [ ] **Window-modal QProgressDialog** (in `compress_dialog.py`):
  - `QProgressDialog("Compressing...", "Cancel", 0, n_datasets, self)`
  - `progress.setWindowModality(Qt.WindowModal)` — blocks parent dialog, prevents re-entrancy
  - `progress.setMinimumDuration(0)` — show immediately (default 4000ms is too slow)
  - Progress callback wired to `progress.setValue()` (which internally calls `processEvents`)
  - `progress.wasCanceled()` checked in the callback and between datasets
- [ ] **Deep-copy config snapshot** before starting compression:
  ```python
  config_snapshot = copy.deepcopy(self.compress_config)
  # Batch compressor works from frozen snapshot, dialog state cannot interfere
  ```
- [ ] **Cancellation via progress callback exception**:
  ```python
  def progress_callback(current, total, msg):
      progress.setValue(current)
      progress.setLabelText(f"({dataset_idx+1}/{n}) {msg}")
      if progress.wasCanceled():
          raise CompressCancelled()
  ```
  This gives 5 cancellation checkpoints per dataset (one per `import_dataset` stage). Consider adding a checkpoint inside the tile-reading loop for large stitched datasets.
- [ ] On complete: show summary dialog — N succeeded, M failed, list failures with error messages
- [ ] Wire `CompressDialog` "Compress" button → pre-flight → `batch_compress()` → summary

**Research Insights: Progress + Threading**

**processEvents is safe when using window-modal QProgressDialog (from race condition reviewer + framework docs):**
- `QProgressDialog.setValue()` internally calls `processEvents()`, so no explicit call needed.
- `Qt.WindowModal` absorbs all input events for the parent — user cannot click Compress again, cannot close the dialog, cannot modify the tree. Only Cancel on the progress dialog is active.
- This is far safer than raw `processEvents()` with manually disabled widgets (easy to miss one).

**io/batch.py stays GUI-agnostic (from architecture strategist):**
- `batch_compress()` is a plain function accepting `Callable` for progress and cancellation.
- It does NOT import Qt, does NOT call processEvents, does NOT know about QProgressDialog.
- The GUI layer wraps the callback to bridge to QProgressDialog.

**Error handling (from architecture strategist):**
- Failed datasets don't crash the batch. Exceptions caught per-dataset, collected in `BatchResult.failed`.
- Partial .h5 files: rely on `DatasetStore.create_atomic()` (write to temp, rename on success). If `import_dataset()` fails mid-write, the temp file is never renamed — no corrupt .h5 left behind.

**Performance note (from performance oracle):**
- The current `progress_callback` in `import_dataset()` fires at only 5 coarse points. For smooth progress within a single large dataset, consider adding callbacks inside the tile-reading loop.
- Current `progress_callback` is never wired up from the launcher (line 862) — this needs to be fixed.

**Files:**
- `src/percell4/io/batch.py` — new module (GUI-agnostic)
- `src/percell4/gui/compress_dialog.py` — progress dialog integration
- `src/percell4/gui/launcher.py` — handler wiring

**Success criteria:**
- [x] Batch of N datasets compresses sequentially with progress updates
- [x] Failed datasets don't crash the batch — errors collected and shown at end
- [x] Cancel stops the batch (raises from progress callback, caught by batch function)
- [x] Pre-flight collision detection with simple overwrite confirmation
- [x] GUI remains responsive and safe from re-entrancy (window-modal dialog)
- [ ] No partial .h5 files left on failure (atomic writes)

#### Phase 5: CompressDialog — Manual Mode

Add manual mode features: layer type dropdowns, editable names, side panel.

> **Simplification note:** This phase roughly doubles implementation effort. The core batch compress value (auto mode) ships in Phases 1-4. Consider deferring Phase 5 to a separate iteration if scope needs to be cut.

**Tasks:**
- [ ] **Per-group layer type dropdown**: use `QStyledItemDelegate` with `openPersistentEditor()` on channel-group items
  - Group files by their auto-detected token group (e.g., all ch0 tiles together). The dropdown applies to the group, not individual tiles — avoids the 72-file-assignment problem.
  - Options: "Channel" (default), "Segmentation", "Mask"
- [ ] **Per-group editable name**: editable column via delegate
  - Defaults to auto-derived name from token (e.g., "ch0")
  - User can rename to "GFP", "DAPI", etc.
- [ ] **Side panel** (`DatasetConfigPanel` QWidget):
  - Shows when a dataset is clicked in the tree (single-click selection, separate from checkbox)
  - **Stitching section**: enabled checkbox, grid rows/cols `QSpinBox`, grid type `QComboBox`, start corner `QComboBox`. Only shown when tiles are detected for the selected dataset.
  - **FLIM section**: enabled checkbox, laser frequency `QDoubleSpinBox`, per-channel calibration table (phase, modulation). Only shown when TCSPC files detected.
  - Panel state saved to `DatasetGuiState` when selection changes (before loading new dataset's config)
  - Empty state when no dataset selected: "Select a dataset to configure"
- [ ] **Layer type validation**: when "Segmentation" is assigned, validate integer values on compress. Float → int32 with warning.
- [ ] **Auto→Manual transition**: preserve auto-detected channel assignments as defaults in the tree
- [ ] **Manual→Auto transition**: warn that manual assignments will be lost, confirm before switching
- [ ] Collect `LayerAssignment`s into `DatasetGuiState.layer_assignments` on compress

**Research Insights: Manual Mode**

**Side panel save/load race (from race condition reviewer):**
```python
def _on_dataset_selected(self, current, previous):
    if self._switching_dataset:
        return
    self._switching_dataset = True
    try:
        if previous is not None:
            self._save_panel_to_gui_state(previous)
        if current is not None:
            self._load_gui_state_to_panel(current)
        else:
            self._clear_panel()
    finally:
        self._switching_dataset = False
```
- **blockSignals on ALL panel widgets during programmatic load** — prevents `valueChanged` signals from overwriting other datasets' state.
- **Save before compress too** — not just on selection change. The last-edited dataset's config is only in the widgets until explicitly saved.

**Layer assignment at orchestration level (from Python reviewer):**
- Don't modify `import_dataset()` signature. Instead, the batch orchestrator separates files by layer type before calling import:
  - Channel files → `import_dataset()` as normal
  - Segmentation files → `store.write_labels(name, array)` after reading
  - Mask files → `store.write_mask(name, array)` after reading
- This keeps `import_dataset()` focused on its current responsibility.

**Signal coalescing (from learnings):**
- If rapid selection changes cause multiple save/load cycles, use `QTimer.singleShot(0, self._deferred_load)` with a `_pending` flag to coalesce into a single update.

**Disable mode toggle during tree population (from race condition reviewer):**
- Switching modes while the tree is being populated can corrupt widget state. Disable the Auto/Manual radio buttons during population.

**Importer changes** (`io/importer.py`):
- [ ] Add optional `files: list[DiscoveredFile] | None` parameter to `import_dataset()` to skip redundant re-scanning when called from batch
- [ ] Layer assignment handling lives in the batch orchestrator, not in `import_dataset()`

**Files:**
- `src/percell4/gui/compress_dialog.py` — manual mode widgets, side panel
- `src/percell4/io/importer.py` — optional `files` parameter
- `src/percell4/io/models.py` — LayerAssignment already added in Phase 2

**Success criteria:**
- [ ] Manual mode shows per-group dropdowns and editable names
- [ ] Stitching config in side panel persists per-dataset (with guard flag)
- [ ] FLIM config in side panel persists per-dataset
- [ ] Segmentation/mask files written to correct HDF5 groups
- [ ] Mode switch preserves/warns appropriately
- [ ] No save/load race conditions on rapid clicking

## Performance Optimizations

These are existing pipeline improvements identified by the performance oracle. They benefit both single and batch compress and can be done opportunistically alongside or after the main phases.

### P-OPT-1: Stream tiles into pre-allocated grid (High impact)

Current `_load_and_stitch()` in `importer.py:408-439` loads ALL tiles into a dict, then copies to the output grid. For a 6x6 grid of 2048x2048 uint16 tiles: ~576 MB peak (tiles dict + output).

**Fix:** Allocate the output grid first, then read and place tiles one at a time:
```python
output = np.zeros((out_h, out_w), dtype=dtype)
for tile_idx, f in tile_files.items():
    img = read_tiff(f.path)["array"]
    row, col = positions[tile_idx]
    output[row*th:(row+1)*th, col*tw:(col+1)*tw] = img
    del img  # free immediately
```
**Impact:** Peak memory drops from ~576 MB to ~296 MB per channel.

### P-OPT-2: Eliminate double `.bin` file reads (High impact)

`.bin` files are read once for intensity extraction (`importer.py:230-251`), deleted, then read again for HDF5 streaming write (`importer.py:346-369`). For a 6x6 grid: ~5 GB of redundant I/O.

**Fix:** Merge into single pass — read each tile, extract intensity, write decay to HDF5, delete.

### P-OPT-3: Fix `assemble_channels` redundant copy

`assembler.py:140` does `np.stack(...).astype(np.float32)` — but inputs are already float32. Remove the `.astype()` call or use `copy=False`. For 4 channels of 12288x12288: saves ~2.3 GB peak.

### P-OPT-4: Use streaming z-projection

`project_z()` already has a streaming mode but the importer builds the full `z_images` list first. Wire up the streaming path to reduce peak memory from N * image_size to 2 * image_size.

### P-OPT-5: Narrow rglob pattern

Change `rglob("*")` to `rglob("*.tif")` + `rglob("*.tiff")` in `FileScanner.scan()`. Faster on network drives.

## Alternative Approaches Considered

1. **Separate dialogs for single vs batch** — Rejected because it creates two code paths and forces users to choose upfront. Auto-detection is simpler.

2. **QThread worker for batch** — Rejected for now due to known bus errors with QThread + external drive I/O (documented in `launcher.py:853`). Window-modal `QProgressDialog` with `setValue()` (which calls `processEvents` internally) is the pragmatic solution. Can revisit if the bus error root cause is identified (may be h5py thread-safety, not a fundamental Qt issue).

3. **Drag-and-drop zone for layer assignment** — Rejected in favor of per-group dropdowns for simplicity and lower implementation complexity.

4. **Dual-list transfer for dataset selection** — Rejected in favor of tree view with checkboxes, which also shows file details and supports expand/collapse.

5. **Raw `processEvents()` without modal protection** — Rejected after race condition review identified critical re-entrancy risks (second compress during batch, dialog close during write, tree interaction during compression). Window-modal QProgressDialog is the safe alternative.

## Acceptance Criteria

### Functional Requirements

- [ ] "Compress TIFF Dataset..." opens the new `CompressDialog`
- [ ] "Load Dataset..." opens a file picker for `.h5` files (renamed from "Load Existing .h5 Dataset...")
- [ ] Subdirectory-based discovery: each child directory = one dataset
- [ ] Token-based discovery: files grouped by user-specified regex
- [x] Single dataset discovered → simplified view (no batch complexity)
- [ ] Multiple datasets → tree view with checkboxes, Select All / Deselect All
- [ ] Auto mode: all files become channels, names from tokens, one click to compress
- [ ] Manual mode: per-group layer type dropdowns (Channel/Segmentation/Mask), editable names
- [ ] Per-dataset stitching config in side panel
- [ ] Per-dataset FLIM config in side panel
- [ ] Global z-projection setting
- [ ] Collapsible advanced token pattern section
- [ ] Output path defaults to source parent, user can override
- [ ] Pre-flight collision check with overwrite confirmation
- [ ] Progress dialog with dataset counter, name, progress bar, Cancel
- [ ] Failed datasets don't crash batch — errors collected and summarized
- [ ] Cancel finishes current dataset then stops
- [ ] Compress never loads into viewer

### Non-Functional Requirements

- [ ] GUI remains responsive and safe from re-entrancy (window-modal QProgressDialog)
- [ ] Memory-safe for large tile scans (streaming writes, pre-allocated grids)
- [ ] Dark theme consistent with existing dialogs (#1e1e1e bg, #4ea8de accent)
- [ ] io/ modules have zero Qt imports — testable without GUI
- [x] No partial .h5 files on failure (atomic writes via temp+rename)

## Dependencies & Prerequisites

- Existing `import_dataset()` pipeline (no changes needed for Phase 1-4)
- `FileScanner` from `io/scanner.py`
- `DatasetStore` from `store.py` (uses `create_atomic()` for crash safety)
- `Worker` class pattern from `gui/workers.py` (reference only — not used directly)

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Re-entrancy during processEvents | **Critical** | Window-modal QProgressDialog + deep-copy config snapshot. setValue() calls processEvents safely within the modal context. |
| Side panel state race on rapid clicks | High | Guard flag (`_switching_dataset`) + blockSignals on all panel widgets during programmatic load. Save before compress, not just on selection change. |
| Large batch exhausts disk space mid-compress | Medium | Pre-flight: check available disk space vs estimated output size. Fail fast. |
| Token-based grouping with bad regex | Medium | Show preview of groups before compress. Validate regex on input. |
| Discovery double-populate on mode switch | Medium | Generation counter pattern: increment on each discovery, discard stale results. |
| User assigns float image as Segmentation | Low | Validate at compress time. Cast to int32 with warning. |
| Redundant scanning (discovery + import) | Low | Pass pre-scanned files to import_dataset() via optional `files` parameter. |
| Memory spike during tile stitching | Medium | Stream tiles into pre-allocated grid (P-OPT-1). |

## Gotchas from Institutional Learnings

These are documented issues from `docs/solutions/` that directly apply:

1. **Dialog value capture timing** (`percell4-flim-phasor-troubleshooting.md`) — Capture all CompressDialog values into a `CompressConfig` object immediately after `exec_()` returns. Do not access widget properties during compression. Call `dialog.deleteLater()` only after extraction.

2. **Thread-unsafe progress callbacks** (`percell4-code-review-findings-phases-0-6.md`) — Never pass a GUI-touching callback to background work. Even on the main thread, design the callback interface as a plain `Callable` in io/batch.py. The GUI layer wraps it.

3. **Signal blocking during repopulation** (`napari-mask-layer-misclassified-as-segmentation.md`) — When repopulating combos, the tree, or side panel widgets, wrap with `blockSignals(True/False)` to prevent spurious signal cascades. `QComboBox.clear()` + `addItem()` fires `currentTextChanged` for the first item added to an empty combo.

4. **Signal coalescing for rapid updates** (`percell4-selection-filtering-multi-roi-patterns.md`) — When tree selection changes fire rapidly, use `QTimer.singleShot(0, callback)` with a `_pending` flag to coalesce into a single side-panel update.

5. **Streaming HDF5 writes for large arrays** (`percell4-flim-phasor-troubleshooting.md`) — Never allocate a full stitched array in memory for multi-GB datasets. Use HDF5 region writes: `dataset[y0:y1, x0:x1, :] = tile_data`.

6. **Layer type classification** (`napari-mask-layer-misclassified-as-segmentation.md`) — When writing masks vs segmentation to HDF5, use the existing `store.write_labels()` / `store.write_mask()` distinction. Maintain `mask_set` and `segmentation_set` tracking.

## References & Research

### Internal References
- Current import dialog: `src/percell4/gui/import_dialog.py`
- Import pipeline: `src/percell4/io/importer.py:25` (`import_dataset()`)
- Scanner: `src/percell4/io/scanner.py:17` (`FileScanner`)
- Data models: `src/percell4/io/models.py`
- Assembler: `src/percell4/io/assembler.py`
- DatasetStore: `src/percell4/store.py`
- Launcher I/O panel: `src/percell4/gui/launcher.py:240-280`
- Import handler: `src/percell4/gui/launcher.py:812-887`
- Worker pattern: `src/percell4/gui/workers.py`
- Checkbox pattern: `src/percell4/gui/launcher.py:1470-1499`

### Institutional Learnings Applied
- `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`
- `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`
- `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md`
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md`

### PerCell3 Reference
- TUI import flow: `/Users/leelab/percell3/src/percell3/cli/menu.py:1477-1657`
- Auto-import: `/Users/leelab/percell3/src/percell3/cli/import_cmd.py`
- Scanner with FOV derivation: `/Users/leelab/percell3/src/percell3/io/scanner.py`

### Brainstorm
- `docs/brainstorms/2026-04-04-batch-compress-brainstorm.md`
