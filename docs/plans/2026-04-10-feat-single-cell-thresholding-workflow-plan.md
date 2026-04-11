---
title: "feat: Single-cell thresholding analysis workflow"
type: feat
date: 2026-04-10
deepened: 2026-04-10
brainstorm: docs/brainstorms/2026-04-10-single-cell-thresholding-workflow-brainstorm.md
---

# feat: Single-cell thresholding analysis workflow

## Enhancement Summary

**Deepened on:** 2026-04-10

**Research agents used:** `kieran-python-reviewer`, `architecture-strategist`, `code-simplicity-reviewer`, `performance-oracle`, `pattern-recognition-specialist`, `best-practices-researcher`, `framework-docs-researcher`, `spec-flow-analyzer`, `learnings-researcher`.

### Scope cuts from user review after deepening

Per `code-simplicity-reviewer` feedback, two v1 features are **cut**:

- **Pause / Resume subsystem** — no `run_state.json`, no "Resume run..." button, no auto-checkpointing, no resume entry point. A running workflow can be **cancelled** (aborts the run; labels/masks already written to h5 are left as-is) but not resumed.
- **Back button** — QC nav bars have Accept & Next + Cancel only. Users who regret an earlier dataset's QC must cancel and restart the run.

**Kept**: the CSV column picker at config time (user explicitly chose this in the brainstorm).

### Key improvements over the initial plan

1. **Correctness: JSON roundtrip rewritten.** `dataclasses.asdict` + `WorkflowConfig(**json.load(...))` does not reconstruct nested dataclasses or convert `Path` fields. Replaced with explicit `config_to_dict` / `config_from_dict` helpers, a single `_json_default` encoder, a schema version field, and a unit-tested roundtrip fixture. Used for writing `run_config.json` only.
2. **Correctness: exception safety.** Top-level `try/except/finally` in the runner that unconditionally unlocks the launcher, restores child windows, and stamps `finished_at` into `run_config.json` on any exception. Atomic writes (`.tmp` + `os.replace`) for all artifact files.
3. **Architecture: split Qt-agnostic from Qt.** `src/percell4/workflows/` is pure Python (models, artifacts I/O, channel intersection, pure phase helpers). All Qt code (runner, dialogs, QC controller, queue wrapper) lives under `src/percell4/gui/workflows/`. Enforces the same rule as `src/percell4/io/` and gives us unit tests without a `QApplication`.
4. **Architecture: `WorkflowHost` Protocol.** The runner depends on a 6-method protocol, not `LauncherWindow` directly. Runner is launchable in tests with a `FakeHost`.
5. **Performance: single-pass measurement.** `measure_multichannel` takes only one mask, so the naive plan called it N+1 times per dataset, re-running `find_objects` and `regionprops` 24× for a 6-channel × 3-round run. New additive helper `measure_multichannel_with_masks(images, labels, masks={round: mask})` runs one pass per dataset and eliminates the rename/merge step entirely.
6. **Performance: session-mode HDF5, reused napari layers, hoisted Cellpose model, Parquet hints, staged Phase 7 writes.** See the Performance Optimizations section.
7. **Patterns: one aggregated signal + Controller (not Dialog) for seg QC.** Matches `CellDataModel.state_changed` + `ThresholdQCController` conventions. Also fixes `QMessageBox.critical/information` usage (codebase uses only `warning`/`question`).
8. **SpecFlow: explicit failure model.** Per-dataset failure states (`segmentation_empty`, `threshold_empty`, `measurement_error`, `compress_failed`) are first-class and flow through the runner, the run config, and the export step. Per-dataset failures never crash the run.
9. **Tiff-pending channel intersection bug.** The initial plan validated intersection at Start — but `tiff_pending` datasets have no h5 yet. Fixed by reading channel names from `CompressConfig.datasets[i]` at Start, and re-validating after Phase 0 once the h5 files exist.
10. **Child-window handling.** `CellTableWindow`, `DataPlotWindow`, `PhasorPlotWindow` are closed on workflow start (remembering which were open) and restored on finish/cancel. Locking only the launcher panels is insufficient because those child windows listen to `CellDataModel` signals and would thrash on every dataset swap.

### New considerations discovered

- **Round-name regex validation** — must be HDF5-path-safe AND pandas-column-name-safe (`^[A-Za-z_][A-Za-z0-9_\-]*$`, max 40 chars).
- **Reentrance guard** on the Start button while a workflow is already running.
- **Keyboard shortcuts** for the QC nav bars (`Ctrl+Enter` Accept, `Esc` Cancel).
- **`setValue()` re-entrancy discipline** — never mutate `CellDataModel` from inside a `QProgressDialog` loop because `setValue()` calls `processEvents()`.

---

## Overview

A first-class, multi-dataset batch workflow that takes a set of experiments from raw input (mixed `.h5` files and `.tiff` sources) through Cellpose segmentation, multi-round grouped thresholding, per-cell measurement, and cross-dataset export. The workflow lives in the currently-empty **Workflows** tab as a single **"Single-cell thresholding analysis workflow"** entry. It is the first real entry in the Workflows tab and establishes the pattern for future batch workflows.

## Problem Statement / Motivation

PerCell4 today is strictly single-dataset. Every existing action operates on whichever dataset is currently loaded in the `CellDataModel`. Researchers doing multi-condition experiments currently have to compress each tiff dataset manually, load each `.h5` one at a time, re-run the same Cellpose settings, QC segmentation in the single-dataset panel, run grouped thresholding with the same config per round per dataset, click "Measure Cells" with the same metric selection, export a CSV, and remember what settings were used weeks later when reviewers ask.

This is error-prone, slow, and produces no provenance. The new workflow replaces it with a single configuration dialog, an unattended batch runner for automatic phases, and queued QC controllers for manual steps, writing a single `run_<timestamp>/` folder that captures everything — config, per-dataset provenance, cross-dataset measurements (Parquet), and user-selected CSV exports. All filtering is deferred to post-hoc analysis against the Parquet so a different cutoff never requires re-running segmentation.

## Proposed Solution

1. **Add two subpackages** — `src/percell4/workflows/` (Qt-agnostic: models, artifacts I/O, channel intersection, pure phase helpers, failure model, run log) and `src/percell4/gui/workflows/` (Qt: runner, config dialog, seg QC controller, threshold QC queue wrapper).
2. **Build one comprehensive config dialog** that collects datasets, Cellpose settings, an ordered list of thresholding rounds, the CSV column selection, and an output parent folder. Validates channel intersection on Start (with tiff-pending awareness) and re-validates after Phase 0.
3. **Implement a `SingleCellThresholdingRunner`** (subclass of a lightweight `BaseWorkflowRunner`) that drives the strict phase sequence via a Python generator driven by a `WorkflowHost` Protocol and closes/re-opens child windows during the run. Cancel is the only off-ramp; exception safety lives in a top-level `try/except/finally`.
4. **Reuse existing building blocks** — `import_dataset`, `run_cellpose`, `segment/postprocess`, `ThresholdQCController`, `measure_cells` — with one additive parameter on `ThresholdQCController`, one additive `model=` kwarg on `run_cellpose`, one additive helper `measure_multichannel_with_masks` in `measure/measurer.py`, and one additive `read_channel` method on `DatasetStore`.
5. **Add a slim segmentation QC controller** (`SegmentationQCController`, modeled on `ThresholdQCController` — not a `QDialog`) that bundles the delete / draw / edge-cleanup handlers from `SegmentationPanel` into a focused, queue-aware window.
6. **Aggregate measurements into a single DataFrame** across datasets and persist as Parquet + CSV outputs in the run folder. Measurements never land in any `.h5`.

## Technical Approach

### Architecture

```
src/percell4/workflows/              (Qt-agnostic — zero Qt imports)
  __init__.py
  models.py          WorkflowConfig, ThresholdingRound, CellposeSettings,
                     WorkflowDatasetEntry, RunMetadata,
                     DatasetFailure (StrEnum), all with __post_init__ validation
  artifacts.py       create_run_folder(), write/read run_config.json,
                     write_atomic(path, writer_fn) using `.tmp` + os.replace
  channels.py        intersect_channels(sources: list[ChannelSource]) ->
                     (intersected, outliers)
  phases.py          compress_one(), segment_one(), threshold_compute_one(),
                     measure_one(), export_run() — pure functions, no Qt
  failures.py        DatasetFailure enum + FailureRecord dataclass
  run_log.py         RunLog(folder) — jsonl audit trail helper
  host.py            WorkflowHost Protocol
  CLAUDE.md

src/percell4/gui/workflows/          (Qt driver)
  __init__.py
  base_runner.py     BaseWorkflowRunner(QObject) — generator-driven phase loop,
                     locking via WorkflowHost, exception-safe finally,
                     single `workflow_event = Signal(object)` carrying descriptor
  single_cell/
    runner.py        SingleCellThresholdingRunner(BaseWorkflowRunner)
    config_dialog.py WorkflowConfigDialog
    round_editor.py  inline QTableWidget round editor (no modal sub-dialog)
    seg_qc.py        SegmentationQCController (follows ThresholdQCController shape)
    threshold_qc_queue.py  per-dataset wrapper around ThresholdQCController
  CLAUDE.md

src/percell4/gui/launcher.py         (modified)
  - set_workflow_locked(locked) / is_workflow_locked property
  - workflows panel replaces dead placeholders with ONE button (Start)
  - implements WorkflowHost Protocol structurally

Runtime flow:
  Workflows tab click -> WorkflowConfigDialog.exec_()
    -> WorkflowConfig -> SingleCellThresholdingRunner(config, host)
    -> host.close_child_windows() + host.set_workflow_locked(True)
    -> runner generator yields phases in strict order
       - unattended phases: QProgressDialog(Qt.WindowModal) + main-thread loop
         (Cellpose is the one exception — Worker with hoisted model)
       - interactive phases: SegQCController / ThresholdQCController per dataset
         with a slim [Accept & Next] [Cancel] nav bar
    -> on finish/cancel/error (in a single finally block):
       host.set_workflow_locked(False) + host.restore_child_windows()
       stamp finished_at into run_config.json
```

### Research insights: existing code we reuse

- **`DatasetStore`** (`src/percell4/store.py:64`) — channel names at `store.metadata["channel_names"]`, `open_read()` session at `store.py:70-89` (64 MB chunk cache — reuse by wrapping per-dataset compute phases in a single `with` block).
- **`import_dataset`** (`src/percell4/io/importer.py:25`) — pure, no Qt, safe to call from `QProgressDialog` main-thread loop.
- **`CompressDialog`** (`src/percell4/gui/compress_dialog.py:44`) — nested inside the config dialog for tiff sources; `.compress_config.datasets` yields `DatasetSpec` plus **channel_names already populated** from the scan step (the key to fixing the tiff-pending intersection bug).
- **`run_cellpose`** (`src/percell4/segment/cellpose.py:25`) + `filter_edge_cells`, `filter_small_cells`, `relabel_sequential` (`src/percell4/segment/postprocess.py`) — pure functions.
- **`ThresholdQCController`** (`src/percell4/gui/threshold_qc.py:77`) — constructor takes everything up front; fires `on_complete(success, msg)` callback; not coupled to `GroupedSegPanel`. **Already the pattern we want to match** for `SegmentationQCController`.
- **`measure_cells` / `measure_multichannel`** (`src/percell4/measure/measurer.py:129,282`) — returns `{channel}_{metric}` columns plus `CORE_COLUMNS`. New helper `measure_multichannel_with_masks` shares `_iter_cell_crops` for the single-pass path.
- **`Worker(QThread)`** (`src/percell4/gui/workers.py:16-62`) — used only for Cellpose (Phase 1). Cooperative cancellation via `request_abort()` / `aborted` property.
- **`_run_batch_compress` pattern** (`src/percell4/gui/launcher.py:801-886`) — canonical main-thread + `QProgressDialog(Qt.WindowModal)` loop for unattended phases. Deliberately avoids `QThread` because of external-drive HDF5 bus errors (`docs/solutions/logic-errors/batch-compress-development-lessons.md`).

### Research insights: gaps the plan closes

1. **`ThresholdQCController` writes `/measurements`** (`threshold_qc.py:719`), violating the "h5 stays pure" rule. Fix: additive `write_measurements_to_store: bool = True` parameter (default preserves current behavior); workflow passes `False`. A 3-arg `on_complete(success, msg, measurements_df)` variant returns the computed DF to the caller so the workflow owns persistence — queued as tech debt for `GroupedSegPanel` migration (see **§Tech Debt Recorded**).
2. **`measure_multichannel` takes one mask**. Fix: add additive `measure_multichannel_with_masks(images, labels, metrics, masks={round_name: mask})` that does a **single pass** over cells reusing one `_iter_cell_crops` iteration. Column naming: `{ch}_{metric}` for whole-cell, `{ch}_{metric}_in_{round}` / `_out_{round}` for per-mask. No post-hoc rename/merge. Estimated speedup on Phase 7: ~5× for a 6-channel × 3-round run.
3. **No "lock launcher main UI" helper.** Add `LauncherWindow.set_workflow_locked(bool)` + `is_workflow_locked` property. Enumerates the exact widget set disabled (not just "action panels"): `_import_panel`, `_segmentation_panel`, `_analysis_panel`, the Workflows tab's Start button (reentrance guard), the File menu's Open/Close dataset items. Closes `CellTableWindow`, `DataPlotWindow`, `PhasorPlotWindow` (remembering which were open) via the `WorkflowHost` protocol; restores on finish/cancel/error.
4. **Tiff-pending channel intersection**: `intersect_channels` is retyped to take a list of `ChannelSource` records (each carrying a `name` and `channel_names: list[str]`), not a list of `DatasetStore`. For `h5_existing` entries it reads from `store.metadata["channel_names"]`; for `tiff_pending` entries it reads from `CompressConfig.datasets[i]` (populated by the scan step at `compress_dialog.py`'s discovery phase). After Phase 0, the runner **re-validates** that the freshly-written h5 files still match, pausing with an error dialog if not.
5. **pyarrow dependency**: add `pyarrow>=14` and `pytest-qt>=4.4` to `pyproject.toml`.
6. **Atomic writes**: all artifact writes (`run_config.json`, final `measurements.parquet`, CSVs, staging parquets) use a `write_atomic(path, writer_fn)` helper that writes to `path.with_suffix(path.suffix + ".tmp")` and calls `os.replace(tmp, path)` on success. Never `os.unlink()` first (per `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md`).

### Key data model (Python — Qt-agnostic)

```python
# src/percell4/workflows/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from percell4.measure.metrics import BUILTIN_METRICS  # tuple[str, ...]


class ThresholdAlgorithm(StrEnum):
    GMM = "gmm"
    KMEANS = "kmeans"


class GmmCriterion(StrEnum):
    BIC = "bic"
    SILHOUETTE = "silhouette"


class DatasetSource(StrEnum):
    H5_EXISTING = "h5_existing"
    TIFF_PENDING = "tiff_pending"


class DatasetFailure(StrEnum):
    COMPRESS_FAILED = "compress_failed"
    SEGMENTATION_EMPTY = "segmentation_empty"
    SEGMENTATION_ERROR = "segmentation_error"
    THRESHOLD_EMPTY = "threshold_empty"
    THRESHOLD_ERROR = "threshold_error"
    MEASUREMENT_ERROR = "measurement_error"


@dataclass(kw_only=True, slots=True, frozen=True)
class CellposeSettings:
    model: str = "cpsam"
    diameter: float = 30.0       # 0 = auto
    gpu: bool = True
    flow_threshold: float = 0.4
    cellprob_threshold: float = 0.0
    min_size: int = 15
    batch_size: int = 8          # tiles per GPU forward pass; unused on CPU
    channel_idx: int = 0         # which intensity channel feeds the segmenter
    # edge removal is always on for this workflow (invariant)


@dataclass(kw_only=True, slots=True, frozen=True)
class ThresholdingRound:
    name: str
    channel: str
    metric: str
    algorithm: ThresholdAlgorithm
    gmm_criterion: GmmCriterion = GmmCriterion.BIC
    gmm_max_components: int = 4
    kmeans_n_clusters: int = 3
    gaussian_sigma: float = 1.0

    def __post_init__(self) -> None:
        import re
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_\-]{0,39}$", self.name):
            raise ValueError(
                f"round name must match ^[A-Za-z_][A-Za-z0-9_\\-]{{0,39}}$, "
                f"got {self.name!r}"
            )
        if self.metric not in BUILTIN_METRICS:
            raise ValueError(f"metric must be one of {BUILTIN_METRICS}, got {self.metric!r}")
        if self.gmm_max_components < 2:
            raise ValueError("gmm_max_components must be >= 2")
        if self.kmeans_n_clusters < 2:
            raise ValueError("kmeans_n_clusters must be >= 2")
        if self.gaussian_sigma < 0:
            raise ValueError("gaussian_sigma must be >= 0")


@dataclass(kw_only=True, slots=True)
class WorkflowDatasetEntry:
    name: str
    source: DatasetSource
    h5_path: Path
    channel_names: list[str] = field(default_factory=list)  # seeded from source
    # For tiff_pending only: minimal info to drive import_dataset()
    compress_plan: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True, frozen=True)
class WorkflowConfig:
    """The recipe. Immutable once Start is clicked."""
    schema_version: int = 1
    datasets: list[WorkflowDatasetEntry]
    cellpose: CellposeSettings
    thresholding_rounds: list[ThresholdingRound]
    selected_csv_columns: list[str]
    output_parent: Path

    def __post_init__(self) -> None:
        if not self.datasets:
            raise ValueError("at least one dataset required")
        if not self.thresholding_rounds:
            raise ValueError("at least one thresholding round required")
        names = [r.name for r in self.thresholding_rounds]
        if len(set(names)) != len(names):
            raise ValueError(f"round names must be unique: {names}")


@dataclass(kw_only=True, slots=True)
class RunMetadata:
    """The instance. Separate from WorkflowConfig."""
    run_id: str
    run_folder: Path
    started_at: datetime
    finished_at: datetime | None = None
    intersected_channels: list[str] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)


@dataclass(kw_only=True, slots=True)
class FailureRecord:
    dataset_name: str
    phase_name: str
    failure: DatasetFailure
    message: str
    ts: datetime
```

**Serialization contract** (all in `workflows/artifacts.py`):
```python
def config_to_dict(cfg: WorkflowConfig) -> dict: ...
def config_from_dict(data: dict) -> WorkflowConfig: ...
def _json_default(obj):  # Path -> str, datetime -> isoformat, Enum -> value

def write_run_config(folder: Path, cfg: WorkflowConfig, meta: RunMetadata) -> None:
    # Single run_config.json file, schema-versioned, written atomically
def read_run_config(folder: Path) -> tuple[WorkflowConfig, RunMetadata]: ...
```
`run_config.json` is the only JSON artifact. It is written on Start (without `finished_at`) and rewritten on finish/cancel/error (with `finished_at` stamped). Schema version scaffolding is present but currently just version `1` — migrations will be added when the format changes.

### `WorkflowHost` protocol

```python
# src/percell4/workflows/host.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class WorkflowHost(Protocol):
    def set_workflow_locked(self, locked: bool) -> None: ...
    def show_workflow_status(self, phase_name: str, sub_progress: str) -> None: ...
    def get_viewer_window(self): ...     # -> ViewerWindow
    def get_data_model(self): ...        # -> CellDataModel
    def close_child_windows(self) -> None: ...   # closes cell_table, data_plot, phasor
    def restore_child_windows(self) -> None: ...  # reopens those that were open
```

`LauncherWindow` conforms structurally — no base class, no inheritance — enabling a `FakeHost` for unit tests.

### Cancel semantics

Cancel aborts the run entirely. There is no pause and no resume.

- **During an unattended phase** (Phase 0/1/3/5/.../7/8): the user clicks Cancel on the `QProgressDialog`. The cooperative cancel flag is checked at the *next dataset boundary* — the in-flight dataset runs to completion before the phase unwinds, so no h5 is left half-written. Any per-dataset artifacts already on disk (compressed h5, `/labels/cellpose_qc`, `/masks/<round>`, `/groups/<round>`) are left as-is.
- **During an interactive QC phase**: the user clicks Cancel on the QC controller's nav bar, or closes the window via the X button (trapped as Cancel with a "Cancel run and close?" confirmation). The controller's `_cleanup_all()` runs, the phase unwinds, the `finally` block unlocks the launcher and restores child windows.
- **On any uncaught exception** in the runner: same path as Cancel, with the exception traceback logged to `run_log.jsonl` and `run_config.json.finished_at` stamped with an error note.

The user can re-run the workflow from scratch; labels/masks that were already written to the h5 in a prior run are simply overwritten.

### Implementation phases

Nine implementation phases. Runtime phases (0–8) are the behavior the user sees; implementation phases are how the code lands in the repo.

#### Phase 1: Foundation — package scaffold, config models, atomic I/O, helpers

Stand up `workflows/`, the config dataclasses with full validation, atomic I/O helpers, and the small upstream fixes.

**Tasks:**
- [x] Add `pyarrow>=14` and `pytest-qt>=4.4` to `pyproject.toml` dependencies
- [x] Create `src/percell4/workflows/__init__.py`
- [x] Create `src/percell4/workflows/models.py` with all dataclasses above; every module in this subpackage starts with `from __future__ import annotations`
- [x] Create `src/percell4/workflows/failures.py` with `DatasetFailure` (StrEnum) + `FailureRecord`
- [x] Create `src/percell4/workflows/artifacts.py` with `write_atomic(path, writer_fn)` helper (`.tmp` + `os.replace`); `config_to_dict` / `config_from_dict`; `write_run_config` / `read_run_config`; `create_run_folder(output_parent) -> Path` using `datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S")` + short uuid suffix + `mkdir(exist_ok=False)` + `per_dataset/` and `staging/` subdirs
- [x] Create `src/percell4/workflows/channels.py` with `intersect_channels(sources: list[ChannelSource]) -> tuple[list[str], list[str]]` where `ChannelSource = tuple[str, list[str]]` — the function is Qt-agnostic and the Start handler builds the list from a mix of `store.metadata["channel_names"]` (for `h5_existing`) and `CompressConfig.datasets[i].channel_names` (for `tiff_pending`)
- [x] Create `src/percell4/workflows/run_log.py` with `RunLog(folder)` — writes `run_log.jsonl` with `log(phase, dataset, event, **fields)`; uses `write_atomic` append semantics (open `a`, `write`, `flush`, `fsync`)
- [x] Create `src/percell4/workflows/host.py` with `WorkflowHost` `Protocol`
- [x] Add `read_channel(hdf5_path: str, channel_idx: int) -> NDArray` to `DatasetStore` for channel-plane-only reads
- [x] Add `write_measurements_to_store: bool = True` parameter to `ThresholdQCController.__init__` at `src/percell4/gui/threshold_qc.py:77-102`; gate the `/measurements` write at `threshold_qc.py:719` on the flag. Default preserves current behavior.
- [x] Add optional `model=None` kwarg to `run_cellpose` at `src/percell4/segment/cellpose.py:25` — when provided, skip internal model construction and reuse the caller's model
- [x] Add `measure_multichannel_with_masks(images, labels, metrics=None, masks=None)` to `src/percell4/measure/measurer.py` — single-pass using `_iter_cell_crops`; column naming `{ch}_{metric}` + `{ch}_{metric}_in_{round}` + `{ch}_{metric}_out_{round}`
- [x] Add `LauncherWindow.set_workflow_locked(locked: bool)` + `is_workflow_locked` property in `src/percell4/gui/launcher.py`; enumerate disabled widgets: `_import_panel`, `_segmentation_panel`, `_analysis_panel`, Workflows tab Start button, File menu Open/Close dataset. Set the `statusBar().showMessage()` while locked
- [x] Add `LauncherWindow.close_child_windows()` / `restore_child_windows()` that close/reopen `CellTableWindow`, `DataPlotWindow`, `PhasorPlotWindow` (remembering which were open via a private `_child_windows_to_restore: set[str]`)
- [x] Create `src/percell4/workflows/CLAUDE.md` using the house template (`# src/percell4/workflows/` + one-paragraph summary + `## Modules` bulleted list)

**Files:**
- `pyproject.toml`
- `src/percell4/workflows/{__init__,models,failures,artifacts,channels,run_log,host,CLAUDE}.py|md`
- `src/percell4/store.py` (additive `read_channel`)
- `src/percell4/segment/cellpose.py` (additive `model=` kwarg)
- `src/percell4/measure/measurer.py` (additive `measure_multichannel_with_masks`)
- `src/percell4/gui/threshold_qc.py` (additive `write_measurements_to_store` flag)
- `src/percell4/gui/launcher.py` (lock helper + child window helpers)
- `src/percell4/CLAUDE.md` (add `workflows/` to subpackage index)

**Tests (`tests/workflows/`):**
- [x] `test_models.py` — `ThresholdingRound.__post_init__` rejects bad regex / bad metric / bad counts; `WorkflowConfig.__post_init__` rejects empty datasets and duplicate round names
- [x] `test_artifacts.py` — round-trip `WorkflowConfig` with a `Path`, a nested `list[ThresholdingRound]`, and a `datetime`; verify atomic write creates no residue on simulated error
- [x] `test_channels.py` — intersection of identical sets, outliers, empty intersection, one dataset
- [x] `test_measurer_with_masks.py` — synthetic 4-cell label + 2-channel image + 2 round masks; asserts the new helper returns the same numbers as two sequential `measure_multichannel` calls with post-hoc renaming, but in one pass
- [x] `test_read_channel.py` — `DatasetStore.read_channel` returns only the slice requested

**Success criteria:**
- [x] `pip install -e .` picks up `pyarrow` and `pytest-qt`
- [x] `pd.DataFrame({"a":[1]}).to_parquet(tmp.parquet, engine="pyarrow")` round-trips
- [x] `from percell4.workflows import models, artifacts, channels, run_log, host` resolves
- [x] `python -c 'import percell4.workflows'` with no `QApplication` created succeeds (Qt-free smoke test)
- [x] `grep -RIl 'qtpy\|PyQt5' src/percell4/workflows/` returns empty
- [x] `ThresholdQCController(write_measurements_to_store=False, ...)` writes `/masks/<name>` and `/groups/<name>` but NOT `/measurements` (verified via h5py)
- [x] `LauncherWindow.set_workflow_locked(True)` disables the enumerated widgets + sets the status-bar note; `False` restores

#### Phase 2: Runner — generator-driven state machine, exception safety, cancel

Build the Qt driver. **Note the split**: `BaseWorkflowRunner` is a generator-driven phase loop that avoids nested `QEventLoop` entirely by using `gen.send()` from signal handlers.

**Tasks:**
- [x] Create `src/percell4/gui/workflows/__init__.py`
- [x] Create `src/percell4/gui/workflows/base_runner.py` with `BaseWorkflowRunner(QObject)` exposing a **single** `workflow_event = Signal(object)` carrying a `WorkflowEvent` descriptor dataclass (fields: `kind`, `phase_name`, `current`, `total`, `dataset_name`, `sub_progress`, `success`, `message`, `run_folder`)
- [x] Define `WorkflowEvent(kind=Literal["phase_started", "phase_progress", "phase_completed", "qc_dataset_ready", "run_finished"], ...)` in `gui/workflows/base_runner.py`
- [x] Implement the generator-driven loop. Each phase is a Python generator that `yield`s a `PhaseRequest` dataclass describing what the driver should do next (`show_progress_dialog`, `run_worker`, `show_seg_qc`, `show_threshold_qc`). A slot on the user action calls `gen.send(result)` to resume. **No nested `QEventLoop.exec_()`**. Rationale: nested event loops are a Qt reentrancy footgun (`kieran-python-reviewer` flagged as Critical).
- [x] Implement the top-level `start(config, host)` wrapped in `try/except/finally`. The `finally` branch unconditionally calls `host.set_workflow_locked(False)`, `host.restore_child_windows()`, stamps `finished_at` into `run_config.json`, and emits `workflow_event(kind="run_finished")` exactly once. Any uncaught exception logs traceback to `run_log.jsonl`, emits `kind="run_finished", success=False, message=<error>`, then re-unwinds through the `finally`
- [x] Implement `request_cancel()` — sets a cooperative cancel flag. For interactive phases, picked up by the QC controller's next event handler. For unattended phases, picked up at the next dataset boundary (not mid-dataset, to keep h5 writes clean). On cancel, the phase unwinds through the `finally` block like a normal finish but emits `kind="run_finished", success=False, message="cancelled"`
- [x] **Re-entrance guard**: `host.set_workflow_locked(True)` must no-op if already locked; the launcher's Start button must check `is_workflow_locked` and do nothing if set
- [ ] `BaseWorkflowRunner.request_cancel()` also propagates to the in-flight Cellpose Worker via `Worker.request_abort()`
- [ ] Trap `QDialog.closeEvent` and `QMainWindow.closeEvent` on every workflow-owned window: X-button close is treated as Cancel with a "Cancel run and close?" confirmation

**Research insights: generator-driven phase loop vs nested `QEventLoop`**

Nested `QEventLoop.exec_()` works in PyQt5 but is the most common Qt-Python footgun — signals can arrive while paused, re-entering slots. `processEvents()` inside a nested loop corrupts Qt state. The generator-driven design (`_run(self) -> Generator[PhaseRequest, PhaseResult, None]`) avoids the whole problem: each phase yields a request, the runner's dispatch slot calls `gen.send(result)` from signal handlers that fire at natural Qt event boundaries. Also makes the runner unit-testable: drive the generator with fake `PhaseResult` objects and assert the emitted `WorkflowEvent` sequence without a `QApplication`.

**Files:**
- `src/percell4/gui/workflows/__init__.py`
- `src/percell4/gui/workflows/base_runner.py`
- `src/percell4/gui/workflows/CLAUDE.md`

**Tests:**
- [x] `tests/gui_workflows/test_base_runner_smoke.py` with `pytest-qt` + `qtbot` — drive a `FakeRunner` with a 3-dataset stub phase list; assert the emitted `WorkflowEvent` sequence
- [x] `test_cancel.py` — trigger cancel mid-phase; verify launcher unlocked, child windows restored, `run_finished(success=False, message="cancelled")` emitted exactly once
- [x] `test_exception_unlock.py` — raise inside a stub phase; assert `host.set_workflow_locked(False)` called, `host.restore_child_windows()` called, exactly one `run_finished(success=False)` emitted

**Success criteria:**
- [x] Raising an exception inside any phase driver unlocks the launcher, restores child windows, and emits exactly one `run_finished(success=False)`
- [x] Cancel during unattended phase K waits for the current dataset to finish, then unwinds cleanly
- [x] Runner is importable and unit-testable without a running `QApplication` (via the `PhaseRequest`/`PhaseResult` generator protocol)

#### Phase 3: Config dialog — dataset picker, settings, column picker

**Tasks:**
- [x] Create `src/percell4/gui/workflows/single_cell/config_dialog.py` with `WorkflowConfigDialog(QDialog)`
- [x] Expose config via `@property workflow_config(self) -> WorkflowConfig` (matching `CompressDialog.compress_config` at `compress_dialog.py:305-374`); Start button connects to `self.accept` with an `accept()` override that runs cross-field validation
- [x] **Dataset picker section** — `QTreeWidget` with four add-buttons:
  - [x] "Add .h5 files..." (multi-select file dialog)
  - [x] "Add folder of .h5 files..." (directory picker; recursive checkbox; globs `*.h5`)
  - [x] "Add .tiff source..." (nested `CompressDialog.exec_()` single-dataset mode; capture `dialog.compress_config` immediately after `exec_() == Accepted`, per `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`; read `datasets[0].channel_names` from the scan result)
  - [x] "Add .tiff folder..." (nested `CompressDialog.exec_()` batch mode)
  - [x] "Remove" button
  - [x] **Dedupe on add** by resolved `h5_path` (or by `(source_dir, file_list)` for tiff sources); show a status-bar toast on skip
  - [x] **Disambiguate display names** — if two entries have the same stem, auto-suffix with `(2)`
- [x] **Cellpose settings group**: model combo, diameter/flow_threshold/cellprob_threshold/min_size spinboxes, GPU checkbox, batch_size spinbox, segmentation channel index; edge-removal shown as a non-interactive label
- [x] **Thresholding rounds section** — **inline `QTableWidget`** (not a modal sub-dialog per `code-simplicity-reviewer` simplification). Columns: Name | Channel | Metric | Algorithm | Params | σ. Name column live-validates the regex and colors invalid cells red. Add/Remove/Up/Down buttons
- [x] **Column picker section**: `QListWidget` with checkboxes. `_recompute_available_columns()` computes from current intersected channels × `BUILTIN_METRICS` + `group_{round_name}` for each round + core columns + per-round `{ch}_{metric}_in_{round}` / `_out_{round}`. Re-runs whenever datasets or rounds change; preserves check state for columns that still exist
- [x] **Output parent** — `QLineEdit` + Browse + "write probe" test on focus-out. Default from `QSettings("LeeLabPerCell4", "PerCell4").value("single_cell_threshold_workflow/output_parent")` (flat 2-segment per pattern reviewer)
- [x] **Start button → `accept()` override** runs validation in order:
  1. `_build_channel_sources(entries)` — builds `list[ChannelSource]` mixing `h5_existing` (read via `store.metadata["channel_names"]`, wrapped in a `QProgressDialog` for feedback on slow drives) and `tiff_pending` (read from stored `CompressConfig.datasets[i].channel_names`)
  2. `intersect_channels(sources)` → (intersected, outliers)
  3. If outliers present → `QMessageBox.warning` with "Proceed without these N datasets" / "Abort and fix selection"; drops outliers on Proceed
  4. Empty intersection → `QMessageBox.warning` with "Abort" only
  5. Every round's `channel` is in intersected → else re-prompt
  6. Round `name` regex + uniqueness validated in `ThresholdingRound.__post_init__` when the config is built
  7. `output_parent` writable → `_write_probe()` test
  8. Prompt "Close current dataset and start workflow?" via `QMessageBox.question`; on No, close dialog with `reject()`

**Research insights: use `QMessageBox.warning` / status bar, never `.critical` / `.information`**

Grep of `src/percell4/gui/` shows 16 `QMessageBox.*` calls — all `.warning` or `.question`. Zero `.critical`, zero `.information`. Matching the house style. Final run summary goes in the status bar (see `launcher.py:880`) with a small "Open run folder" button in a non-modal popup.

**Files:**
- `src/percell4/gui/workflows/single_cell/config_dialog.py`

**Tests:**
- [x] `tests/gui_workflows/test_config_dialog.py` with `pytest-qt` — qtbot opens dialog, Add 2 h5 files, Add 1 round, click Start, assert `workflow_config` materializes a valid `WorkflowConfig`; test outlier prompt on mixed-channel h5 fixtures

**Success criteria:**
- [x] Opening dialog with empty state disables Start
- [x] Adding a folder of `.h5` files auto-discovers all `*.h5`
- [x] Outlier datasets trigger Proceed/Abort prompt; Proceed removes them
- [x] Column picker refreshes on dataset/round changes and preserves check marks for columns that still exist
- [x] Start writes `output_parent` to `QSettings` under `single_cell_threshold_workflow/output_parent`
- [x] Round name regex violations block Start with inline red highlight
- [x] Duplicate dataset additions show a status-bar toast and skip silently

#### Phase 4: Unattended phases — compress, segment, threshold compute, measure, export

All unattended phase drivers, implemented as pure helpers in `workflows/phases.py` plus thin Qt wrappers in `gui/workflows/single_cell/runner.py`.

**Tasks:**
- [x] `compress_one(entry: WorkflowDatasetEntry, run_folder: Path, host_progress) -> WorkflowDatasetEntry` — calls `import_dataset(..., files=compress_plan["files"])`, writes output to `entry.compress_plan["output_path"]` via `.tmp + os.replace`, returns a new entry with `source=H5_EXISTING` and `h5_path` populated
- [ ] **Post-Phase-0 re-validation**: after all tiff compresses, re-read `store.metadata["channel_names"]` for every freshly-minted h5 and confirm it matches the sealed `RunMetadata.intersected_channels`. If any dataset's channels drift, abort the run via a `QMessageBox.warning` listing the drifted datasets (the user can remove them from the selection and restart)
- [x] `segment_one(store, cfg, cellpose_model) -> NDArray[int32]` — reads one intensity channel via `store.read_channel`, calls `run_cellpose(image, model=cellpose_model, ...)`, runs `filter_edge_cells`, `filter_small_cells`, `relabel_sequential`, writes to `/labels/cellpose_qc` via `store.write_labels`. If `labels.max() == 0`, returns the empty array but the wrapper records a `DatasetFailure.SEGMENTATION_EMPTY` and asks the user what to do (Skip / Draw manually / Cancel)
- [x] **Cellpose model hoist**: `_run_phase_segment` builds one `CellposeModel(...)` **once** and passes it to every per-dataset `segment_one` call. Saves ~1–15 s per dataset of model-init cost
- [ ] Phase 1 (segment) is the one phase that uses `Worker(QThread)` per dataset. Wrap in a helper `run_in_worker(fn, *args)` that hides the worker lifecycle, connects `finished` / `error`, and yields to the runner's generator via `gen.send(result)`. **Cancellation is cooperative** — on cancel, calls `worker.request_abort()` and waits for `.finished` or `.error` to avoid half-written h5 state
- [x] `threshold_compute_one(store, round_spec) -> GroupingResult` — reads the round's channel via `read_channel`, reads `/labels/cellpose_qc`, calls `measure_cells` (single channel, one metric), then `group_cells_gmm` / `group_cells_kmeans`. The `GroupingResult` is held in an in-memory `dict[tuple[str, str], GroupingResult]` on the runner (keyed by dataset + round name) and consumed by the corresponding Phase 4/6 QC immediately. On empty groups (GMM failure), records `DatasetFailure.THRESHOLD_EMPTY` and skips that (dataset, round) pair
- [x] `measure_one(store, rounds, round_masks) -> pd.DataFrame` — opens **one** `store.open_read()` session per dataset, reads all channels, labels, and `/masks/<round>` for every round. Calls `measure_multichannel_with_masks(images, labels, metrics=BUILTIN_METRICS, masks={r.name: round_masks[r.name]})`. Merges `group_{round}` columns from `/groups/<round>` (renamed from whatever `ThresholdQCController` wrote). Prepends `dataset` column. **Streams to `run_folder/staging/<dataset.name>.parquet`** per-dataset (not held in memory). On error, records `DatasetFailure.MEASUREMENT_ERROR` and skips
- [x] `export_run(run_folder, config, metadata)` — scans `staging/*.parquet`, concatenates with `pyarrow.dataset.dataset(run_folder / "staging").to_table().to_pandas()`, adds a `pd.Categorical` conversion on the `dataset` column, downcasts float64 → float32 where lossless, writes `measurements.parquet` via `to_parquet(engine="pyarrow", compression="snappy", index=False, row_group_size=100_000, use_dictionary=True)`, then writes `combined.csv` (selected columns + identity) and `per_dataset/*.csv`. Deletes `staging/` on success
- [x] **CSV hygiene** — `to_csv(..., index=False, float_format="%.6g", na_rep="", encoding="utf-8", lineterminator="\n")`
- [x] **Progress reporting**: every unattended phase emits `workflow_event(kind="phase_progress", current=i, total=n, dataset_name=name, sub_progress="<step>")` at dataset boundaries. Sub-progress strings for Phase 7: "Base metrics...", "Round 1 metrics...", "Merging groups...". **Never** per-cell — `QProgressDialog.setValue()` calls `processEvents()` and would thrash if called per cell
- [x] **`setValue()` reentrancy discipline**: the runner NEVER mutates `CellDataModel` from inside a progress loop; all model updates happen at phase transition points

**Research insights: `measure_multichannel_with_masks` single-pass helper**

```python
# src/percell4/measure/measurer.py  (additive)
def measure_multichannel_with_masks(
    images: dict[str, NDArray],
    labels: NDArray[np.int32],
    metrics: list[str] | None = None,
    masks: dict[str, NDArray] | None = None,
) -> pd.DataFrame:
    """One pass per cell; reuses `_iter_cell_crops`.

    Columns:
      {ch}_{metric}                 — whole cell
      {ch}_{metric}_in_{round}      — inside round mask
      {ch}_{metric}_out_{round}     — outside round mask
    """
    # ...single iteration over _iter_cell_crops, per-channel per-metric, per-mask in/out
```

Estimated saving: ~5× on Phase 7 for a 6-channel × 3-round run. Also eliminates the post-hoc rename step that the initial plan called out as a risk row.

**Research insights: HDF5 session reads + channel-plane slicing**

All per-dataset unattended work wraps `store.open_read()` once per dataset so the 64 MB chunk cache survives across reads within the same phase. Phases 1, 3/5, 7 all touch multiple datasets in `/intensity` and one or two labels/masks each — session mode eliminates per-call file-open overhead (50–200 ms on network drives, zero on local SSD).

**Files:**
- `src/percell4/workflows/phases.py` — pure helpers (Qt-free)
- `src/percell4/gui/workflows/single_cell/runner.py` — `SingleCellThresholdingRunner` with `_run_phase_*` coroutine methods

**Tests:**
- [x] `test_phases.py` — each pure helper with a `DatasetStore` tmp-path fixture
- [x] `test_measure_one_with_masks.py` — verify single-pass output matches sequential calls numerically
- [x] `test_export_run.py` — synthetic `staging/` dir + config → verify `measurements.parquet` + `combined.csv` + `per_dataset/*.csv` match expected shape

**Success criteria:**
- [x] Phase 0 compresses a fixture tiff folder via `import_dataset`, emits dataset-level `phase_progress`, and produces `.h5` files at expected paths
- [ ] Phase 0 post-validation catches channel drift and aborts the run with a warning
- [x] Phase 1 produces `/labels/cellpose_qc` in each dataset's h5 with edge cells removed; detects `labels.max() == 0` and records a failure
- [x] Cellpose model is constructed once per phase, not per dataset (verify via mock or `id()`)
- [x] Phase 3/5 computes `GroupingResult` per (dataset, round) and holds it in runner memory for the matching Phase 4/6 QC
- [x] Phase 7 produces one `staging/<dataset>.parquet` per dataset without holding every DataFrame in memory
- [x] Phase 8 reads from `staging/`, writes `measurements.parquet` + CSVs, deletes `staging/` on success
- [x] No `/measurements` group is written to any input `.h5`
- [x] `QProgressDialog.setValue()` is never called per-cell — only per-dataset

#### Phase 5: Segmentation QC controller (not dialog) — slim, queue-aware

**Tasks:**
- [x] Create `src/percell4/gui/workflows/single_cell/seg_qc.py` with `SegmentationQCController(QObject)` that **follows the `ThresholdQCController` shape** (`gui/threshold_qc.py:71`) — builds its own `QMainWindow` with a narrow tool dock, not a `QDialog`
- [x] Constructor takes `(viewer_win, data_model, store, entry, queue_index, queue_total, on_complete)` where `on_complete(result: Literal["next", "cancel", "skip"])` is a plain Python callback — matching `ThresholdQCController`'s contract
- [x] `start()` loads the dataset: opens `store.open_read()` once, reads `intensity` + `/labels/cellpose_qc`. **Reuses single image + labels layers across dataset advances** via `layer.data = ...` + `layer.refresh()`, dropping Python references on advance and calling `gc.collect()` between datasets
- [x] Hides non-QC layers on entry; restores on exit (pattern from `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md`)
- [x] Nav bar: `[✕ Cancel run] [Accept & Next →] (DS i of N: <name>)`. Counter reads from `config.datasets` (post-outlier-filter)
- [x] **Keyboard shortcuts**: `Ctrl+Enter` → Accept & Next (focus on Accept by default), `Esc` → Cancel run (with confirmation)
- [x] Edit tools (lifted from `SegmentationPanel`, implementations copied):
  - Delete selected label (`segmentation_panel.py:358-373`)
  - Draw new label (`segmentation_panel.py:375-407`, including the 100-ms napari mode-switch delay)
  - Edge-margin preview + Apply (`segmentation_panel.py:443-525`)
- [x] **Signal coalescing**: label edit handlers gated by `QTimer.singleShot(0, self._schedule_refresh)` per `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md` to avoid redundant layer refreshes on rapid edit events
- [x] `_on_accept_next` persists current labels to `store.write_labels("cellpose_qc", labels)` (overwrites the pre-QC Cellpose output) and advances the queue
- [x] `_on_cancel` fires `on_complete("cancel")` after a `QMessageBox.question("Cancel the running workflow? Labels and masks already written to disk will remain.")`
- [x] `closeEvent` on the QC window: trapped and treated as Cancel (with the same confirmation)
- [x] `skip` action — offered only when `segment_one` returned empty labels: records `FailureRecord(SEGMENTATION_EMPTY)` and advances

**Files:**
- `src/percell4/gui/workflows/single_cell/seg_qc.py`

**Tests:**
- [x] `test_seg_qc_smoke.py` with pytest-qt — instantiate controller, fake label edits, assert Accept writes to `/labels/cellpose_qc` and advances; assert Cancel fires `on_complete("cancel")`

**Success criteria:**
- [x] Accept & Next persists edits to `/labels/cellpose_qc` and advances the queue
- [x] Cancel (button, keyboard, or window-X) prompts for confirmation and unwinds the runner cleanly through its `finally` block
- [x] Advancing datasets does not leak napari layers — RSS remains bounded across a 10-dataset QC queue
- [x] Keyboard shortcuts work for Accept/Cancel

#### Phase 6: Threshold QC queue — wrapper around `ThresholdQCController`

**Tasks:**
- [x] Create `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py` with `ThresholdQCQueueBar(QWidget)` — a small floating widget with `[✕ Cancel run]`. Accept & Next is implicit: the controller's `on_complete(success=True)` fires the advance via the runner's generator dispatch
- [x] `_run_phase_threshold_qc(round_spec, entries)` on the runner:
  1. For each dataset: open `store`, read `intensity` and `/labels/cellpose_qc`
  2. Look up the `GroupingResult` from runner memory (populated in the preceding Phase 3/5 compute)
  3. Instantiate `ThresholdQCController(... write_measurements_to_store=False, store=<dataset store>, mask_name=round.name)` with an `on_complete` callback that dispatches into the runner's generator
  4. Show `ThresholdQCQueueBar` docked next to the controller's own windows
  5. Call `controller.start()`
  6. Wait for the callback (via the generator protocol — no nested event loop)
  7. On `success=True`: mask + `/groups/<round>` have already been written by the controller's `_finalize`. Advance to the next dataset
  8. On `success=False`: record `FailureRecord(THRESHOLD_ERROR)` with the controller's message; prompt the user "Skip this dataset for this round" / "Cancel run"
  9. On Cancel (queue bar or window-X): call `_cleanup_all()` on the controller and unwind the runner through its `finally` block
- [x] **Verify `ThresholdQCController` does not use `events.colormap.blocker()`** on its `DirectLabelColormap` assignments. If it does, remove the blocker per `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`. This is a precondition for the workflow, not a new feature
- [x] Runner alternates `_run_phase_threshold_compute(round)` and `_run_phase_threshold_qc(round)` for every configured round

**Research insights: controller is already workflow-friendly**

`ThresholdQCController._finalize` (`threshold_qc.py:687-733`) already writes `/masks/<name>` and `/groups/<name>` and, after the Phase 1 flag, skips `/measurements`. It already calls `_cleanup_all()` on its own temp layers. The only thing the workflow adds is per-dataset navigation — the controller's internal per-group Back button stays untouched.

**Files:**
- `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py`

**Tests:**
- [x] `test_threshold_qc_queue.py` with pytest-qt — stub controller fires `on_complete(True)`, queue advances; stub fires `on_complete(False)`, failure recorded and prompt shown

**Success criteria:**
- [x] `/masks/<round.name>` and `/groups/<round.name>` appear in each dataset's h5 after accept
- [x] No `/measurements` appears in any dataset's h5 at any point during threshold QC
- [x] Cancel during threshold QC cleans up the controller's temp layers before unwinding the runner
- [x] Verify `events.colormap.blocker()` is NOT used in the existing `ThresholdQCController` (grep check)

#### Phase 7: Measure phase — in detail (mostly in Phase 4 but calling out here)

Phase 7 is `_run_phase_measure` from Phase 4. Key acceptance: one `store.open_read()` session per dataset, one `measure_multichannel_with_masks` call per dataset, streaming to `staging/`.

#### Phase 8: Workflows tab wiring + end-to-end acceptance

**Tasks:**
- [x] Edit `LauncherWindow._create_workflows_panel` at `src/percell4/gui/launcher.py:534-544` to remove the dead placeholders and add a single entry: `[Single-cell thresholding analysis workflow]` → opens `WorkflowConfigDialog`
- [x] On Start click: prompt "Close current dataset and start workflow?"; on confirm call `CellDataModel.clear()` and `host.close_child_windows()`
- [ ] Wire runner `workflow_event` signal to the launcher's status bar and an appropriate `QProgressDialog` per phase
- [x] Trap `LauncherWindow.closeEvent` while `is_workflow_locked`: prompt "Cancel the running workflow and quit?"; on confirm call `runner.request_cancel()` then accept the close
- [x] Ensure only one runner can be active at a time (reentrance guard on Start)
- [x] Archive the brainstorm per project rules: move `docs/brainstorms/2026-04-10-single-cell-thresholding-workflow-brainstorm.md` to `docs/archive/` after successful implementation

**Files:**
- `src/percell4/gui/launcher.py`
- `src/percell4/gui/CLAUDE.md` (add the new workflows subpackage + dialogs + controllers to the index)

**Success criteria — end-to-end:**
- [ ] Configure 3 test datasets (2 h5 + 1 tiff) → 2 rounds → Start → run completes → run folder contains `measurements.parquet`, `combined.csv`, `per_dataset/*.csv`, `run_config.json` with `finished_at` set, and `run_log.jsonl`
- [ ] `measurements.parquet` loads via `pd.read_parquet` with correct shape and dtypes
- [x] No `/measurements` group appears in any input `.h5`
- [x] `/labels/cellpose_qc`, `/masks/<round>`, `/groups/<round>` exist in each dataset's h5
- [x] Cancel during Phase 2 unwinds cleanly: launcher is unlocked, child windows are restored, `run_config.json.finished_at` is stamped with a cancel note
- [x] Raising an exception inside any phase (simulate via a monkey-patched stub) leaves the launcher unlocked and child windows restored
- [x] Outlier datasets prompt Proceed/Abort on Start
- [x] Round name with a space is rejected at dialog validation time
- [x] Closing the app while workflow-locked prompts to cancel first

## Failure Model

Per-dataset failures are first-class; the workflow never crashes because one dataset misbehaves.

| Failure | Detected in | Handling |
|---|---|---|
| `COMPRESS_FAILED` | Phase 0 | Runner stops phase, shows `QMessageBox.warning` listing failed datasets with Retry / Drop + Continue / Cancel run |
| `SEGMENTATION_EMPTY` | Phase 1 (`labels.max() == 0`) | Runner prompts Skip dataset / Draw manually in seg QC / Cancel run |
| `SEGMENTATION_ERROR` | Phase 1 (exception from `run_cellpose`) | Dataset marked failed; excluded from all subsequent phases; noted in `run_config.json` `failures` list |
| `THRESHOLD_EMPTY` | Phase 3/5 (GMM returns 0 groups) | `(dataset, round)` pair marked failed; skipped in the corresponding Phase 4/6 QC |
| `THRESHOLD_ERROR` | Phase 4/6 (`controller.on_complete(success=False)`) | Prompt Skip dataset for this round / Cancel run |
| `MEASUREMENT_ERROR` | Phase 7 (exception from `measure_multichannel_with_masks`) | Dataset skipped in Phase 7; excluded from `combined.csv` and `per_dataset/*.csv` |

All failures are recorded in `RunMetadata.failures: list[FailureRecord]` and persisted to `run_config.json` on finish/cancel. Failed datasets are silently skipped by later phases; the run proceeds with the remaining datasets.

## Performance Optimizations

| ID | Area | Impact | Concrete action |
|---|---|---|---|
| P-OPT-1 | HDF5 I/O | High | Wrap every per-dataset compute phase in a single `store.open_read()` session; add `DatasetStore.read_channel(path, idx)` for channel-plane-only reads |
| P-OPT-2 | Memory | High | Stream Phase 7 per-dataset to `staging/<name>.parquet` instead of accumulating DataFrames in runner memory |
| P-OPT-3 | Measurement | High | `measure_multichannel_with_masks` — one pass per dataset, ~5× speedup for Phase 7 |
| P-OPT-4 | Cellpose init | Medium | Hoist `CellposeModel(...)` above the per-dataset loop in Phase 1 |
| P-OPT-5 | Parquet write | Medium | Categorical `dataset` column; float64→float32 downcast where lossless; `row_group_size=100_000`; explicit `compression="snappy"`, `use_dictionary=True` |
| P-OPT-6 | Concat | Medium | `pyarrow.dataset.dataset("staging/").to_table().to_pandas()` for the final concat — bounded memory, no `pd.concat` alignment path |
| P-OPT-7 | Viewer layers | Medium | SegQCController reuses one image + labels layer across datasets via `.data = ... + .refresh()`; drops refs + `gc.collect()` on advance |
| P-OPT-8 | Channel validation | Medium | Run `intersect_channels` inside a `QProgressDialog(Qt.WindowModal)` for feedback on 50-dataset network-drive selections |
| P-OPT-9 | Cellpose batching (follow-up) | Low–Medium (GPU only) | `run_cellpose_batch(images: list, batch_size=N)` for true cross-image batching on GPU. Not in initial scope; add to backlog |
| P-OPT-10 | Progress throttling | Medium | Never call `setValue()` per cell; dataset granularity only. Sub-progress strings flow via status bar, not via `QProgressDialog` |

## Alternative Approaches Considered

1. **Dataset-at-a-time execution** — rejected in the brainstorm; user wants step-at-a-time.
2. **Pipelined phases** (next unattended phase runs in background during current QC) — rejected; complexity not worth wall-clock savings.
3. **Store measurements inside each `.h5`** — rejected; h5 stays pure image + metadata.
4. **Reuse `SegmentationPanel` directly** for seg QC — rejected for slim dedicated controller.
5. **Extend `measure_multichannel`** to accept `masks: dict` in place — rejected in favor of additive `measure_multichannel_with_masks` helper (preserves existing callers).
6. **Refactor `ThresholdQCController` to remove `/measurements` write entirely** — **deferred, not rejected**. Queued as tech debt (see **§Tech Debt Recorded**). Ship with additive `write_measurements_to_store` flag + optional 3-arg `on_complete(success, msg, measurements_df)` callback now; migrate `GroupedSegPanel` later.
7. **Background `QThread` for compress / threshold-compute / measure phases** — rejected per `docs/solutions/logic-errors/batch-compress-development-lessons.md` (HDF5 bus errors on external drives).
8. **Nested `QEventLoop.exec_()` for interactive phases** — rejected for generator-driven `gen.send()` approach (avoids Qt reentrancy footgun; also testable without a `QApplication`).
9. **Pydantic v2 for config models** — rejected for stdlib `@dataclass` + explicit `to_dict/from_dict` helpers. The project has a "minimum deps" preference and the hand-rolled helpers are ~40 lines total with tests. Revisit if configuration validation becomes a recurring pain.
10. **`async`/`await` runner via `qasync`** — rejected. Heavyweight, inconsistent with the rest of the sync Qt codebase.
11. **`QDialog` for seg QC** — rejected for Controller + `QMainWindow` per the existing `ThresholdQCController` precedent.
12. **Six separate signals on `WorkflowRunner`** — rejected for one `workflow_event = Signal(object)` + `WorkflowEvent` descriptor. Matches `CellDataModel.state_changed` + `StateChange`.
13. **Pause / Resume subsystem** — **cut from v1** per `code-simplicity-reviewer` and user sign-off. Would have added `run_state.json`, a second Workflows-tab entry, auto-checkpointing, and on-disk reconciliation — ~250–400 LOC and most of the state-management risk rows. V1 has Cancel only. Revisit once the workflow is in real use and pause points are data-driven.
14. **Back button in QC queues** — **cut from v1** per `code-simplicity-reviewer` and user sign-off. Would have required pre-QC label/mask snapshots (on disk or in memory) and cross-dataset rollback mechanics. V1 has Accept & Next + Cancel only. Users who regret a prior dataset's QC must cancel and re-run.

## Acceptance Criteria

### Functional requirements

- [ ] Workflows tab contains exactly one entry: Start
- [ ] Config dialog accepts individual `.h5`, a folder of `.h5`, a single tiff source, and a folder of tiff sources; dedupes on add
- [ ] Channel intersection validation on Start handles mixed `h5_existing` and `tiff_pending` datasets correctly (reads channel names from `CompressConfig` for pending tiffs)
- [ ] Channel intersection re-validation after Phase 0 catches drift and aborts the run with a warning
- [ ] Cellpose settings are global; edge-cell removal always on; model constructed once per phase
- [ ] Thresholding rounds are an ordered list, inline-edited in a table, with live regex validation on name
- [ ] Each round's QC uses `ThresholdQCController` with `write_measurements_to_store=False`
- [ ] Segmentation QC is a slim `SegmentationQCController` providing delete / draw / edge-cleanup with keyboard shortcuts
- [ ] QC navigation: Accept & Next + Cancel run only (no Back, no Pause)
- [ ] Measurements capture every `BUILTIN_METRIC` on every intersected channel, plus `{ch}_{metric}_in_{round}` / `_out_{round}`, plus `group_{round}`, plus `dataset`
- [ ] Final output: `measurements.parquet` (full), `combined.csv` (selected columns + identity), `per_dataset/*.csv`, `run_config.json`, `run_log.jsonl`
- [ ] No workflow write touches `/measurements` in any input `.h5`
- [ ] `/labels/cellpose_qc`, `/masks/<round_name>`, `/groups/<round_name>` are written to each `.h5`
- [ ] Starting the workflow clears `CellDataModel`, closes child windows (cell_table, data_plot, phasor), and locks the main UI; restored on finish/cancel/error
- [ ] Closing the app while workflow-locked prompts to Cancel first
- [ ] Reentrance guard: clicking Start while a run is active is a no-op
- [ ] Per-dataset failures are first-class, recorded in `run_config.json`, and do not crash the run
- [ ] Exception in any phase unwinds through a single `finally` block that unlocks the launcher and restores child windows

### Non-functional requirements

- [ ] No regression in `GroupedSegPanel` — `ThresholdQCController` default behavior unchanged
- [ ] No regression in `SegmentationPanel` — handlers are copied into the slim controller, not modified
- [ ] `measurements.parquet` read time for a 10-dataset × 5000-cells × 30-column run < 1 second
- [ ] `src/percell4/workflows/` has zero Qt imports; enforced by a test that imports the subpackage without a `QApplication`
- [ ] `src/percell4/gui/workflows/` holds all Qt code; `runner.py` is the generator driver
- [ ] `pyproject.toml` pins `pyarrow>=14` and `pytest-qt>=4.4`
- [ ] Per-module `CLAUDE.md` files are updated for every new subpackage
- [ ] Runner is unit-testable without a running `QApplication` via the generator protocol + `FakeHost`
- [ ] Peak RSS during a 10-dataset × 3-round run does not exceed current single-dataset peak by more than 500 MB (per-dataset measurement DFs stream to `staging/` instead of accumulating in memory)

## Dependencies & Prerequisites

- `pyarrow>=14` (new) — Parquet engine
- `pytest-qt>=4.4` (new, dev) — Qt unit tests
- Existing: cellpose, h5py, napari, qtpy, PyQt5, pandas, numpy, scikit-image, scipy
- Brainstorm: `docs/brainstorms/2026-04-10-single-cell-thresholding-workflow-brainstorm.md`

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| HDF5 bus errors under QThread on external drives | Phase 0 / 3 / 5 / 7 crashes | Main-thread `QProgressDialog` pattern for all unattended phases; Cellpose (Phase 1) is the only `Worker` usage |
| `ThresholdQCController`'s `/measurements` write leaks into h5 | Violates purity invariant | Phase 1 additive flag with default `True`; workflow passes `False`; end-to-end test asserts no `/measurements` group exists |
| Per-round mask column-name collisions | Data loss | Eliminated by `measure_multichannel_with_masks` — columns are built with `_in_{round}` / `_out_{round}` suffixes in a single pass |
| Crash mid-phase corrupts a dataset's h5 | Partial state; re-run needed | Every artifact write uses `.tmp + os.replace`; per-dataset h5 writes are atomic at the store layer; user re-runs the workflow (labels/masks already in the h5 are overwritten) |
| User picks wrong folder | Run wastes hours | Channel intersection validation with Proceed/Abort prompt on Start |
| Tiff-pending datasets can't be validated at Start | False error or silent bypass | `intersect_channels` accepts `list[ChannelSource]` including pending tiffs; re-validation after Phase 0 |
| Nested `QEventLoop.exec_()` reentrancy | Subtle crashes | Runner uses generator + `gen.send()` from slots; no nested event loops anywhere |
| Round name collides with h5 path or CSV column | Silent corruption | Strict regex `^[A-Za-z_][A-Za-z0-9_\-]{0,39}$` in `ThresholdingRound.__post_init__` |
| Child windows thrash on dataset swap | Laggy / wrong UI | Child windows closed on Start and restored on finish/cancel via `WorkflowHost` protocol |
| Cellpose returns 0 cells | Downstream crashes | `DatasetFailure.SEGMENTATION_EMPTY` path with Skip / Draw / Cancel prompt |
| Disk fills during Phase 7 export | Partial output | Wrapped writes with `OSError` catch; prompt Retry / Pick different folder / Cancel |
| Output parent not writable | Crash on Start | `_write_probe()` pre-check before `accept()` |
| Uncaught exception leaves launcher locked | User stuck | Single top-level `try/except/finally` in the runner; `finally` always unlocks and restores child windows |
| User cancels mid-dataset in an unattended phase | Half-written h5 | Cooperative cancel checked at dataset boundaries only; in-flight dataset always finishes before unwind |

## Gotchas from Institutional Learnings

1. **Main-thread + `QProgressDialog` for HDF5 loops, not `QThread`.** `docs/solutions/logic-errors/batch-compress-development-lessons.md`. Cellpose is the only exception.
2. **Capture `dialog.compress_config` immediately after `exec_()` returns, before `deleteLater()`.** `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md`. Applies to Add .tiff source / Add .tiff folder.
3. **`labels_layer.refresh()` after every `.data =` assignment.** `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`. Required in the seg QC controller's delete/draw/cleanup handlers.
4. **Use `ViewerWindow.add_mask`, not `add_labels`**, for thresholded binary masks. `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md`. `ThresholdQCController` already does this correctly.
5. **`numpy.isin` fails with Python sets.** `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md`. Convert to `list` / `np.array`.
6. **`DirectLabelColormap` needs a full dict plus a `None` entry for background; never wrap in `events.colormap.blocker()`.** `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md`. **Phase 6 task: verify existing `ThresholdQCController` does not use the blocker.**
7. **Signal coalescing with `QTimer.singleShot(0, ...)`.** `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md`. Apply in the seg QC controller's label-edit event handlers.
8. **`os.replace()` atomic writes, never preceded by `os.unlink()`.** `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md`. Used throughout `workflows/artifacts.py`.
9. **Layer visibility save/restore during QC** — hide non-QC layers on entry, restore on exit. `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md`.
10. **Use Worker signals for cross-thread communication, not callbacks.** `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md`. The Cellpose worker in Phase 1 emits `finished`/`error` signals; the runner's slot consumes them.

## Tech Debt Recorded

- **`ThresholdQCController.write_measurements_to_store` flag** is a compatibility shim. Remove it when `GroupedSegPanel` is next refactored by migrating the callback to a 3-arg `on_complete(success, msg, measurements_df)` form that returns measurements to the caller. Record in `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` (to be created during Phase 1).
- **`SegmentationPanel` handler copy-paste**: the slim seg QC controller copies delete/draw/edge-cleanup handlers from `SegmentationPanel`. When either file is next touched, extract the handlers into pure functions in `src/percell4/segment/label_ops.py` and call from both sites.
- **Cellpose true cross-image batching** on GPU: add `run_cellpose_batch(images: list, batch_size=N)` as a follow-up for GPU users (`docs/solutions/tech-debt/cellpose-batch-inference.md`).
- **`BaseWorkflowRunner` / `BaseWorkflowConfigDialog`** are introduced in this plan but only one concrete subclass exists today. When the second workflow lands, audit and extract the config-dialog base-class shared sections.

## Simplicity Feedback (user decisions after deepening)

`code-simplicity-reviewer` flagged three features as over-engineered for v1. After review the user cut two and kept one:

1. **Pause / Resume subsystem** — **CUT**. v1 has Cancel only. Labels/masks already written to h5 persist across runs; re-running the workflow overwrites them.
2. **Back button in QC queues** — **CUT**. v1 nav bars are Accept & Next + Cancel only. Users who regret a prior dataset's QC must cancel and re-run.
3. **CSV column picker** — **KEPT**. Config-time column selection remains; user explicitly chose it for clean downstream CSVs.

Net effect: ~350-600 LOC cut from the initial deepened plan, and all state-management complexity around checkpoint reconciliation, pre-QC label snapshots, and cross-dataset rollback is eliminated.

## File Summary

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | modify | Add `pyarrow>=14`, `pytest-qt>=4.4` |
| `src/percell4/workflows/__init__.py` | new | Package marker |
| `src/percell4/workflows/models.py` | new | Config dataclasses with `__post_init__` validation, StrEnums, frozen where possible |
| `src/percell4/workflows/failures.py` | new | `DatasetFailure`, `FailureRecord` |
| `src/percell4/workflows/artifacts.py` | new | `write_atomic`, `create_run_folder`, `config_to_dict`/`from_dict`, `write_run_config`/`read_run_config` |
| `src/percell4/workflows/channels.py` | new | `intersect_channels(list[ChannelSource])` |
| `src/percell4/workflows/phases.py` | new | Pure phase helpers (`compress_one`, `segment_one`, `threshold_compute_one`, `measure_one`, `export_run`) — zero Qt |
| `src/percell4/workflows/run_log.py` | new | `RunLog(folder)` jsonl audit helper |
| `src/percell4/workflows/host.py` | new | `WorkflowHost` Protocol |
| `src/percell4/workflows/CLAUDE.md` | new | House-style subpackage doc |
| `src/percell4/store.py` | modify | Additive `read_channel(path, idx)` |
| `src/percell4/segment/cellpose.py` | modify | Additive `model=None` kwarg |
| `src/percell4/measure/measurer.py` | modify | Additive `measure_multichannel_with_masks(images, labels, metrics, masks)` |
| `src/percell4/gui/threshold_qc.py` | modify | Additive `write_measurements_to_store: bool = True` parameter |
| `src/percell4/gui/workflows/__init__.py` | new | Package marker |
| `src/percell4/gui/workflows/base_runner.py` | new | `BaseWorkflowRunner(QObject)` generator-driven loop, `WorkflowEvent` descriptor, single `workflow_event` signal |
| `src/percell4/gui/workflows/single_cell/runner.py` | new | `SingleCellThresholdingRunner(BaseWorkflowRunner)` concrete phase methods |
| `src/percell4/gui/workflows/single_cell/config_dialog.py` | new | `WorkflowConfigDialog(QDialog)` — dataset picker, cellpose group, round table, column picker, output parent |
| `src/percell4/gui/workflows/single_cell/round_editor.py` | new | Inline `QTableWidget`-based round editor |
| `src/percell4/gui/workflows/single_cell/seg_qc.py` | new | `SegmentationQCController(QObject)` with `QMainWindow` nav |
| `src/percell4/gui/workflows/single_cell/threshold_qc_queue.py` | new | `ThresholdQCQueueBar` per-dataset wrapper |
| `src/percell4/gui/workflows/CLAUDE.md` | new | House-style subpackage doc |
| `src/percell4/gui/launcher.py` | modify | Replace dead Workflows panel; add `set_workflow_locked`, `close_child_windows`, `restore_child_windows`; trap `closeEvent` while locked; wire runner signals |
| `src/percell4/gui/CLAUDE.md` | modify | Add new subpackage + dialogs to index |
| `src/percell4/CLAUDE.md` | modify | Add `workflows/` to subpackage index |
| `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` | new | Tech-debt marker for the additive flag |
| `tests/workflows/test_*.py` | new | Pure-Python tests (no Qt) |
| `tests/gui_workflows/test_*.py` | new | pytest-qt tests |
| `run_<ts>/run_config.json` | runtime | Per run; schema-versioned; stamped with `finished_at` on finish/cancel/error |
| `run_<ts>/run_log.jsonl` | runtime | Audit trail |
| `run_<ts>/measurements.parquet` | runtime | Cross-dataset DataFrame |
| `run_<ts>/combined.csv` | runtime | User-selected columns |
| `run_<ts>/per_dataset/<name>.csv` | runtime | Per-dataset CSVs |
| `run_<ts>/staging/<dataset>.parquet` | runtime | Per-dataset Phase 7 output, deleted on successful export |

## Expected Run Folder Layout

```
<output_parent>/
  run_2026-04-10_143022_ab12cd34/
    run_config.json                         # recipe + RunMetadata including finished_at + failures
    run_log.jsonl                           # audit trail
    measurements.parquet                    # final cross-dataset DataFrame
    combined.csv                            # user-selected columns
    per_dataset/
      DS1.csv
      DS2.csv
      DS3.csv
    staging/                                # transient, deleted after successful export
      DS1.parquet
      ...
```

## Expected h5 Layout (per dataset, after a run)

```
DS1.h5
  /intensity                    # unchanged
  /metadata                     # unchanged (channel_names, dims, ...)
  /decays/*                     # unchanged (FLIM path)
  /labels/cellpose_qc           # post-QC segmentation from Phase 2
  /masks/<round1_name>          # from Phase 4 threshold QC accept
  /masks/<round2_name>          # from Phase 6 threshold QC accept
  /groups/<round1_name>         # group assignments DF
  /groups/<round2_name>
  # NOT written: /measurements
```

## References

### Internal references

- Brainstorm: `docs/brainstorms/2026-04-10-single-cell-thresholding-workflow-brainstorm.md`
- Workflows tab: `src/percell4/gui/launcher.py:157-166` (category registration), `:534-544` (`_create_workflows_panel`)
- Canonical unattended batch pattern: `src/percell4/gui/launcher.py:801-886` (`_run_batch_compress`)
- Compress dialog + import: `src/percell4/gui/compress_dialog.py:305-374`, `src/percell4/io/importer.py:25-38`, `src/percell4/io/models.py:95-181`
- Cellpose integration: `src/percell4/segment/cellpose.py:25-91`, `src/percell4/segment/postprocess.py`
- Segmentation panel handlers to copy: `src/percell4/gui/segmentation_panel.py:358-525`
- Threshold QC controller: `src/percell4/gui/threshold_qc.py:77-102` (ctor), `:687-733` (finalize)
- Grouped threshold panel blueprint: `src/percell4/gui/grouped_seg_panel.py:182-390`
- Measurement pipeline: `src/percell4/measure/measurer.py:129,282`, `metrics.py:113-123`, `grouper.py`
- Store API: `src/percell4/store.py:64-308`
- CellDataModel + StateChange: `src/percell4/model.py:22-155`
- Viewer window: `src/percell4/gui/viewer.py:116-237`
- Worker pattern: `src/percell4/gui/workers.py:16-62`
- QSettings keys: `src/percell4/gui/launcher.py:2448`, `viewer.py:420`, `launcher.py:1525-1536`

### Institutional learnings applied

- `docs/solutions/logic-errors/batch-compress-development-lessons.md` — main-thread + `QProgressDialog` pattern
- `docs/solutions/logic-errors/grouped-thresholding-development-lessons.md` — layer visibility save/restore during QC
- `docs/solutions/ui-bugs/napari-mask-layer-misclassified-as-segmentation.md` — `add_mask` vs `add_labels`
- `docs/solutions/ui-bugs/napari-direct-label-colormap-rendering-blocked-by-events.md` — no colormap blocker
- `docs/solutions/ui-bugs/percell4-flim-phasor-troubleshooting.md` — dialog value capture timing
- `docs/solutions/ui-bugs/percell4-phases-0-6-napari-qt-learnings.md` — signal coalescing via `QTimer.singleShot(0)`
- `docs/solutions/logic-errors/numpy-isin-fails-with-python-sets.md` — no `np.isin` with sets
- `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md` — worker signals not callbacks
- `docs/solutions/build-errors/cross-platform-packaging-review-fixes.md` — `os.replace` without preceding unlink

### External documentation

- [pandas.DataFrame.to_parquet](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_parquet.html)
- [pyarrow parquet — writing](https://arrow.apache.org/docs/python/parquet.html)
- [pyarrow.dataset.write_dataset](https://arrow.apache.org/docs/python/generated/pyarrow.dataset.write_dataset.html)
- [napari Labels layer API](https://napari.org/dev/api/napari.layers.Labels.html)
- [Cellpose settings.rst (list-of-images eval)](https://github.com/mouseland/cellpose/blob/main/docs/settings.rst)
- [h5py File Objects](https://docs.h5py.org/en/latest/high/file.html)
- [Qt 5 QProgressDialog](https://doc.qt.io/qt-5/qprogressdialog.html)

### Precedent plans (house style)

- `docs/plans/2026-04-04-feat-batch-compress-tiff-datasets-plan.md` — batch-compress pattern, phase structure
- `docs/plans/2026-04-03-feat-grouped-segmentation-plan.md` — threshold QC architecture, finalize flow
