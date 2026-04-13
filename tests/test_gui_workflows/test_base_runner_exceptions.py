"""Exception safety: generator and handler errors always unlock the host.

The plan's strongest promise is that an uncaught exception inside the
generator or a phase handler **never** leaves the launcher locked or the
child windows detached. Every termination path funnels through the
idempotent ``_finish`` which restores everything exactly once.
"""

from __future__ import annotations

from collections.abc import Generator

from percell4.gui.workflows.base_runner import (
    BaseWorkflowRunner,
    PhaseKind,
    PhaseRequest,
    PhaseResult,
    WorkflowEventKind,
)


class HandlerRaisesRunner(BaseWorkflowRunner):
    """Runner whose second handler raises."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[int] = []

    def _phase_generator(self) -> Generator[PhaseRequest, PhaseResult | None, None]:
        def handler_ok() -> PhaseResult:
            self.calls.append(0)
            return PhaseResult()

        def handler_boom() -> PhaseResult:
            self.calls.append(1)
            raise RuntimeError("synthetic handler failure")

        yield PhaseRequest(
            kind=PhaseKind.UNATTENDED, phase_name="stub", handler=handler_ok
        )
        yield PhaseRequest(
            kind=PhaseKind.UNATTENDED, phase_name="stub", handler=handler_boom
        )
        yield PhaseRequest(
            kind=PhaseKind.UNATTENDED, phase_name="stub", handler=handler_ok
        )


class GeneratorRaisesRunner(BaseWorkflowRunner):
    """Runner whose generator itself raises after the first yield."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def _phase_generator(self) -> Generator[PhaseRequest, PhaseResult | None, None]:
        def handler() -> PhaseResult:
            self.calls.append("handler")
            return PhaseResult()

        yield PhaseRequest(
            kind=PhaseKind.UNATTENDED, phase_name="stub", handler=handler
        )
        raise RuntimeError("synthetic generator failure")


def test_handler_exception_unlocks_host_once(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    runner = HandlerRaisesRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    # First handler ran, second raised, third never reached.
    assert runner.calls == [0, 1]
    assert runner.state == "finished"

    # Exactly one run_finished event with success=False.
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert "handler error" in finished[0].message
    assert "synthetic handler failure" in finished[0].message

    # Host was unlocked and child windows restored.
    fake_host.set_workflow_locked.assert_any_call(False)
    assert fake_host.restore_child_windows.call_count == 1


def test_generator_exception_unlocks_host_once(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    runner = GeneratorRaisesRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    assert runner.calls == ["handler"]  # the one handler ran
    assert runner.state == "finished"

    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert "generator error" in finished[0].message
    assert "synthetic generator failure" in finished[0].message

    fake_host.set_workflow_locked.assert_any_call(False)
    assert fake_host.restore_child_windows.call_count == 1


def test_host_lock_failure_still_unlocks(
    qtbot, sample_config, sample_metadata, collect_events
):
    """If set_workflow_locked(True) itself raises, _finish still unlocks.

    This is the worst-case host breakage: the runner can't even get
    started. We still need to end in the FINISHED state and emit exactly
    one run_finished(success=False) so the caller knows what happened.
    """
    from unittest.mock import MagicMock

    from percell4.workflows.host import WorkflowHost

    broken_host = MagicMock(spec=WorkflowHost)

    # set_workflow_locked(True) raises on the first call, works on the
    # second (during _finish).
    call_count = {"n": 0}

    def flaky_lock(locked: bool) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1 and locked:
            raise RuntimeError("synthetic lock failure")

    broken_host.set_workflow_locked.side_effect = flaky_lock

    runner = BaseWorkflowRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, broken_host, sample_metadata)

    # The second lock call (False, during _finish) DID run.
    assert call_count["n"] == 2
    # run_finished was still emitted exactly once.
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert "lock host" in finished[0].message


def test_restore_windows_failure_does_not_prevent_unlock(
    qtbot, sample_config, sample_metadata, collect_events
):
    """If restore_child_windows raises in _finish, unlock still runs."""
    from unittest.mock import MagicMock

    from percell4.workflows.host import WorkflowHost

    host = MagicMock(spec=WorkflowHost)
    host.restore_child_windows.side_effect = RuntimeError("synthetic restore failure")

    runner = BaseWorkflowRunner()  # empty generator
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, host, sample_metadata)

    # Despite restore raising, unlock(False) was still called.
    host.set_workflow_locked.assert_any_call(False)
    # And a single run_finished event fired.
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1


def test_finish_is_idempotent(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    """Calling _finish multiple times only emits one event."""
    runner = BaseWorkflowRunner()  # empty generator
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)
    # At this point the run is already FINISHED.
    # Re-invoking _finish should be a no-op.
    runner._finish(success=False, message="should be ignored")
    runner._finish(success=False, message="also ignored")

    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is True  # from the original completion
