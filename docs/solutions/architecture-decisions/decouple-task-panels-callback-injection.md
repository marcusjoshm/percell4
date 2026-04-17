---
title: "Decouple task panels from launcher via callback injection"
category: architecture-decisions
tags: [dependency-injection, testability, god-object, coupling, task-panels, hexagonal-architecture, qt]
module: interfaces/gui/task_panels
symptom: "Task panels held a launcher reference and called back via private methods (self._launcher._on_load_dataset(), self._launcher._windows.get(), self._launcher.statusBar().showMessage()), making them untestable without a full launcher instance"
root_cause: "Panels were extracted from a 2793-line launcher as cosmetic splits — lines moved but responsibility did not. Each panel kept a direct reference to the launcher and tunneled through its private API"
severity: p1
date: 2026-04-17
---

# Decouple Task Panels from Launcher via Callback Injection

## Problem

Task panels (IoPanel, AnalysisPanel, FlimPanel, DataPanel) were extracted from a 2793-line launcher god object but remained hollow delegates. Each received `launcher=self` at construction and called back into the launcher via private methods:

```python
# BEFORE: panel reaches into launcher internals
class IoPanel(QWidget):
    def __init__(self, data_model, launcher=None, parent=None):
        self._launcher = launcher

    def _on_load_dataset(self):
        if self._launcher is not None:
            self._launcher._on_load_dataset()  # pure passthrough

    def _show_status(self, msg):
        if self._launcher is not None:
            self._launcher.statusBar().showMessage(msg)  # knows launcher is QMainWindow
```

This created: (1) untestable panels (need full launcher + napari + all windows), (2) invisible coupling via `getattr(self._launcher, "_current_store")`, (3) bidirectional dependency web.

## Root Cause

The extraction moved lines out of the launcher without moving responsibility. The `launcher=self` pattern is a lateral move, not a decoupling — the panel still depends on the full launcher interface. This is the same anti-pattern documented in `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` where a boolean flag (`write_measurements_to_store`) was used to toggle parent-owned behavior inside a child component.

## Solution: Three Tiers of Callback Injection

Replace the opaque launcher reference with explicit `Callable` parameters in the panel's `__init__`.

### Tier 1: Pure action callbacks (IoPanel)

The simplest case. Panel is a bag of buttons; every button delegates to an injected `Callable[[], None]`.

```python
# AFTER: IoPanel — zero launcher knowledge
class IoPanel(QWidget):
    def __init__(
        self,
        *,
        on_import: Callable[[], None],
        on_load: Callable[[], None],
        on_add_layer: Callable[[], None],
        on_close: Callable[[], None],
        on_export_csv: Callable[[], None],
        on_export_images: Callable[[], None],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        ...
```

### Tier 2: Accessor callbacks for lazy resolution (DataPanel)

Panel needs store, viewer window, and h5 path, which change over the application lifetime (datasets load/close). Inject `Callable[[], T | None]` accessors that resolve at call time.

```python
class DataPanel(QWidget):
    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_store: Callable[[], Any | None],
        get_viewer_window: Callable[[], Any | None],
        get_h5_path: Callable[[], str | None],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        ...
```

### Tier 3: Mixed callbacks and accessors (FlimPanel)

Complex panel combining accessor callbacks (repo, viewer, phasor window, seg labels) with action callbacks (show_window) and the shared data model.

```python
class FlimPanel(QWidget):
    def __init__(
        self,
        data_model: CellDataModel,
        *,
        get_repo: Callable[[], Any],
        get_viewer_window: Callable[[], Any | None],
        get_phasor_window: Callable[[], Any | None],
        get_active_seg_labels: Callable[[], Any | None],
        show_window: Callable[[str], None],
        show_status: Callable[[str], None] = lambda _: None,
        parent: QWidget | None = None,
    ) -> None:
        ...
```

### Wiring in the Launcher

The launcher wires callbacks via lambdas in `_create_*_panel` methods. This is the **only place** where launcher private attributes are referenced:

```python
# main_window.py — lambda wiring
def _create_io_panel(self) -> QWidget:
    self._io_panel = IoPanel(
        on_import=self._on_import_dataset,
        on_load=self._on_load_dataset,
        on_add_layer=self._on_add_layer_to_dataset,
        on_close=self._on_close_dataset,
        on_export_csv=self._on_export_csv,
        on_export_images=self._on_export_images,
        show_status=lambda msg: self.statusBar().showMessage(msg),
    )
    return self._io_panel

def _create_data_panel(self) -> QWidget:
    self._data_panel = DataPanel(
        self.data_model,
        get_store=lambda: getattr(self, "_current_store", None),
        get_viewer_window=lambda: self._windows.get("viewer"),
        get_h5_path=lambda: getattr(self, "_current_h5_path", None),
        show_status=lambda msg: self.statusBar().showMessage(msg),
    )
    return self._data_panel
```

## Key Decisions

**Why callbacks over DI container?** Panels are constructed once at startup with 3-6 dependencies each. A registry/container adds infrastructure for no value.

**Why lambda wiring?** Lambdas provide lazy resolution. `get_repo=lambda: self._repo` reads `self._repo` at call time, not construction time. Critical because `_repo`, `_current_store`, and `_windows["viewer"]` are all `None` at panel construction time.

**Why `Callable[[], Any | None]` instead of typed protocols?** Accessor return types (ViewerWindow, DatasetStore) live in packages the panel should not import. `Any` avoids import coupling. Real type safety comes from the lambda wiring site.

**Why `show_status = lambda _: None` default?** Lets panels be constructed in test harnesses without a status bar. Establishes a convention across all panels.

**Why keep CellDataModel as a direct parameter?** It's the one stable, shared object — exists before panels, never changes identity, has a well-defined public interface. Wrapping it in a callback adds indirection without benefit.

## Prevention Rules

In code review, reject if any of these are true:

1. **Parent private access:** Panel calls `self._parent._private_method()` or reads `self._parent._private_attr`
2. **Pass-through booleans:** Constructor takes flags like `write_to_store: bool` that toggle parent-owned behavior
3. **Circular signal flow:** Panel emits signal, parent handles it by calling back into panel to finish
4. **Constructor receives parent as `self`:** `parent=self` and then `self.parent().<anything beyond geometry>`

## Checklist for Future Panel Extractions

- [ ] Panel owns its data inputs (via constructor args, signals, or DI — never by reaching into parent)
- [ ] Panel emits results as signals or returns via callbacks; never writes to parent-owned storage
- [ ] Panel can be instantiated in a test with mock model and no parent window
- [ ] No imports from the parent module in the panel file
- [ ] Grep confirms zero references to parent's private attributes from the new panel
- [ ] Parent's LOC decreases by roughly the amount the panel contains

## Transitional State

Two components still use `launcher=self`: `SegmentationPanel` (unreformed, 18+ launcher refs) and `GroupedSegPanel` (reached through `AnalysisPanel._launcher_for_grouped`). These are documented tech debt.

## Related Documentation

- `docs/solutions/architecture-decisions/percell4-code-review-findings-phases-0-6.md` — Flagged launcher god object and panel coupling as future concerns
- `docs/solutions/tech-debt/threshold-qc-measurements-write-owned-by-controller.md` — Same anti-pattern: extracted controller still delegates persistence to parent via boolean flag
- `docs/solutions/ui-bugs/percell4-selection-filtering-multi-roi-patterns.md` — Qt signal coalescing and cross-window communication patterns
