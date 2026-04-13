"""Smoke test: a 3-dataset run with a stub unattended phase generator.

Drives a subclass of :class:`BaseWorkflowRunner` end-to-end, asserts the
expected sequence of ``workflow_event`` emissions, and verifies that
``run_config.json`` has a ``finished_at`` timestamp after the run.
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
from percell4.workflows.artifacts import read_run_config


class StubUnattendedRunner(BaseWorkflowRunner):
    """Runner that yields a fixed number of unattended requests."""

    def __init__(self, n_datasets: int = 3) -> None:
        super().__init__()
        self.n_datasets = n_datasets
        self.handler_calls: list[int] = []

    def _phase_generator(self) -> Generator[PhaseRequest, PhaseResult | None, None]:
        for i in range(self.n_datasets):
            def handler(idx=i) -> PhaseResult:
                self.handler_calls.append(idx)
                return PhaseResult(success=True, message=f"done {idx}")

            result = yield PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="stub",
                dataset_index=i,
                dataset_total=self.n_datasets,
                dataset_name=f"DS{i}",
                handler=handler,
            )
            assert result is not None and result.success


def test_smoke_three_unattended_phases(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    runner = StubUnattendedRunner(n_datasets=3)
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    # All three handlers ran in order.
    assert runner.handler_calls == [0, 1, 2]

    # Runner is finished.
    assert runner.state == "finished"
    assert runner.is_running is False

    # Host lock lifecycle: locked once, closed once, restored once,
    # unlocked once.
    fake_host.set_workflow_locked.assert_any_call(True)
    fake_host.set_workflow_locked.assert_any_call(False)
    assert fake_host.close_child_windows.call_count == 1
    assert fake_host.restore_child_windows.call_count == 1

    # Expected emission sequence: three PHASE_PROGRESS then one RUN_FINISHED.
    kinds = [e.kind for e in events]
    assert kinds.count(WorkflowEventKind.PHASE_PROGRESS) == 3
    assert kinds.count(WorkflowEventKind.RUN_FINISHED) == 1
    assert events[-1].kind is WorkflowEventKind.RUN_FINISHED
    assert events[-1].success is True
    assert "complet" in events[-1].message

    # The progress events carry current / total / dataset name. The runner
    # renames PhaseRequest.dataset_index → WorkflowEvent.current so
    # subscribers see consistent progress-bar semantics regardless of what
    # the generator called its counter.
    progress = [e for e in events if e.kind is WorkflowEventKind.PHASE_PROGRESS]
    assert [e.current for e in progress] == [0, 1, 2]
    assert [e.total for e in progress] == [3, 3, 3]
    assert [e.dataset_name for e in progress] == ["DS0", "DS1", "DS2"]


def test_run_config_stamped_with_finished_at(
    qtbot, fake_host, sample_config, sample_metadata
):
    runner = StubUnattendedRunner(n_datasets=2)
    runner.start(sample_config, fake_host, sample_metadata)

    cfg, meta = read_run_config(sample_metadata.run_folder)
    assert meta.finished_at is not None
    assert meta.run_id == sample_metadata.run_id


def test_run_log_entries_written(
    qtbot, fake_host, sample_config, sample_metadata
):
    runner = StubUnattendedRunner(n_datasets=1)
    runner.start(sample_config, fake_host, sample_metadata)

    log_path = sample_metadata.run_folder / "run_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    event_names = [e["event"] for e in events]
    assert "run_started" in event_names
    assert "run_finished" in event_names
    finished = next(e for e in events if e["event"] == "run_finished")
    assert finished["success"] is True


def test_empty_generator_completes_successfully(
    qtbot, fake_host, sample_config, sample_metadata, collect_events
):
    """A subclass that yields nothing should terminate on the first advance."""

    class EmptyRunner(BaseWorkflowRunner):
        pass  # base _phase_generator yields nothing

    runner = EmptyRunner()
    events, slot = collect_events()
    runner.workflow_event.connect(slot)

    runner.start(sample_config, fake_host, sample_metadata)

    assert runner.state == "finished"
    assert events[-1].kind is WorkflowEventKind.RUN_FINISHED
    assert events[-1].success is True
    # Still locked + unlocked the host
    fake_host.set_workflow_locked.assert_any_call(True)
    fake_host.set_workflow_locked.assert_any_call(False)
