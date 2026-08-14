"""Interactive phases: handler registers a callback and returns.

The runner breaks out of its loop on an interactive request, yields
control back to Qt, and resumes when the handler's ``on_complete``
callback fires — matching the real QC-controller pattern that Phase 5
and Phase 6 will use.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import pytest

from percell4.gui.workflows.base_runner import (
    BaseWorkflowRunner,
    PhaseKind,
    PhaseRequest,
    PhaseResult,
    WorkflowEventKind,
)


class InteractiveRunner(BaseWorkflowRunner):
    """Runner with an interactive phase in the middle."""

    def __init__(self) -> None:
        super().__init__()
        self.stored_callback: Callable[[PhaseResult], None] | None = None
        self.phase_order: list[str] = []

    def _phase_generator(self) -> Generator[PhaseRequest, PhaseResult | None, None]:
        # Phase A: unattended
        def handler_a() -> PhaseResult:
            self.phase_order.append("A")
            return PhaseResult()

        result = yield PhaseRequest(
            kind=PhaseKind.UNATTENDED, phase_name="A", handler=handler_a
        )
        assert result is not None and result.success

        # Phase B: interactive. Store the callback; do not fire it here.
        def handler_b(on_complete: Callable[[PhaseResult], None]) -> None:
            self.phase_order.append("B_register")
            self.stored_callback = on_complete

        result = yield PhaseRequest(
            kind=PhaseKind.INTERACTIVE, phase_name="B", handler=handler_b
        )
        # This line only runs after the test calls
        # runner.stored_callback(PhaseResult(...)).
        self.phase_order.append("B_resumed")
        assert result is not None and result.success
        assert result.payload == "user data"

        # Phase C: unattended, after resume
        def handler_c() -> PhaseResult:
            self.phase_order.append("C")
            return PhaseResult()

        yield PhaseRequest(
            kind=PhaseKind.UNATTENDED, phase_name="C", handler=handler_c
        )


def test_interactive_handler_blocks_until_callback(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    runner = InteractiveRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    # After start() returns: phase A ran, phase B's handler registered a
    # callback, the runner is still in RUNNING state waiting for the
    # user to complete the interactive phase.
    assert runner.phase_order == ["A", "B_register"]
    assert runner.state == "running"
    assert runner.stored_callback is not None

    # The launcher was locked and child windows closed, but NOT restored
    # yet — the run isn't finished.
    fake_host.set_workflow_locked.assert_any_call(True)
    assert fake_host.restore_child_windows.call_count == 0

    # Simulate the user clicking Accept in the QC dialog: fire the stored
    # callback with a result.
    runner.stored_callback(PhaseResult(success=True, payload="user data"))

    # Runner resumed: phase B's post-yield code ran, phase C ran, run
    # completed.
    assert runner.phase_order == ["A", "B_register", "B_resumed", "C"]
    assert runner.state == "finished"

    # Terminal event was emitted exactly once.
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is True

    # Host restored on the way out.
    fake_host.set_workflow_locked.assert_any_call(False)
    assert fake_host.restore_child_windows.call_count == 1


def test_interactive_completion_after_finish_is_dropped(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    """Stray callback after _finish should not re-enter the state machine."""
    runner = InteractiveRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)
    assert runner.stored_callback is not None

    # User cancels instead of accepting.
    runner.request_cancel()
    # Now fire the stored callback — the runner has already transitioned
    # on its own because cancel was honoured at the next boundary, but in
    # this test the loop is blocked in the interactive phase so cancel
    # alone doesn't wake it up. The intended pattern is that a QC dialog
    # that's asked to cancel fires on_complete with an error result, so
    # test that flow too.

    # Actually: cancel while parked in an interactive phase is the
    # realistic pattern. The runner returns from request_cancel() without
    # forcing a _finish because the interactive loop is inside the
    # generator — the runner has no way to preempt it. So we rely on the
    # interactive handler to notice the cancel request and fire its
    # on_complete. Test that the runner handles a late callback gracefully:
    # force _finish manually, then fire the stored callback.
    runner._finish(success=False, message="cancelled via test")
    assert runner.state == "finished"

    # Stray callback after _finish: silently dropped, no crash, no
    # re-entry.
    runner.stored_callback(PhaseResult(success=True, payload="late"))

    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].message == "cancelled via test"


def test_interactive_handler_returns_wrong_type(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    """If the handler calls on_complete with a non-PhaseResult, we unwind."""

    class BadInteractiveRunner(BaseWorkflowRunner):
        def __init__(self):
            super().__init__()
            self.stored_callback: Callable | None = None

        def _phase_generator(self):
            def handler(on_complete):
                self.stored_callback = on_complete
            yield PhaseRequest(
                kind=PhaseKind.INTERACTIVE, phase_name="bad", handler=handler
            )

    runner = BadInteractiveRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)
    assert runner.stored_callback is not None

    # Fire the callback with the wrong type.
    runner.stored_callback("not a PhaseResult")  # type: ignore[arg-type]

    assert runner.state == "finished"
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert "returned str" in finished[0].message


def test_unattended_handler_returns_wrong_type(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    """Synchronous handler returning non-PhaseResult should unwind cleanly."""

    class BadSyncRunner(BaseWorkflowRunner):
        def _phase_generator(self):
            def handler():
                return "not a PhaseResult"  # wrong type

            yield PhaseRequest(
                kind=PhaseKind.UNATTENDED, phase_name="bad", handler=handler
            )

    runner = BadSyncRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    assert runner.state == "finished"
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert "expected PhaseResult" in finished[0].message


def test_reentrance_guard(
    qtbot, fake_host, sample_config, sample_metadata
):
    """Calling start() while already running raises RuntimeError."""
    runner = InteractiveRunner()
    runner.start(sample_config, fake_host, sample_metadata)

    # Runner is still in RUNNING state (parked at the interactive phase).
    assert runner.state == "running"

    with pytest.raises(RuntimeError, match="state=running"):
        runner.start(sample_config, fake_host, sample_metadata)

    # Complete the run so the fixture cleanup is clean.
    runner.stored_callback(PhaseResult(success=True, payload="user data"))
    assert runner.state == "finished"


def test_reentrance_after_finish(
    qtbot, fake_host, sample_config, sample_metadata
):
    """Calling start() after a run finished also raises — a single runner
    instance is one-shot. Callers instantiate a new runner per run."""

    class MinimalRunner(BaseWorkflowRunner):
        pass

    runner = MinimalRunner()
    runner.start(sample_config, fake_host, sample_metadata)
    assert runner.state == "finished"

    with pytest.raises(RuntimeError, match="state=finished"):
        runner.start(sample_config, fake_host, sample_metadata)
