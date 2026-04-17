---
title: "refactor: Port percell4 to hexagonal architecture with reactive Session"
type: refactor
date: 2026-04-16
supersedes: 2026-04-16-refactor-ui-architecture-service-extraction-plan.md
---

# refactor: Hexagonal architecture with reactive Session

## TL;DR

The prior plan ("decompose the launcher god object into services") is a UI cleanup, not an architectural refactor. It leaves business logic in `gui/`, preserves the launcher as composition root, and lists five invariants that the refactor must preserve — which is the tell that the architecture hasn't actually simplified.

This plan replaces it with: **port percell's ports-and-adapters architecture (which the author already built and liked) into percell4, with a reactive Session in the application layer to handle multi-view synchronization, and treat napari as a driven adapter rather than a source of truth.**

Outcomes targeted, in order of priority:

1. **Fewer bugs** — propagation bugs become impossible because propagation is centralized in the Session, not scattered across launcher/viewer/store/model.
2. **CLI mode** — `domain/` and `application/` never import Qt, napari, or PyQt, enforced by import-linter. A CLI is a second driving adapter, not a rewrite.
3. **Easier features** — adding a feature is "add a use case + wire a button." The launcher doesn't grow.

## Why the prior plan was wrong for this codebase

Stated precisely so it's easy to revisit if someone disagrees later:

1. **The prior plan diagnoses `launcher.py`'s size as the problem.** The real problem is that domain logic lives in the UI layer and there is no architectural seam separating it from infrastructure. Extracting two services *inside* `gui/` doesn't establish the seam; it reorganizes the UI.
2. **The prior plan explicitly defers the seam-creating move** ("extract a Qt-free core package — not worth it now"). The author has since confirmed CLI/headless mode is a real goal. Deferring the seam-creating move is now incorrect.
3. **The prior plan lists 5 critical invariants the refactor must preserve** (store-before-layer ordering, main-thread-only services, metadata tagging, signal blocking, viewer teardown). If a refactor still requires 5 invariants to stay bug-free, the architecture hasn't simplified — it's been re-partitioned. In the new architecture, these invariants become properties of specific components (the use case, the adapter) where they're encapsulated, not laws the whole system must respect.
4. **The prior plan explicitly rejects the move that would actually fix the bug pattern:** centralizing state in a single observable Session. Nearly every bug in the referenced solution docs is a propagation bug — A updated B but not C, or A updated B before storing in D. A single Session with consistent event ordering eliminates the class.
5. **The author has lived evidence that hex works for their domain.** The prior percell repo is hex, CLI-first, and the author's own report was "very few bugs, very easy to implement new features." The prior plan does not engage with this evidence.

## Architecture overview

```
┌──────────────────────────────────────────────────────────────┐
│  interfaces/ (driving adapters — entry points)                │
│    gui/       Qt windows, peer views, task panels            │
│    cli/       Headless commands (future)                      │
│    batch/     Workflow runner                                 │
└──────────────────────────────────────────────────────────────┘
                              │ calls ↓      subscribes ↑
┌──────────────────────────────────────────────────────────────┐
│  application/                                                 │
│    session.py          Observable state hub (reactive)        │
│    use_cases/          load_dataset, segment_cells,           │
│                        compute_phasor, apply_threshold, ...   │
│    workflows/          Composition of use cases               │
│                                                               │
│  No Qt. No napari. No h5py-as-API. Enforced by import-linter.│
└──────────────────────────────────────────────────────────────┘
                              │ depends on ↓
┌──────────────────────────────────────────────────────────────┐
│  domain/       Pure logic. Dataclasses, algorithms, policies.│
│  ports/        Protocols that domain/application require.    │
└──────────────────────────────────────────────────────────────┘
                              ▲ implemented by
┌──────────────────────────────────────────────────────────────┐
│  adapters/ (driven adapters)                                  │
│    hdf5_store.py       DatasetRepository port impl            │
│    napari_viewer.py    ViewerPort impl (the ONLY napari site) │
│    cellpose.py         Segmenter port impl                    │
│    tifffile_io.py      ImageIO port impl                      │
└──────────────────────────────────────────────────────────────┘
```

Directory structure:

```
src/percell4/
├── domain/              # pure Python, no external deps beyond numpy/pandas
├── application/
│   ├── session.py
│   ├── use_cases/
│   └── workflows/
├── ports/               # protocols (interfaces)
├── adapters/            # concrete implementations of ports
├── interfaces/
│   ├── gui/
│   │   ├── app.py              # composition root
│   │   ├── main_window.py      # Qt chrome only
│   │   ├── task_panels/        # domain task panels (threshold, segment, ...)
│   │   ├── peer_views/         # scatter, table, phasor — subscribe to Session
│   │   └── napari_host.py      # creates napari viewer at startup
│   ├── cli/                    # future
│   └── batch/
└── (legacy gui/, io/, measure/, flim/, segment/ are migrated in)
```

## The Session pattern

The Session is the answer to "how does hex handle synchronized interactive views?"

It is a single observable state object in the application layer. It owns:
- Current dataset handle (via repository port)
- Current selection (cell IDs, active layer references as *names*, not napari objects)
- Current filter state
- Transient task state (e.g., threshold preview parameters)

It exposes:
- Read accessors (pure queries)
- Mutators (small, explicit: `set_selection`, `set_active_segmentation`)
- Events (signal-style: `dataset_loaded`, `selection_changed`, `measurements_updated`)

It does not:
- Run business logic (use cases do that, and they mutate the Session at the end)
- Know about Qt (it's in `application/`; it uses a plain observer/signal abstraction)
- Know about napari (napari subscribes to it via the adapter, one-way)

**The key rule about interaction granularity:**

- **"Selection changed" / "filter changed" / "active layer changed"** are direct Session mutations — no use case. They're pure state.
- **"Load dataset" / "run segmentation" / "apply threshold" / "compute phasor"** are use cases — they touch infrastructure, run computation, and mutate the Session at the end.

This distinction is what the prior plan missed and why it couldn't cleanly decouple peer views.

### Session sketch

```python
# application/session.py
from dataclasses import dataclass, field
from typing import Callable, Protocol
from percell4.domain.dataset import DatasetHandle, ChannelName
from percell4.domain.selection import CellId, LayerName

class Observer(Protocol):
    def __call__(self) -> None: ...

@dataclass
class Session:
    _dataset: DatasetHandle | None = None
    _active_segmentation: LayerName | None = None
    _active_mask: LayerName | None = None
    _selection: frozenset[CellId] = frozenset()
    _filter_expr: str | None = None

    # --- event machinery (trivial observer pattern; no Qt) ---
    _observers: dict[str, list[Observer]] = field(default_factory=dict)

    def subscribe(self, event: str, cb: Observer) -> Callable[[], None]:
        self._observers.setdefault(event, []).append(cb)
        return lambda: self._observers[event].remove(cb)

    def _emit(self, event: str) -> None:
        for cb in list(self._observers.get(event, ())):
            cb()

    # --- queries ---
    @property
    def dataset(self) -> DatasetHandle | None:
        return self._dataset

    @property
    def selection(self) -> frozenset[CellId]:
        return self._selection

    # --- mutations (called by use cases and peer views) ---
    def set_dataset(self, ds: DatasetHandle | None) -> None:
        self._dataset = ds
        self._active_segmentation = None
        self._active_mask = None
        self._selection = frozenset()
        self._emit("dataset_changed")

    def set_selection(self, ids: frozenset[CellId]) -> None:
        if ids == self._selection:
            return
        self._selection = ids
        self._emit("selection_changed")

    def set_active_segmentation(self, name: LayerName | None) -> None:
        self._active_segmentation = name
        self._emit("active_segmentation_changed")
```

Notes on the sketch:
- No Qt in the Session. Peer views that happen to be Qt widgets can wrap `subscribe()` in a `QTimer.singleShot(0, cb)` if they need main-thread marshaling, but that's a presentation detail.
- Frozen set for selection — immutability avoids a whole class of "who mutated my copy" bugs that the prior plan's `CellDataModel` allowed.
- `_emit` iterates a copy of the observer list so unsubscribing inside a callback is safe.
- Events are strings here for brevity; in practice, use a small `Enum` to get autocomplete and typo-safety.

## The ViewerPort pattern

Napari's integration is shaped around three roles the user confirmed: **explore, draw shapes, select labels.** The port is use-case-level, not napari-level.

```python
# ports/viewer.py
from typing import Protocol, Callable
from percell4.domain.dataset import DatasetView, ChannelName
from percell4.domain.segmentation import SegmentationView
from percell4.domain.phasor import PhasorPreview
from percell4.domain.shapes import Shape, ShapeKind
from percell4.domain.selection import CellId, SelectionMode

class ShapeRequest(Protocol):
    """Handle for a pending 'draw me a shape' interaction."""
    def on_complete(self, cb: Callable[[list[Shape]], None]) -> None: ...
    def cancel(self) -> None: ...

class SelectionRequest(Protocol):
    def on_complete(self, cb: Callable[[frozenset[CellId]], None]) -> None: ...
    def cancel(self) -> None: ...

class Subscription(Protocol):
    def unsubscribe(self) -> None: ...

class ViewerPort(Protocol):
    # --- Role 1: explore (fire-and-forget configuration) ---
    def show_dataset(self, view: DatasetView) -> None: ...
    def show_segmentation(self, seg: SegmentationView) -> None: ...
    def show_threshold_preview(self, preview: "ThresholdPreviewView") -> None: ...
    def clear(self) -> None: ...

    # --- Role 2: draw shapes ---
    def request_shapes(
        self,
        prompt: str,
        kinds: list[ShapeKind],
    ) -> ShapeRequest: ...

    def subscribe_shapes(
        self,
        cb: Callable[[Shape], None],
    ) -> Subscription: ...

    # --- Role 3: select labels ---
    def request_label_selection(
        self,
        segmentation: LayerName,
        mode: SelectionMode,
    ) -> SelectionRequest: ...

    def subscribe_label_selection(
        self,
        segmentation: LayerName,
        cb: Callable[[frozenset[CellId]], None],
    ) -> Subscription: ...

    # --- lifecycle ---
    def close(self) -> None: ...
```

The adapter (`adapters/napari_viewer.py`) is the only file in the codebase that imports napari. It:
- Owns a single `napari.Viewer` instance for the app's lifetime.
- Translates napari events into domain types in callbacks.
- Honors one-way binding: napari events outside an active `request_*` / `subscribe_*` are ignored.
- Encapsulates the invariants the prior plan listed: metadata tagging, signal blocking on combo repopulation (moot once combos are in task panels, not napari), correct layer ordering.

## Use case pattern

A use case is a small class (or function) that orchestrates domain operations. It's the only place "work gets done" outside the domain itself.

```python
# application/use_cases/load_dataset.py
from pathlib import Path
from percell4.ports.dataset_repository import DatasetRepository
from percell4.ports.viewer import ViewerPort
from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle, DatasetView

class LoadDataset:
    def __init__(
        self,
        repo: DatasetRepository,
        viewer: ViewerPort,
        session: Session,
    ) -> None:
        self._repo = repo
        self._viewer = viewer
        self._session = session

    def execute(self, path: Path) -> DatasetHandle:
        handle = self._repo.open(path)
        view = self._repo.build_view(handle)  # pure read, no side effects

        # Order matters: store is authoritative before viewer reconfigures.
        self._session.set_dataset(handle)
        self._viewer.show_dataset(view)

        return handle
```

Notes:
- The use case receives its dependencies at construction (DI).
- No Qt import. No napari import. Provably so: import-linter will fail the build.
- The store-before-layer invariant from the prior plan becomes a property of *this function's implementation* — not a system-wide rule. If it's violated, it's violated in one place and easy to see.
- Testable in plain pytest with a fake repo, fake viewer, real Session.

## Peer views

Peer views (scatter, phasor plot, cell table) are Qt widgets in `interfaces/gui/peer_views/`. Each one:
1. Receives a `Session` reference at construction.
2. Subscribes to relevant Session events.
3. On user interaction, either calls a Session mutator (for selection/filter changes) or calls a use case (for operations that do work).
4. Never knows any other peer view exists.

```python
# interfaces/gui/peer_views/cell_table.py
class CellTableWidget(QTableView):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self._session = session
        self._unsub_sel = session.subscribe("selection_changed", self._on_selection_changed)
        self._unsub_meas = session.subscribe("measurements_updated", self._on_measurements_updated)
        self.selectionModel().selectionChanged.connect(self._on_user_selected)

    def _on_user_selected(self, *_):
        ids = frozenset(self._ids_for_current_qt_selection())
        self._session.set_selection(ids)

    def _on_selection_changed(self):
        self._highlight_rows_for(self._session.selection)

    def closeEvent(self, e):
        self._unsub_sel(); self._unsub_meas()
        super().closeEvent(e)
```

Notice what's missing: any reference to the viewer, to the scatter plot, to the launcher, to the store. This is the property the prior plan was trying to achieve with `DatasetService` and `ViewerFacade` injection — but achieved here without needing those classes, because the Session absorbs the coupling.

## Napari lifecycle

Napari launches once at app startup and persists. The architectural contract is unchanged — calls to `viewer.show_dataset(...)` etc. produce a known state regardless of history — but the implementation reuses the existing viewer instead of creating a new one per task.

```python
# interfaces/gui/app.py (composition root)

def main() -> int:
    qt_app = QApplication(sys.argv)

    # --- infrastructure ---
    repo: DatasetRepository = Hdf5DatasetRepository()
    segmenter: Segmenter = CellposeSegmenter()

    # --- application ---
    session = Session()

    # --- napari launches here, ONCE, and lives for the app lifetime ---
    napari_host = NapariHost()  # creates napari.Viewer(), hidden initially
    viewer: ViewerPort = NapariViewerAdapter(napari_host)

    # --- use cases (constructed once, reused) ---
    load_dataset = LoadDataset(repo, viewer, session)
    apply_threshold = ApplyThreshold(repo, viewer, session)
    # ... etc

    # --- peer views ---
    table = CellTableWidget(session)
    scatter = ScatterWidget(session)
    phasor = PhasorWidget(session)

    # --- task panels (receive the use cases they need) ---
    threshold_panel = ThresholdPanel(apply_threshold, viewer, session)

    main_window = MainWindow(
        peer_views=[table, scatter, phasor],
        task_panels=[threshold_panel, ...],
        napari_host=napari_host,
    )
    main_window.show()
    return qt_app.exec_()
```

Cold-start cost is paid once, at `qt_app.show()`. Subsequent task clicks reuse the viewer. The user experiences napari as always-available; the architecture treats it as per-task-reconfigured. Both properties hold.

## Handling the three hard tradeoffs

The author asked for explicit solutions to the three tradeoffs flagged during design:

**Tradeoff 1: duplicating state that napari already tracks.**

Solution: **one-way binding, Session → napari, never reverse.** The Session owns domain state (active segmentation, selection). The adapter mirrors Session → napari. Napari events outside an active `request_*` / `subscribe_*` are dropped. No duplication because napari is never consulted as a state source.

**Tradeoff 2: request/response over napari is async.**

Solution: **`ShapeRequest` / `SelectionRequest` expose both signal-style and awaitable APIs.** Task panels that want to block on user input wait on `request.on_complete(cb)`. Tests use `qtbot.waitSignal`. No napari types leak through the port.

**Tradeoff 3: the temptation to "just read it from napari."**

Solution: **import-linter makes it impossible.** `application/` and `domain/` cannot import napari. If the author ever reaches for a shortcut, the lint fails before the commit. The boundary is enforced by the build, not by discipline.

## Implementation plan

Six stages. Staged so that each stage ends in a working state (the app may be ugly or incomplete, but it starts, loads a dataset, and you can exit cleanly). Solo-dev sized.

### Stage 0: Characterization tests + architectural recon

Time: 1-2 days. No production code changes.

- [x] Write pytest characterization tests around the key behaviors (the "invariants" from the prior plan). These will survive into the new architecture as integration tests against use cases. Reuse existing `conftest.py` fixtures.
- [x] Do a launcher-method classification pass. For each of the 80 launcher methods, tag which layer it belongs in: domain / application / ports / adapters / interfaces. Output: a markdown table.
- [x] Set up `importlinter` with the contracts listed earlier. Initially, contracts will fail (because everything is in `gui/`); that's fine. The contracts tell you when migration is done.

Deliverable: test suite + migration spreadsheet + ~~failing~~ passing lint config (contracts pass because new packages are clean).

### Stage 1: Empty scaffolding + vertical slice (Load Dataset)

Time: 1 week.

Goal: prove the architecture works end-to-end for the simplest real flow. If this fails, stop and re-plan.

Steps:
1. [x] Create empty `domain/`, `application/`, `ports/`, `adapters/`, `interfaces/gui/` packages.
2. [x] Define `DatasetRepository` port in `ports/`.
3. [x] Define `ViewerPort` in `ports/` (initial minimum: `show_dataset`, `clear`, `close`).
4. [x] Port `store.py` into `adapters/hdf5_store.py` as the `DatasetRepository` implementation. No behavior changes — this is a rename + protocol conformance.
5. [x] Build the initial `Session` with `set_dataset` and `dataset_changed` event.
6. [x] Write the `LoadDataset` use case.
7. [x] Write `NapariViewerAdapter.show_dataset` — enough napari integration to display a loaded dataset.
8. [x] In `interfaces/gui/app.py`, wire a minimal composition root.
9. [x] In `interfaces/gui/main_window.py`, add a "Load Dataset" button that calls the use case. Nothing else.
10. [x] Delete nothing from the existing launcher yet. Run the new `app.py` as a separate entry point alongside the old launcher.

**Acceptance:** you can launch the new app, click "Load Dataset," and see it in napari. Tests pass. Import-linter passes for the three new files.

**Exit criteria you must honestly evaluate:**
- Did the Session's event model feel natural or fighting you?
- Did the ViewerPort surface turn out to be the right shape?
- Did the adapter's napari handling feel clean or awkward?

If any of those are "awkward," revise the architecture before continuing. This stage is cheap to throw away. Stage 2+ is not.

### Stage 2: Migrate domain computation

Time: 3-5 days.

- Move `measure/`, `flim/`, `segment/`, `io/` into `domain/` and `adapters/` as appropriate:
  - Pure functions (e.g., `measure_multichannel`, `compute_phasor_from_decay`) → `domain/`
  - External-tool wrappers (Cellpose invocation, tifffile I/O) → `adapters/` with ports in `ports/`
- Rename where it improves clarity. Don't rename where it doesn't.
- Update all existing tests to new imports.

No behavior changes. Import-linter's `domain-is-pure` contract should now pass.

### Stage 3: Build the Session out + peer views

Time: 1 week.

- Expand Session with selection, filter, active-segmentation state and events.
- Migrate `cell_table.py`, `scatter`, `phasor_plot.py`, `data_plot.py` into `interfaces/gui/peer_views/`. Each one: strip launcher references, receive Session, subscribe.
- Migrate `CellDataModel`'s event responsibilities into the Session. `CellDataModel` disappears.

Peer views now work against Session. Old launcher still exists; it's getting smaller.

### Stage 4: Build out use cases + task panels

Time: 2-3 weeks.

One domain at a time:
- Segmentation: `SegmentCells` use case + `SegmentationPanel` in `task_panels/`
- Thresholding: `ApplyThreshold` use case + `ThresholdPanel` (exercises `request_shapes`)
- FLIM / phasor: `ComputePhasor`, `ApplyWavelet` use cases + panels
- Measurement: `MeasureCells` use case + panel
- Export: use cases + panel

Each migration: write the use case (pure, testable), write the panel (wires buttons to use cases + Session), delete the corresponding methods from the old launcher.

By end of stage 4, the launcher is either empty or only contains window-management scaffolding.

### Stage 5: Retire the launcher + workflow rework

Time: 1 week.

- Delete `launcher.py`.
- Reimplement `workflows/` as compositions of use cases. `WorkflowHost` protocol goes away (workflows no longer need a GUI handle; they need a Session and a set of use cases).
- Batch workflow tests migrate to drive use cases directly.

### Stage 6: CLI adapter (validation of the seam)

Time: 2-3 days.

- Write a `interfaces/cli/` entry point that loads a dataset, runs segmentation, applies threshold, and exports — all through the same use cases.
- If this requires importing Qt or napari into `application/` or `domain/`, the seam is broken and must be fixed. This stage is the proof that Stage 1-5 were done correctly.

**Total: roughly 6-8 weeks of focused solo work, 3-4 months part-time.**

## What to preserve, what to throw away

### Preserve from the existing codebase

- `CellDataModel`'s instinct and shape (becomes the Session, moved to `application/`).
- Pure computation in `measure/`, `flim/`, `segment/`, `io/` (becomes `domain/`).
- `store.py`'s implementation (becomes `adapters/hdf5_store.py`; gets a port in front).
- `ThresholdQCController`'s DI pattern (becomes the norm for all task panels).
- `theme.py` and styling (lives in `interfaces/gui/`; orthogonal to this refactor).
- Tests for non-GUI code (will mostly work after rename; fix imports).

### Preserve from the prior plan

- **Phase 0 (characterization tests).** Non-negotiable. Becomes Stage 0 here.
- **The "don't touch what already works" discipline.** Applied here to anything that's already pure-Python and testable.
- **The invariants list.** In the new architecture they become properties of specific components (the use case enforces store-before-event; the adapter enforces metadata tagging), not system-wide rules.
- **Honest line-count and scope accounting.** This plan owes the same honesty: Stage 1 may take 2 weeks, not 1. Stage 4 may be painful.

### Throw away

- The launcher as a concept. There's an `app.py` composition root and a `MainWindow`; no god object remains.
- `WorkflowHost` protocol in its current form.
- `DatasetService` and `ViewerFacade` as designed in the prior plan (wrong layer; their responsibilities get absorbed by use cases + the Session + the ViewerPort).
- Backward-compat shim properties during migration. The user is OK with breakage; stop paying the cognitive tax of temporary aliases.

## Risks and honest limitations

| Risk | Mitigation |
|------|-----------|
| Stage 1 reveals the ViewerPort is the wrong shape | Stage 1 is explicitly a throwaway-if-needed checkpoint. Revise before Stage 2. |
| Hex vocabulary creeps into domain-expert territory (ubiquitous language conflict) | Use percell's prior vocabulary where it exists. "Dataset," "segmentation," "threshold," "phasor" — not "aggregate root." |
| Napari's two-way event model leaks despite the port | One-way binding is a discipline, enforced at the adapter. If it leaks, the adapter is buggy; treat as a bug to fix, not an architectural concession. |
| Session becomes a new god object | The test: does every piece of state in the Session need to be observed by multiple views? If not, it belongs in a more local scope. Revisit at end of Stage 3. |
| Solo-dev context-switching between layers is exhausting | Migrate one vertical slice at a time (Stage 4 is structured this way). Don't touch domain + application + adapter + GUI in the same week. |
| The 6-8 week estimate is optimistic | It always is. Build in 50% slack. If Stage 1 takes more than 2 weeks, re-examine assumptions before continuing. |
| CLI mode (Stage 6) reveals a Qt leak in application/ | That's the point — the stage exists to catch it. Fix the leak; don't make the CLI tolerate Qt. |

## What this plan does NOT do

- **Does not promise a line-count reduction.** That's a UI refactor metric. The new system may have more files than the old one — but each file is smaller, more focused, and individually testable.
- **Does not address GUI polish, theme consistency, or styling.** Orthogonal.
- **Does not introduce a DI framework.** Constructor injection in `app.py` is the pattern. No `python-dependency-injector` or similar.
- **Does not introduce an event bus.** The Session is the event hub. One hub, not two.
- **Does not try to replace napari.** Napari is the right tool; it's just wrapped behind a port so the rest of the system doesn't depend on its shape.

## Success criteria

Evaluated at the end of Stage 6:

- [ ] `grep -r "import napari\|import PyQt\|import qtpy" src/percell4/domain/ src/percell4/application/ src/percell4/ports/` returns nothing.
- [ ] Import-linter contracts all pass.
- [ ] CLI adapter runs end-to-end without importing Qt or napari at any point.
- [ ] All peer views (table, scatter, phasor) receive only a `Session` reference — no launcher, no other view, no viewer directly.
- [ ] Load → segment → threshold → measure → export workflow runs through the GUI, passing through use cases that are covered by pytest without qtbot.
- [ ] The ~7 solution docs referenced in the prior plan (ui-bugs, logic-errors) describe classes of bug that the new architecture structurally prevents. At least 4 of them should be testable as "this bug cannot be expressed in the new architecture" rather than "we added a guard."

If these pass, the refactor achieved its stated goals. If they don't, the refactor is incomplete — not "done with some tech debt."
