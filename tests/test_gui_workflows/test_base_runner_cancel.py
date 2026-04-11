"""Cancel mid-run: the in-flight phase completes, then the runner unwinds.

The plan is explicit: cooperative cancel is checked at dataset boundaries
between requests, not inside a handler. The in-flight handler always runs
to completion before the runner emits ``run_finished(success=False,
message="cancelled")`` and unlocks the host.
"""

from __future__ import annotations

import json
from collections.abc import Generator

from percell4.gui.workflows.base_runner import (
    BaseWorkflowRunner,
    PhaseKind,
    PhaseRequest,
    PhaseResult,
    WorkflowEventKind,
)


class CancellableRunner(BaseWorkflowRunner):
    """Runner that cancels itself after the first handler finishes."""

    def __init__(self, n_datasets: int = 5, cancel_after: int = 1) -> None:
        super().__init__()
        self.n_datasets = n_datasets
        self.cancel_after = cancel_after
        self.handler_calls: list[int] = []

    def _phase_generator(self) -> Generator[PhaseRequest, PhaseResult | None, None]:
        for i in range(self.n_datasets):
            def handler(idx=i) -> PhaseResult:
                self.handler_calls.append(idx)
                if idx + 1 == self.cancel_after:
                    # User clicks Cancel *after* this handler finishes.
                    # The in-flight dataset must still complete.
                    self.request_cancel()
                return PhaseResult(success=True)

            yield PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="stub",
                dataset_index=i,
                dataset_total=self.n_datasets,
                dataset_name=f"DS{i}",
                handler=handler,
            )


def test_cancel_after_first_dataset(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    runner = CancellableRunner(n_datasets=5, cancel_after=1)
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    # Exactly one handler ran. Cancel is checked at the TOP of the next
    # loop iteration, so the in-flight handler finishes and then the
    # runner unwinds before calling the second handler.
    assert runner.handler_calls == [0]

    # Exactly one run_finished event with success=False, message=cancelled.
    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is False
    assert finished[0].message == "cancelled"

    # Host unlocked + restored on the way out.
    fake_host.set_workflow_locked.assert_any_call(False)
    assert fake_host.restore_child_windows.call_count == 1

    # cancel_requested flag is visible to observers.
    assert runner.cancel_requested is True
    assert runner.state == "finished"


def test_cancel_before_start_is_noop(
    qtbot, fake_host, sample_config, sample_metadata
):
    runner = CancellableRunner(n_datasets=1, cancel_after=999)
    # IDLE state
    runner.request_cancel()
    assert runner.cancel_requested is False  # ignored
    assert runner.state == "idle"

    runner.start(sample_config, fake_host, sample_metadata)
    # Should complete normally (cancel_after=999 never triggers).
    assert runner.handler_calls == [0]
    assert runner.state == "finished"


def test_cancel_is_idempotent(
    qtbot, fake_host, sample_config, sample_metadata
):
    """Calling request_cancel twice does not double-log or re-trigger."""

    class TwoShotCancelRunner(BaseWorkflowRunner):
        def __init__(self):
            super().__init__()
            self.called = 0

        def _phase_generator(self):
            for _ in range(3):
                def handler() -> PhaseResult:
                    self.called += 1
                    self.request_cancel()
                    self.request_cancel()  # second call should be a no-op
                    return PhaseResult()
                yield PhaseRequest(
                    kind=PhaseKind.UNATTENDED,
                    phase_name="stub",
                    handler=handler,
                )

    runner = TwoShotCancelRunner()
    runner.start(sample_config, fake_host, sample_metadata)

    assert runner.called == 1
    assert runner.cancel_requested is True


def test_cancel_logs_single_cancel_requested_entry(
    qtbot, fake_host, sample_config, sample_metadata
):
    runner = CancellableRunner(n_datasets=3, cancel_after=1)
    runner.start(sample_config, fake_host, sample_metadata)

    log_path = sample_metadata.run_folder / "run_log.jsonl"
    entries = [json.loads(line) for line in log_path.read_text().strip().splitlines()]
    cancel_entries = [e for e in entries if e["event"] == "cancel_requested"]
    assert len(cancel_entries) == 1

    finished = [e for e in entries if e["event"] == "run_finished"]
    assert len(finished) == 1
    assert finished[0]["success"] is False
    assert finished[0]["message"] == "cancelled"
