"""U1: empty-labels QC recovery — auto-skip removed.

Previously the controller auto-completed with success when
``labels.max() == 0``, which (combined with the runner's
``datasets_without_failures`` filter) gave the user no opportunity to
draw labels manually. After U1, an empty labels layer enters the
normal load + show path so the existing draw / brush tools can act on
it. The runner's success/failure accounting is unchanged: only the
QC window's behaviour shifts.

Covers AE1 from
``docs/brainstorms/2026-05-26-seg-qc-recovery-options-requirements.md``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.gui.viewer import ViewerWindow
from percell4.gui.workflows.single_cell.seg_qc import SegmentationQCController
from percell4.model import CellDataModel
from percell4.store import DatasetStore
from percell4.workflows.models import DatasetSource, WorkflowDatasetEntry

# Builds a real napari viewer, so this module carries the ``napari_viewer``
# marker: skipped by default (see pyproject addopts), run explicitly on CI.
pytestmark = pytest.mark.napari_viewer


def _make_dataset(path: Path, *, label_cells: int) -> None:
    """Tiny .h5 fixture: 16×16 single-channel intensity + cellpose_qc labels.

    ``label_cells == 0`` writes an all-zeros labels array (the
    empty-Cellpose case we're recovering). Any positive value writes
    that many labelled cells as 4×4 squares.
    """
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.ones((16, 16), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    labels = np.zeros((16, 16), dtype=np.int32)
    for i in range(label_cells):
        r = 2 + (i // 3) * 5
        c = 2 + (i % 3) * 5
        labels[r:r + 3, c:c + 3] = i + 1
    store.write_labels("cellpose_qc", labels)


@pytest.fixture
def viewer_win():
    win = ViewerWindow(CellDataModel(session=Session()))
    yield win
    try:
        win.viewer.close()
    except Exception:
        pass


def _controller(path: Path, viewer_win, completions: list):
    entry = WorkflowDatasetEntry(
        name="ds_empty",
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["GFP"],
    )
    return SegmentationQCController(
        viewer_win=viewer_win,
        entry=entry,
        queue_index=0,
        queue_total=1,
        on_complete=lambda result: completions.append(result),
        channel_idx=0,
        seg_name="cellpose_qc",
    )


def test_empty_labels_opens_qc_window_instead_of_auto_skipping(
    tmp_path, viewer_win
):
    """AE1: empty Cellpose result -> QC window opens for hand-drawing."""
    path = tmp_path / "empty.h5"
    _make_dataset(path, label_cells=0)
    completions: list = []
    ctrl = _controller(path, viewer_win, completions)

    ctrl.start()

    # Window is up, labels (empty) are loaded into napari, and the
    # phase has NOT auto-completed.
    assert ctrl._window is not None
    assert ctrl._window.isVisible()
    assert ctrl._labels is not None
    assert int(ctrl._labels.max()) == 0
    assert completions == []  # no auto-accept

    from percell4.gui.workflows.base_runner import PhaseResult
    ctrl._finish(PhaseResult(success=True, message="test teardown"))


def test_non_empty_labels_still_opens_qc_window(tmp_path, viewer_win):
    """Regression guard: the non-empty path is unchanged by U1."""
    path = tmp_path / "with_cells.h5"
    _make_dataset(path, label_cells=3)
    completions: list = []
    ctrl = _controller(path, viewer_win, completions)

    ctrl.start()

    assert ctrl._window is not None
    assert ctrl._window.isVisible()
    assert int(ctrl._labels.max()) == 3
    assert completions == []

    from percell4.gui.workflows.base_runner import PhaseResult
    ctrl._finish(PhaseResult(success=True, message="test teardown"))


def test_empty_labels_path_records_no_failure(tmp_path, viewer_win):
    """The controller does not push any failure record on empty-labels entry.

    The runner's RunMetadata is owned by the runner, not the controller,
    so this is a smoke check that start()'s empty-labels branch is a
    pure load+show path with no side effect on phase outcomes. Failure
    bookkeeping only happens on Cancel; Accept persists labels.
    """
    path = tmp_path / "empty2.h5"
    _make_dataset(path, label_cells=0)
    completions: list = []
    ctrl = _controller(path, viewer_win, completions)

    ctrl.start()

    # No completion record means no failure was emitted to the runner.
    assert completions == []
    from percell4.gui.workflows.base_runner import PhaseResult
    ctrl._finish(PhaseResult(success=True, message="test teardown"))


def test_missing_labels_on_disk_finishes_with_failure(tmp_path, viewer_win):
    """When /labels/<seg_name> is genuinely missing (not just empty),
    the controller short-circuits with a failure — distinct from the
    empty-labels recovery path."""
    path = tmp_path / "no_labels.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.ones((16, 16), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    completions: list = []
    ctrl = _controller(path, viewer_win, completions)

    ctrl.start()

    assert len(completions) == 1
    assert completions[0].success is False
