"""Generator-driven state machine for batch workflows.

:class:`BaseWorkflowRunner` drives an abstract phase sequence via a Python
generator that yields :class:`PhaseRequest` objects. The runner dispatches
each request, catches exceptions centrally, and emits a single
``workflow_event = Signal(object)`` carrying a :class:`WorkflowEvent`
descriptor.

Why generator-driven instead of nested ``QEventLoop.exec_()``:

    Nested event loops work in PyQt5 but are the most common Qt-Python
    footgun — signals arrive while paused, re-entering slots;
    ``processEvents()`` inside a nested loop corrupts Qt state. The
    generator design sidesteps the whole class of bugs. Each phase yields
    a request; the runner's dispatch code either runs the work
    synchronously (unattended phases, main-thread ``QProgressDialog``
    loop) or registers a completion callback and returns (interactive
    phases). Either way, resumption happens at a natural Qt event
    boundary via ``gen.send(result)`` — never inside a nested event loop.

The dataclasses (:class:`PhaseRequest`, :class:`PhaseResult`,
:class:`WorkflowEvent`) are pure Python. They can be constructed, yielded,
and inspected in unit tests without a running ``QApplication``. Only
``BaseWorkflowRunner`` itself needs Qt, because it inherits from
``QObject`` and emits a ``Signal``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from qtpy.QtCore import QObject, Signal

from percell4.workflows.artifacts import write_run_config
from percell4.workflows.host import WorkflowHost
from percell4.workflows.models import RunMetadata, WorkflowConfig
from percell4.workflows.run_log import RunLog

logger = logging.getLogger(__name__)


class PhaseKind(StrEnum):
    """What the runner should do to satisfy a PhaseRequest."""

    # Synchronous work: the runner calls ``handler()`` on the main thread
    # and proceeds to the next request immediately. Used for unattended
    # phases (compress, segment, measure, export) that run their own
    # main-thread ``QProgressDialog`` loop.
    UNATTENDED = "unattended"

    # Interactive work: the runner calls
    # ``handler(on_complete=self._on_interactive_complete)`` and returns.
    # Control goes back to the Qt event loop; the handler (typically a QC
    # controller) invokes ``on_complete(result)`` later from a slot when
    # the user accepts / cancels. The runner resumes the generator from
    # that callback at a natural Qt event boundary.
    INTERACTIVE = "interactive"


class WorkflowEventKind(StrEnum):
    """Kinds of events emitted by :attr:`BaseWorkflowRunner.workflow_event`."""

    PHASE_STARTED = "phase_started"
    PHASE_PROGRESS = "phase_progress"
    PHASE_COMPLETED = "phase_completed"
    QC_DATASET_READY = "qc_dataset_ready"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True)
class PhaseResult:
    """Return value from a phase handler; sent back into the generator."""

    success: bool = True
    message: str = ""
    # Phase-specific payload (e.g. a dict of per-dataset mask arrays,
    # a GroupingResult, etc.). Pure Python — no Qt dependency.
    payload: Any = None


PhaseHandlerSync = Callable[[], PhaseResult]
PhaseHandlerInteractive = Callable[[Callable[[PhaseResult], None]], None]


@dataclass(frozen=True)
class PhaseRequest:
    """One step of a phase generator — tells the runner what to do next.

    Generators yield ``PhaseRequest`` objects; the runner dispatches them
    and sends back a :class:`PhaseResult`. Pure data — no Qt dependency.

    For :attr:`PhaseKind.UNATTENDED`, ``handler`` must be a zero-argument
    callable returning a :class:`PhaseResult`. For
    :attr:`PhaseKind.INTERACTIVE`, ``handler`` takes exactly one argument,
    the runner's ``on_complete`` callback, and must arrange for it to be
    invoked exactly once when the user finishes the interaction.
    """

    kind: PhaseKind
    phase_name: str
    dataset_index: int = 0
    dataset_total: int = 0
    dataset_name: str = ""
    sub_progress: str = ""
    handler: Callable[..., Any] | None = None
    # Free-form metadata (e.g. the ThresholdingRound spec for a threshold
    # QC request). The concrete runner subclass defines the shape.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowEvent:
    """Single descriptor emitted by :attr:`BaseWorkflowRunner.workflow_event`.

    Matches the ``StateChange`` pattern used by ``CellDataModel``: one
    signal carries one dataclass instance and all subscribers switch on
    ``kind``. Pure Python.
    """

    kind: WorkflowEventKind
    phase_name: str = ""
    current: int = 0
    total: int = 0
    dataset_name: str = ""
    sub_progress: str = ""
    success: bool = True
    message: str = ""


class _RunnerState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"


class BaseWorkflowRunner(QObject):
    """State machine for batch workflows, driven by a Python generator.

    Subclasses implement :meth:`_phase_generator` to yield
    :class:`PhaseRequest` objects. The base class owns:

    - Host locking and child-window teardown / restore
    - The run folder's ``run_config.json`` (initial write + ``finished_at``
      stamp on termination)
    - Cooperative cancellation (checked between requests, so in-flight
      phases run to completion before the runner unwinds)
    - A single ``workflow_event`` signal carrying a :class:`WorkflowEvent`
      descriptor, matching the ``CellDataModel.state_changed`` convention
    - Exception safety: any exception from the generator or a handler is
      caught and routed to :meth:`_finish`, which is idempotent and always
      runs — so the launcher never stays locked after a crash

    Subclasses do not override ``start``/``request_cancel``/``_finish``.
    They only provide :meth:`_phase_generator`.
    """

    # One aggregated signal, per house convention. Subscribers switch on
    # ``event.kind`` rather than connecting to N narrow signals.
    workflow_event = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._state: _RunnerState = _RunnerState.IDLE
        self._generator: Generator[PhaseRequest, PhaseResult | None, None] | None = None
        self._config: WorkflowConfig | None = None
        self._metadata: RunMetadata | None = None
        self._host: WorkflowHost | None = None
        self._run_log: RunLog | None = None
        self._cancel_requested: bool = False
        self._finish_called: bool = False
        # Carries the PhaseResult from the most recent handler completion
        # back into the generator on the next advance.
        self._pending_result: PhaseResult | None = None

    # ── Public API ────────────────────────────────────────────

    @property
    def state(self) -> str:
        """Current runner state: ``"idle"``, ``"running"``, or ``"finished"``."""
        return self._state.value

    @property
    def is_running(self) -> bool:
        return self._state is _RunnerState.RUNNING

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def start(
        self,
        config: WorkflowConfig,
        host: WorkflowHost,
        metadata: RunMetadata,
    ) -> None:
        """Begin a new run. Primes the generator and returns.

        This method returns as soon as the first interactive handler
        registers its callback, or immediately when the run completes
        synchronously (all unattended phases). The caller should connect
        to ``workflow_event`` to observe progress and the terminal
        ``kind=run_finished`` event.

        A single runner instance can only drive one run at a time. Calling
        ``start`` while another run is in progress raises ``RuntimeError``
        — this is the reentrance guard the launcher's Start button relies
        on.
        """
        if self._state is not _RunnerState.IDLE:
            raise RuntimeError(
                f"BaseWorkflowRunner.start() called while state={self._state.value}"
            )

        self._state = _RunnerState.RUNNING
        self._config = config
        self._metadata = metadata
        self._host = host
        self._cancel_requested = False
        self._finish_called = False
        self._pending_result = None

        # Open the run log. RunLog.__init__ creates the parent directory
        # if it does not yet exist, so this is safe even if start() is
        # called before create_run_folder (though normal callers will
        # have already created it).
        try:
            self._run_log = RunLog(metadata.run_folder)
            self._run_log.log(event="run_started", run_id=metadata.run_id)
        except OSError as e:
            self._finish(success=False, message=f"failed to open run log: {e}")
            return

        # Write the initial run_config.json (no finished_at yet). _finish
        # will rewrite it with finished_at stamped on the way out.
        try:
            write_run_config(metadata.run_folder, config, metadata)
        except OSError as e:
            self._finish(success=False, message=f"failed to write run_config.json: {e}")
            return

        # Lock the host and close child windows. Any exception here is a
        # genuine runner-setup error and routes through _finish which will
        # attempt to unlock again (idempotent).
        try:
            host.set_workflow_locked(True)
            host.close_child_windows()
        except Exception as e:
            logger.exception("failed to lock host")
            self._finish(success=False, message=f"failed to lock host: {e}")
            return

        # Build the generator (subclass provides).
        try:
            self._generator = self._phase_generator()
        except Exception as e:
            logger.exception("failed to build phase generator")
            self._finish(success=False, message=f"failed to build generator: {e}")
            return

        # Kick off the loop. This runs synchronous (unattended) phases in
        # a tight loop; the first interactive request breaks out and
        # returns control to Qt until the handler's on_complete fires.
        self._run_loop()

    def request_cancel(self) -> None:
        """Cooperative cancel. Checked at dataset boundaries between requests.

        The in-flight phase (if any) runs to completion before the runner
        unwinds. Cancel is idempotent: calling twice is a no-op. Subclasses
        that kick off a ``Worker`` thread are expected to call
        ``Worker.request_abort()`` in their handler as well, so the worker
        itself stops at the next cooperative check point.
        """
        if self._state is not _RunnerState.RUNNING:
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        if self._run_log is not None:
            try:
                self._run_log.log(event="cancel_requested")
            except OSError:
                logger.exception("failed to write cancel log entry")

    # ── Subclass contract ─────────────────────────────────────

    def _phase_generator(
        self,
    ) -> Generator[PhaseRequest, PhaseResult | None, None]:
        """Yield :class:`PhaseRequest` objects until the run is complete.

        Subclasses override this to produce the concrete phase sequence.
        The base implementation yields nothing, which makes the base class
        usable directly in tests (a zero-phase run completes successfully
        and emits a single ``run_finished`` event).
        """
        if False:  # pragma: no cover - stub for the base class
            yield PhaseRequest(kind=PhaseKind.UNATTENDED, phase_name="")

    # ── Core state machine ────────────────────────────────────

    def _run_loop(self) -> None:
        """Drive the generator synchronously until blocked or finished.

        Runs unattended phases in a tight loop. On the first interactive
        request, dispatches it (which registers a callback and returns)
        and returns itself, yielding control back to the Qt event loop.
        When the interactive callback fires later, it calls
        :meth:`_on_interactive_complete` which re-enters this loop to
        resume.

        Exceptions raised inside the generator or a handler are caught
        and routed to :meth:`_finish`. Cancel requests are checked at the
        top of each loop iteration, so the in-flight phase always runs to
        completion (whatever that looks like for the handler) before the
        runner unwinds.
        """
        if self._state is not _RunnerState.RUNNING:
            return
        if self._generator is None:
            return

        while True:
            # Cancel check: honoured at the boundary between requests.
            if self._cancel_requested:
                self._finish(success=False, message="cancelled")
                return

            # Advance the generator one step.
            try:
                if self._pending_result is None:
                    request = next(self._generator)
                else:
                    request = self._generator.send(self._pending_result)
                    self._pending_result = None
            except StopIteration:
                self._finish(success=True, message="completed")
                return
            except Exception as e:
                logger.exception("workflow generator raised")
                self._finish(success=False, message=f"generator error: {e}")
                return

            # Emit a progress event so subscribers see phase transitions.
            self._emit(
                WorkflowEvent(
                    kind=WorkflowEventKind.PHASE_PROGRESS,
                    phase_name=request.phase_name,
                    current=request.dataset_index,
                    total=request.dataset_total,
                    dataset_name=request.dataset_name,
                    sub_progress=request.sub_progress,
                )
            )

            # Dispatch the request. Synchronous handlers store their
            # PhaseResult in self._pending_result and we loop; interactive
            # handlers return without storing anything and we break out
            # until the callback fires.
            try:
                blocked = self._dispatch_request(request)
            except Exception as e:
                logger.exception("workflow handler raised")
                self._finish(success=False, message=f"handler error: {e}")
                return

            if blocked:
                # Interactive phase is in flight; return control to Qt.
                return
            # else: fall through to the next iteration.

    def _dispatch_request(self, request: PhaseRequest) -> bool:
        """Dispatch a single request. Returns True if the runner is now blocked.

        UNATTENDED handlers run synchronously; the returned
        :class:`PhaseResult` is stored in ``_pending_result`` and the
        loop continues. INTERACTIVE handlers receive the
        ``_on_interactive_complete`` callback and return; the runner
        reports ``blocked=True`` and :meth:`_run_loop` yields back to Qt.
        """
        if request.handler is None:
            # Empty request — treat as a no-op success.
            self._pending_result = PhaseResult(success=True)
            return False

        if request.kind is PhaseKind.UNATTENDED:
            result = request.handler()
            if not isinstance(result, PhaseResult):
                raise TypeError(
                    f"UNATTENDED handler for {request.phase_name!r} returned "
                    f"{type(result).__name__}, expected PhaseResult"
                )
            self._pending_result = result
            return False

        if request.kind is PhaseKind.INTERACTIVE:
            request.handler(self._on_interactive_complete)
            return True

        raise ValueError(f"unknown PhaseKind: {request.kind!r}")

    def _on_interactive_complete(self, result: PhaseResult) -> None:
        """Callback invoked by interactive handlers when the user finishes.

        The handler (typically a QC controller) is responsible for
        ensuring this runs on the main thread — e.g. by connecting its
        own completion signal to a slot that invokes this method. We
        stash the result and re-enter :meth:`_run_loop`.
        """
        if self._state is not _RunnerState.RUNNING:
            # Callback fired after we already finished (e.g. user clicked
            # the QC window's X which triggered cancel → _finish → the
            # controller fires its stale on_complete). Silently drop.
            return
        if not isinstance(result, PhaseResult):
            logger.error(
                "interactive completion callback got non-PhaseResult: %r", result
            )
            self._finish(
                success=False,
                message=f"interactive handler returned {type(result).__name__}",
            )
            return
        self._pending_result = result
        self._run_loop()

    # ── Termination ───────────────────────────────────────────

    def _finish(self, success: bool, message: str) -> None:
        """Unconditional cleanup. Idempotent — safe to call from any path.

        This is the single exit point of the state machine. Every
        termination path — normal completion, explicit cancel, generator
        exception, handler exception, host-lock failure — funnels here.
        On the first call it closes the generator, rewrites
        ``run_config.json`` with ``finished_at`` stamped, unlocks the host,
        restores child windows, and emits exactly one ``run_finished``
        event. Subsequent calls are no-ops.

        None of the cleanup steps are allowed to raise: each is wrapped
        in ``try/except`` so that (for example) a failure to rewrite
        ``run_config.json`` does not prevent the host from being unlocked.
        """
        if self._finish_called:
            return
        self._finish_called = True
        self._state = _RunnerState.FINISHED

        # Close the generator if still alive. This runs any generator-side
        # finally blocks so subclasses can clean up per-run state.
        if self._generator is not None:
            try:
                self._generator.close()
            except Exception:
                logger.exception("error closing phase generator")
            self._generator = None

        # Stamp finished_at and rewrite run_config.json.
        if self._metadata is not None and self._config is not None:
            self._metadata.finished_at = datetime.now(UTC)
            try:
                write_run_config(
                    self._metadata.run_folder, self._config, self._metadata
                )
            except Exception:
                logger.exception("failed to rewrite run_config.json with finished_at")

        # Audit-log the termination.
        if self._run_log is not None:
            try:
                self._run_log.log(
                    event="run_finished",
                    success=success,
                    message=message,
                )
            except Exception:
                logger.exception("failed to write run_finished log entry")

        # Unlock host + restore child windows. Best-effort — never raises
        # out of _finish even if the host itself is broken, because the
        # last thing the user needs in an error state is a silently-stuck
        # launcher.
        if self._host is not None:
            try:
                self._host.restore_child_windows()
            except Exception:
                logger.exception("error restoring child windows")
            try:
                self._host.set_workflow_locked(False)
            except Exception:
                logger.exception("error unlocking host")

        # Emit exactly one run_finished event.
        self._emit(
            WorkflowEvent(
                kind=WorkflowEventKind.RUN_FINISHED,
                success=success,
                message=message,
            )
        )

    # ── Helpers ───────────────────────────────────────────────

    def _emit(self, event: WorkflowEvent) -> None:
        """Emit a workflow_event signal with the given descriptor."""
        self.workflow_event.emit(event)
