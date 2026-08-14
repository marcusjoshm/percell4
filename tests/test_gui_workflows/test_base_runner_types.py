"""Tests for the pure-Python dataclasses in ``base_runner``.

These tests do not need a ``QApplication`` — the success criterion
"runner is unit-testable without a running QApplication via the
PhaseRequest/PhaseResult generator protocol" is satisfied by this file.
"""

from __future__ import annotations

import pytest

from percell4.gui.workflows.base_runner import (
    PhaseKind,
    PhaseRequest,
    PhaseResult,
    WorkflowEvent,
    WorkflowEventKind,
)


def test_phase_kind_values():
    assert PhaseKind("unattended") is PhaseKind.UNATTENDED
    assert PhaseKind("interactive") is PhaseKind.INTERACTIVE


def test_workflow_event_kind_values():
    assert WorkflowEventKind("phase_started") is WorkflowEventKind.PHASE_STARTED
    assert WorkflowEventKind("phase_progress") is WorkflowEventKind.PHASE_PROGRESS
    assert WorkflowEventKind("phase_completed") is WorkflowEventKind.PHASE_COMPLETED
    assert WorkflowEventKind("qc_dataset_ready") is WorkflowEventKind.QC_DATASET_READY
    assert WorkflowEventKind("run_finished") is WorkflowEventKind.RUN_FINISHED


def test_phase_request_minimal():
    req = PhaseRequest(kind=PhaseKind.UNATTENDED, phase_name="compress")
    assert req.kind is PhaseKind.UNATTENDED
    assert req.phase_name == "compress"
    assert req.dataset_index == 0
    assert req.dataset_total == 0
    assert req.dataset_name == ""
    assert req.handler is None
    assert req.metadata == {}


def test_phase_request_with_fields():
    def handler() -> PhaseResult:
        return PhaseResult()

    req = PhaseRequest(
        kind=PhaseKind.UNATTENDED,
        phase_name="segment",
        dataset_index=2,
        dataset_total=5,
        dataset_name="DS3",
        sub_progress="Cellpose...",
        handler=handler,
        metadata={"round": "GFP_bright"},
    )
    assert req.dataset_index == 2
    assert req.dataset_total == 5
    assert req.handler is handler
    assert req.metadata == {"round": "GFP_bright"}


def test_phase_request_is_frozen():
    req = PhaseRequest(kind=PhaseKind.UNATTENDED, phase_name="compress")
    with pytest.raises((AttributeError, TypeError)):
        req.phase_name = "mutated"  # type: ignore[misc]


def test_phase_result_defaults():
    result = PhaseResult()
    assert result.success is True
    assert result.message == ""
    assert result.payload is None


def test_phase_result_is_frozen():
    result = PhaseResult(success=True)
    with pytest.raises((AttributeError, TypeError)):
        result.success = False  # type: ignore[misc]


def test_workflow_event_defaults():
    event = WorkflowEvent(kind=WorkflowEventKind.RUN_FINISHED)
    assert event.kind is WorkflowEventKind.RUN_FINISHED
    assert event.success is True
    assert event.message == ""
    assert event.phase_name == ""


def test_workflow_event_is_frozen():
    event = WorkflowEvent(kind=WorkflowEventKind.RUN_FINISHED)
    with pytest.raises((AttributeError, TypeError)):
        event.success = False  # type: ignore[misc]


def test_generator_protocol_works_without_qt():
    """Demonstrates that the generator protocol is testable standalone."""

    def sample_phase_generator():
        for i in range(3):
            result: PhaseResult | None = yield PhaseRequest(
                kind=PhaseKind.UNATTENDED,
                phase_name="stub",
                dataset_index=i,
                dataset_total=3,
                dataset_name=f"DS{i}",
            )
            assert result is not None
            assert result.success is True

    gen = sample_phase_generator()
    # Drive it manually, no QObject involved.
    req = next(gen)
    assert req.dataset_index == 0
    req = gen.send(PhaseResult(success=True))
    assert req.dataset_index == 1
    req = gen.send(PhaseResult(success=True))
    assert req.dataset_index == 2
    with pytest.raises(StopIteration):
        gen.send(PhaseResult(success=True))
