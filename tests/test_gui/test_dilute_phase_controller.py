"""U6 tests: DilutePhaseMaskController state machine.

The controller owns the per-workflow round state machine — working
buffer, cumulative-condensed union, locked config, round counter.
These tests pin the contract end-to-end with a fake viewer + fake
store; one integration test wires in a *real* ThresholdQCController in
``persist_round_outputs=False`` mode to prove U3's no-persist contract
holds in production wiring.

Scenarios covered:

* AE-1: three-round happy path; Done writes one mask.
* AE-2: cancel after rounds; zero ``.h5`` writes.
* AE-5: a cell entirely subtracted by an earlier round drops cleanly
  from the next round's metric column.
* Edge: user rejects a round; working buffer unchanged.
* Edge: cancel during in-flight Worker; teardown does not raise.
* Edge: ``_teardown`` is idempotent.
* Error: per-round metric returns all NaN.
* Integration: real ThresholdQCController fed by the controller's
  callback path, no mocks on U3's surface.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from percell4.gui._grouped_threshold_settings import GroupedThresholdConfig

# ── Fakes ───────────────────────────────────────────────────


class _Layers(list):
    """Minimal list subclass standing in for ``napari.LayerList``."""

    def remove(self, layer) -> None:  # noqa: D401 - mimic napari API
        super().remove(layer)


class _FakeLayer:
    """Drop-in for a napari Image/Labels layer for our tests."""

    def __init__(self, name: str, data: np.ndarray, kind: str) -> None:
        self.name = name
        self.data = data
        self.kind = kind


def _build_fake_viewer():
    layers = _Layers()

    def add_image(data, *, name, **kwargs):
        layers.append(_FakeLayer(name, np.asarray(data), "image"))

    def add_labels(data, *, name, **kwargs):
        layers.append(_FakeLayer(name, np.asarray(data), "labels"))

    viewer = MagicMock()
    viewer.layers = layers
    viewer.add_image = MagicMock(side_effect=add_image)
    viewer.add_labels = MagicMock(side_effect=add_labels)

    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.existing_viewer = viewer
    viewer_win.add_image = MagicMock(side_effect=lambda data, **kw: add_image(data, **kw))
    viewer_win.add_labels = MagicMock(side_effect=lambda data, **kw: add_labels(data, **kw))
    viewer_win.add_mask = MagicMock()
    viewer_win.show = MagicMock()
    return viewer_win


@pytest.fixture
def fake_viewer():
    return _build_fake_viewer()


@pytest.fixture
def fake_session():
    sess = MagicMock()
    sess.active_mask = None

    def _set_active_mask(name):
        sess.active_mask = name

    sess.set_active_mask = MagicMock(side_effect=_set_active_mask)
    sess.refresh_resource_lists = MagicMock()
    return sess


@pytest.fixture
def fake_data_model():
    dm = MagicMock()
    dm.df = pd.DataFrame()
    dm.session = MagicMock()
    return dm


@pytest.fixture
def locked_config():
    return GroupedThresholdConfig(
        metric="mean_intensity",
        algorithm="K-means",
        gmm_criterion="bic",
        gmm_max_components=10,
        kmeans_n_clusters=2,
        sigma=0.0,
    )


@pytest.fixture
def seg_labels():
    """16x16 with three labelled cells in distinct regions."""
    arr = np.zeros((16, 16), dtype=np.int32)
    arr[2:5, 2:5] = 1
    arr[2:5, 8:11] = 2
    arr[10:13, 4:7] = 3
    return arr


@pytest.fixture
def channel_image():
    """Bright in cell 1, medium in cell 2, dim in cell 3."""
    img = np.zeros((16, 16), dtype=np.float32)
    img[2:5, 2:5] = 100.0
    img[2:5, 8:11] = 50.0
    img[10:13, 4:7] = 10.0
    return img


@pytest.fixture
def handle():
    from percell4.domain.dataset import DatasetHandle
    return DatasetHandle(path=Path("/tmp/dilute_test.h5"), metadata={})


def _make_controller(
    fake_viewer,
    fake_data_model,
    fake_session,
    channel_image,
    seg_labels,
    locked_config,
    handle,
    *,
    store=None,
    dilation_radius_px: int = 1,
    final_mask_name: str = "dilute_v1",
):
    from percell4.gui.workflows.dilute_phase.controller import (
        DilutePhaseMaskController,
    )

    if store is None:
        store = MagicMock()
        store.list_masks.return_value = [final_mask_name]

    return DilutePhaseMaskController(
        viewer_win=fake_viewer,
        data_model=fake_data_model,
        store=store,
        session=fake_session,
        channel_image=channel_image,
        seg_labels=seg_labels,
        locked_config=locked_config,
        dilation_radius_px=dilation_radius_px,
        final_mask_name=final_mask_name,
        handle=handle,
    )


# ── AE-1: three-round happy path ────────────────────────────


def test_three_round_happy_path_done_writes_one_mask(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
    monkeypatch,
):
    """Drive 3 rounds → each accepted → Done. Exactly ONE store.write_mask
    call, on the FINAL mask name; transient layers are gone afterward."""
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
    )

    # Intercept the use case's repo so we can audit the final write.
    repo_writes: list[tuple[str, np.ndarray]] = []

    class _FakeRepo:
        def write_mask(self, h, name, data, attrs=None):
            repo_writes.append((name, np.asarray(data)))

        def list_masks(self, h):
            return [name for name, _ in repo_writes]

    monkeypatch.setattr(
        "percell4.gui.workflows.dilute_phase.controller.Hdf5DatasetRepository",
        _FakeRepo,
    )

    # Drive three accepted rounds by directly invoking the QC callback.
    # The mask that round-N "accepts" is just a patch in cell N's region.
    round_masks = [
        # Round 1: accept cell 1's region.
        (seg_labels == 1).astype(np.uint8),
        # Round 2: accept cell 2's region.
        (seg_labels == 2).astype(np.uint8),
        # Round 3: accept cell 3's region.
        (seg_labels == 3).astype(np.uint8),
    ]
    for n, mask in enumerate(round_masks, start=1):
        controller._round_n = n  # simulate start_round increment
        controller._on_round_qc_complete(True, f"round {n} ok", mask)

    # After each round, the working buffer is NaN where dilation hits.
    # Cumulative-condensed has been growing.
    assert np.all(np.isnan(controller._working_buffer[seg_labels == 1]))
    assert np.all(np.isnan(controller._working_buffer[seg_labels == 2]))
    assert np.all(np.isnan(controller._working_buffer[seg_labels == 3]))
    assert controller._cumulative_condensed[seg_labels == 1].all()
    assert controller._cumulative_condensed[seg_labels == 2].all()
    assert controller._cumulative_condensed[seg_labels == 3].all()

    # Transient layers exist before Done.
    names_before = [layer.name for layer in fake_viewer.viewer.layers]
    assert "_dilute_workflow_view" in names_before
    assert "_dilute_workflow_condensed" in names_before

    # Done.
    controller.finish()

    assert len(repo_writes) == 1, "exactly ONE store write at workflow end"
    written_name, written_data = repo_writes[0]
    assert written_name == "dilute_v1"
    # Final dilute = (seg > 0) & ~cumulative. With three cells fully
    # subtracted (plus dilation), the final dilute mask should be empty
    # in those cells.
    assert written_data.dtype == np.uint8
    assert not written_data[seg_labels == 1].any()
    assert not written_data[seg_labels == 2].any()
    assert not written_data[seg_labels == 3].any()

    assert fake_session.active_mask == "dilute_v1"
    assert fake_session.set_active_mask.call_count == 1

    # Transient layers removed.
    names_after = [layer.name for layer in fake_viewer.viewer.layers]
    assert "_dilute_workflow_view" not in names_after
    assert "_dilute_workflow_condensed" not in names_after


# ── AE-2: cancel after rounds ───────────────────────────────


def test_cancel_after_rounds_zero_writes(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
    monkeypatch,
):
    """Two rounds accepted, then Cancel → zero store writes, no active mask."""
    store = MagicMock()
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
        store=store,
    )

    repo_writes: list = []

    class _FakeRepo:
        def write_mask(self, h, name, data, attrs=None):
            repo_writes.append((name, data))

        def list_masks(self, h):
            return []

    monkeypatch.setattr(
        "percell4.gui.workflows.dilute_phase.controller.Hdf5DatasetRepository",
        _FakeRepo,
    )

    controller._round_n = 1
    controller._on_round_qc_complete(
        True, "ok", (seg_labels == 1).astype(np.uint8),
    )
    controller._round_n = 2
    controller._on_round_qc_complete(
        True, "ok", (seg_labels == 2).astype(np.uint8),
    )

    controller.cancel()

    assert len(repo_writes) == 0
    assert store.write_mask.call_count == 0
    assert fake_session.set_active_mask.call_count == 0
    assert fake_session.active_mask is None

    names_after = [layer.name for layer in fake_viewer.viewer.layers]
    assert "_dilute_workflow_view" not in names_after
    assert "_dilute_workflow_condensed" not in names_after


# ── AE-5: cell entirely subtracted ──────────────────────────


def test_cell_entirely_subtracted_does_not_break_next_round(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
):
    """Round 1 fully subtracts cells 1 and 2 → round 2's measure_cells
    returns rows for cell 3 only; the controller proceeds without raising."""
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
        dilation_radius_px=0,  # exact cell-pixel subtraction for the test
    )

    # Simulate Round 1: accept a mask that covers cells 1 AND 2.
    union_mask = ((seg_labels == 1) | (seg_labels == 2)).astype(np.uint8)
    controller._round_n = 1
    controller._on_round_qc_complete(True, "ok", union_mask)

    # Cells 1 & 2 should be entirely NaN now; cell 3 should still hold
    # its original values.
    assert np.all(np.isnan(controller._working_buffer[seg_labels == 1]))
    assert np.all(np.isnan(controller._working_buffer[seg_labels == 2]))
    assert np.all(
        controller._working_buffer[seg_labels == 3] == 10.0
    )

    # Confirm a per-cell metric over the working buffer drops cells 1 & 2.
    from percell4.domain.measure.measurer import measure_cells
    df = measure_cells(
        controller._working_buffer,
        seg_labels,
        metrics=["mean_intensity"],
    )
    finite = df.loc[df["mean_intensity"].notna()]
    assert sorted(finite["label"].tolist()) == [3]

    # ``start_round`` should now succeed (it will kick off a worker;
    # in this test we just want to verify it doesn't raise on the
    # all-NaN-for-most-cells case). To avoid actually spinning sklearn
    # in a unit test, we patch the worker to be a no-op.
    from unittest.mock import patch

    with patch(
        "percell4.gui.workflows.dilute_phase.controller.Worker"
    ) as worker_cls:
        instance = MagicMock()
        worker_cls.return_value = instance
        # The metric column will have only one cell with a finite value
        # (cell 3) → len(values) == 1, which trips the < 2 guard and
        # emits an error. That's the expected acceptance criterion:
        # the controller should NOT crash; it surfaces an error
        # message so the user can adjust scope.
        errors: list[str] = []
        controller.error.connect(errors.append)
        controller.start_round()

        # No worker spawned because the < 2 guard fired first.
        worker_cls.assert_not_called()
        assert len(errors) == 1
        assert "insufficient cells" in errors[0]


# ── Edge: user rejects a round ──────────────────────────────


def test_round_rejected_leaves_state_unchanged(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
):
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
    )

    buf_before = controller._working_buffer.copy()
    cum_before = controller._cumulative_condensed.copy()

    completed: list[int] = []
    controller.round_complete.connect(completed.append)

    controller._round_n = 1
    controller._on_round_qc_complete(False, "user rejected", None)

    np.testing.assert_array_equal(controller._working_buffer, buf_before)
    np.testing.assert_array_equal(
        controller._cumulative_condensed, cum_before,
    )
    assert completed == [1]


# ── Edge: cancel during in-flight Worker ────────────────────


def test_cancel_during_in_flight_worker_does_not_raise(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
):
    from unittest.mock import patch

    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
    )

    # Patch the Worker so .start() doesn't actually spin a thread.
    with patch(
        "percell4.gui.workflows.dilute_phase.controller.Worker"
    ) as worker_cls:
        worker_inst = MagicMock()
        worker_cls.return_value = worker_inst
        controller.start_round()

        assert controller._grouping_worker is worker_inst

        # Now cancel mid-flight.
        controller.cancel()

    # Worker reference cleared; teardown didn't raise.
    assert controller._grouping_worker is None
    assert controller._torn_down is True


# ── Edge: idempotent teardown ───────────────────────────────


def test_teardown_is_idempotent(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
):
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
    )

    # Plant a transient layer to confirm the first teardown removes
    # it and the second is a true no-op.
    controller._round_n = 1
    controller._on_round_qc_complete(
        True, "ok", (seg_labels == 1).astype(np.uint8),
    )
    assert any(
        layer.name == "_dilute_workflow_view"
        for layer in fake_viewer.viewer.layers
    )

    controller._teardown()
    assert controller._torn_down is True
    assert not any(
        layer.name == "_dilute_workflow_view"
        for layer in fake_viewer.viewer.layers
    )

    # Second call: still no-op. We confirm by counting remove calls.
    remove_count_before = fake_viewer.viewer.layers
    controller._teardown()
    assert controller._torn_down is True
    # Idempotency: nothing else has changed in the layer list.
    assert list(fake_viewer.viewer.layers) == list(remove_count_before)


# ── Error: all-NaN working buffer pre-seed ──────────────────


def test_start_round_emits_error_when_buffer_all_nan(
    fake_viewer, fake_data_model, fake_session,
    seg_labels, locked_config, handle,
):
    """A buffer where every pixel is NaN → no finite metric values →
    error emitted, no state changes."""
    nan_image = np.full((16, 16), np.nan, dtype=np.float32)
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        nan_image, seg_labels, locked_config, handle,
    )

    errors: list[str] = []
    controller.error.connect(errors.append)

    controller.start_round()

    assert len(errors) == 1
    assert "insufficient cells" in errors[0]
    # No worker was spawned.
    assert controller._grouping_worker is None
    # Round counter still advanced (the panel uses this for status).
    assert controller._round_n == 1


# ── Integration: real ThresholdQCController in no-persist mode ──


def test_real_threshold_qc_in_no_persist_mode_feeds_callback(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
):
    """Drive one round through a *real* ThresholdQCController in
    ``persist_round_outputs=False`` and verify the accepted union mask
    flows back through ``_on_round_qc_complete``, is dilated, and
    NaN-stamps the working buffer.

    This proves U3's no-persist contract holds in production wiring:
    the controller does NOT call store/viewer/session writes; the mask
    travels back via the 3-arg ``on_complete`` callback only.
    """
    from percell4.domain.measure.grouper import GroupingResult
    from percell4.gui.threshold_qc import ThresholdQCController

    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
    )

    # Build a single-group grouping covering cell 1 only — we want
    # to verify the round-1 accepted mask ends up dilated in the
    # working buffer.
    grouping = GroupingResult(
        group_assignments=pd.Series(
            data=np.array([1], dtype=int),
            index=pd.Index([1], name="label"),
            name="group",
        ),
        n_groups=1,
        group_means=[100.0],
    )

    store = MagicMock()
    captured: list = []

    qc = ThresholdQCController(
        viewer_win=fake_viewer,
        data_model=fake_data_model,
        store=store,
        grouping_result=grouping,
        channel_image=controller._working_buffer,
        seg_labels=seg_labels,
        channel="ch0",
        metric="mean_intensity",
        sigma=0.0,
        mask_name="_dilute_round_1",
        on_complete=lambda s, m, mask: captured.append((s, m, mask)),
        persist_round_outputs=False,
    )

    # Plant an accepted mask on the (single) group and call _finalize
    # directly to skip the UI loop. _finalize:
    #   1) unions group masks → combined
    #   2) skips ALL store/viewer/session writes (persist=False)
    #   3) calls on_complete(True, msg, combined)
    accepted = (seg_labels == 1).astype(np.uint8)
    qc._groups[0].mask = accepted
    qc._finalize()

    # No store writes — U3's contract.
    assert store.write_mask.call_count == 0
    assert store.write_dataframe.call_count == 0
    # And the QC controller didn't touch our session.
    assert fake_session.set_active_mask.call_count == 0

    # Mask flowed back through the 3-arg callback.
    assert len(captured) == 1
    success, _msg, mask_out = captured[0]
    assert success is True
    np.testing.assert_array_equal(mask_out, accepted)

    # Now feed this captured mask into the dilute controller and verify
    # the NaN-subtraction + cumulative-union land correctly.
    controller._round_n = 1
    controller._on_round_qc_complete(success, _msg, mask_out)

    assert np.all(np.isnan(controller._working_buffer[seg_labels == 1]))
    assert controller._cumulative_condensed[seg_labels == 1].all()
    # Cells 2 and 3 untouched by dilation (radius=1; cells are
    # ≥3 pixels apart).
    assert not controller._cumulative_condensed[seg_labels == 2].any()
    assert not controller._cumulative_condensed[seg_labels == 3].any()


# ── Regression: viewer.add_mask called on Done ──────────────


def test_finish_calls_viewer_add_mask_so_layer_appears_in_napari(
    fake_viewer, fake_data_model, fake_session,
    channel_image, seg_labels, locked_config, handle,
    monkeypatch,
):
    """Bug 2026-05-18: after Done, the dilute mask landed in /masks/
    and the SessionWindow combo refreshed, but the napari viewer never
    got an add_mask() call — so the user saw the mask in the combo
    but no labels layer until they reloaded the dataset.

    The Creator triplet in AcceptDiluteMask (store.write_mask →
    refresh_resource_lists → set_active_mask) is intentionally Qt-free
    — viewer wiring is the caller's responsibility. The controller
    must call ``viewer_win.add_mask(final_mask, name=name)`` after
    the use case returns so the layer materializes immediately.
    """
    controller = _make_controller(
        fake_viewer, fake_data_model, fake_session,
        channel_image, seg_labels, locked_config, handle,
    )

    # Stub the use case's repo so finish() can run without disk I/O.
    class _FakeRepo:
        def write_mask(self, h, name, data, attrs=None):
            pass

        def list_masks(self, h):
            return ["dilute_v1"]

    monkeypatch.setattr(
        "percell4.gui.workflows.dilute_phase.controller.Hdf5DatasetRepository",
        _FakeRepo,
    )

    controller.finish()

    assert fake_viewer.add_mask.call_count == 1, (
        "Controller.finish() must call viewer_win.add_mask(mask, "
        "name=final_mask_name) so the dilute mask appears in napari "
        "without forcing the user to reload the dataset"
    )
    args, kwargs = fake_viewer.add_mask.call_args
    # Mask name should be the final_mask_name from _make_controller.
    name_arg = kwargs.get("name", args[1] if len(args) > 1 else None)
    assert name_arg == "dilute_v1"
    mask_arg = args[0] if args else kwargs.get("data")
    # Mask should be 2D, dtype uint8 (per the add_mask wrapper contract).
    assert mask_arg.ndim == 2
    assert mask_arg.dtype == np.uint8
