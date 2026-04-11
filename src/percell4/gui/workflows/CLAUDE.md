# src/percell4/gui/workflows/

Qt driver for batch workflows. The pure-Python core — config dataclasses,
run-folder I/O, channel intersection, host protocol — lives under
`src/percell4/workflows/`. Everything here is Qt-dependent: a
`QObject`-based state machine, Qt dialogs, QC controllers, and the
`workflow_event = Signal(object)` runner-progress surface.

## Modules

- `base_runner.py` — `BaseWorkflowRunner(QObject)` + the pure-Python
  dataclasses (`PhaseKind`, `PhaseRequest`, `PhaseResult`,
  `WorkflowEventKind`, `WorkflowEvent`) that drive it. The runner owns
  host locking, child-window teardown/restore, `run_config.json`
  lifecycle (initial write + `finished_at` stamp on termination),
  cooperative cancellation, and exception safety (a single idempotent
  `_finish` is the only exit point, so the launcher never stays locked
  after a crash). Subclasses implement `_phase_generator` to yield
  `PhaseRequest` objects; the base class handles everything else.
  Generator-driven on purpose — no nested `QEventLoop.exec_()`, because
  unattended phases run synchronously in a tight loop and interactive
  phases register a completion callback and return, resuming at
  natural Qt event boundaries.

## Subpackages

- `single_cell/` — concrete UI for the **single-cell thresholding
  analysis workflow**:
  - `config_dialog.py` — `WorkflowConfigDialog` (dataset picker,
    Cellpose settings, thresholding rounds table, CSV column picker,
    output parent)
  - `runner.py` — `SingleCellThresholdingRunner(BaseWorkflowRunner)`.
    Phase generator yields UNATTENDED compress/threshold-compute/
    threshold-apply/measure/export requests, INTERACTIVE segment
    (Worker-backed) + seg QC + threshold QC requests. Has an
    `interactive_qc=True/False` switch for production vs tests.
    Propagates `request_cancel()` to the in-flight Cellpose Worker
  - `seg_qc.py` — `SegmentationQCController`. Per-dataset interactive
    label editor: delete / draw / edge-cleanup tools, Ctrl+Enter
    accept, Esc cancel, layer visibility save/restore, signal
    coalescing. Persists edited labels to `/labels/cellpose_qc` on
    accept, restores hidden viewer layers on any exit
  - `threshold_qc_queue.py` — `ThresholdQCQueueEntry`. Thin per-dataset
    wrapper that instantiates the existing `ThresholdQCController`
    with `write_measurements_to_store=False`, forwards its
    `on_complete(success, msg)` into a `PhaseResult` for the runner
