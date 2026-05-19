"""U3 regression tests: persist_round_outputs flag + 3-arg on_complete.

The dilute-phase workflow needs ThresholdQCController to run the per-
round QC modal WITHOUT writing anything to the store or viewer — each
round's accepted mask is in-memory state owned by the orchestrating
controller, and only the final dilute mask gets persisted (via the
AcceptDiluteMask use case).

Two API extensions live in U3:

  - `persist_round_outputs: bool = True` — when False, _finalize skips
    /masks/<name>, /groups/<name>, /measurements, viewer.add_mask,
    refresh_resource_lists, and set_active_mask. The accepted mask
    travels back to the caller via on_complete instead.
  - `on_complete` evolves to a 3-arg signature
    `(success, msg, mask_array | None)` with backward-compat for the
    legacy 2-arg single-cell workflow consumer.

These tests pin both shapes against the real controller's _finalize
path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult


@pytest.fixture
def fake_viewer():
    class _Layers(list):
        pass
    viewer = MagicMock()
    viewer.layers = _Layers()
    viewer_win = MagicMock()
    viewer_win.viewer = viewer
    viewer_win.add_mask = MagicMock()
    viewer_win.show = MagicMock()
    return viewer_win


@pytest.fixture
def single_cell_grouping():
    return GroupingResult(
        group_assignments=pd.Series(
            data=np.array([1], dtype=int),
            index=pd.Index([1], name="label"),
            name="group",
        ),
        n_groups=1,
        group_means=[10.0],
    )


def _make_controller(
    fake_viewer,
    grouping_result,
    *,
    store=None,
    on_complete=None,
    write_measurements_to_store: bool | type = True,  # use type sentinel for "not passed"
    persist_round_outputs: bool = True,
    data_model=None,
):
    """Helper to spin up a controller without spamming kwargs in every
    test. Uses sigma=0 to skip smoothing (orthogonal to U3's surface)."""
    from percell4.gui.threshold_qc import ThresholdQCController

    H, W = 8, 8
    image = np.ones((H, W), dtype=np.float32) * 10.0
    seg_labels = np.zeros((H, W), dtype=np.int32)
    seg_labels[2:4, 2:4] = 1  # one cell

    if data_model is None:
        data_model = MagicMock()
        data_model.df = pd.DataFrame()
        data_model.session = MagicMock()

    kwargs = dict(
        viewer_win=fake_viewer,
        data_model=data_model,
        store=store,
        grouping_result=grouping_result,
        channel_image=image,
        seg_labels=seg_labels,
        channel="ch0",
        metric="mean_intensity",
        sigma=0.0,
        mask_name="round_1",
        on_complete=on_complete,
        persist_round_outputs=persist_round_outputs,
    )
    if write_measurements_to_store is not True:
        kwargs["write_measurements_to_store"] = write_measurements_to_store

    return ThresholdQCController(**kwargs)


# ── persist_round_outputs=False: full suppression ────────────────────


def test_no_persist_skips_all_store_writes(
    fake_viewer, single_cell_grouping
):
    """When persist_round_outputs=False, _finalize must not touch the
    store at all — no /masks, no /groups, no /measurements."""
    store = MagicMock()
    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, persist_round_outputs=False,
    )
    # Plant an accepted mask on the single group so _finalize has
    # something to union.
    controller._groups[0].mask = np.ones((8, 8), dtype=np.uint8)
    controller._groups[0].status = "accepted"  # GroupStatus.ACCEPTED

    controller._finalize()

    assert store.write_mask.call_count == 0, (
        "persist_round_outputs=False must skip /masks/<name>"
    )
    assert store.write_dataframe.call_count == 0, (
        "persist_round_outputs=False must skip /groups/<name> AND "
        "/measurements"
    )


def test_no_persist_skips_viewer_and_session(
    fake_viewer, single_cell_grouping
):
    """No add_mask on the viewer; no set_active_mask on the data
    model; no refresh_resource_lists on the session."""
    store = MagicMock()
    data_model = MagicMock()
    data_model.df = pd.DataFrame()
    data_model.session = MagicMock()

    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, persist_round_outputs=False, data_model=data_model,
    )
    controller._groups[0].mask = np.ones((8, 8), dtype=np.uint8)

    controller._finalize()

    assert fake_viewer.add_mask.call_count == 0
    assert data_model.set_active_mask.call_count == 0
    assert data_model.session.refresh_resource_lists.call_count == 0


def test_no_persist_calls_on_complete_with_mask(
    fake_viewer, single_cell_grouping
):
    """on_complete (3-arg) is called with the accepted union mask."""
    store = MagicMock()
    captured = []

    def cb(success, msg, mask):
        captured.append((success, msg, mask))

    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, persist_round_outputs=False, on_complete=cb,
    )
    accepted = np.ones((8, 8), dtype=np.uint8)
    controller._groups[0].mask = accepted

    controller._finalize()

    assert len(captured) == 1
    success, msg, mask = captured[0]
    assert success is True
    assert "Round complete" in msg or "Mask returned to caller" in msg
    np.testing.assert_array_equal(mask, accepted)


def test_no_persist_user_cancels_mask_is_none(
    fake_viewer, single_cell_grouping
):
    """Cancel path: on_complete called with success=False, mask=None."""
    captured = []

    def cb(success, msg, mask):
        captured.append((success, msg, mask))

    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        persist_round_outputs=False, on_complete=cb,
    )
    controller._on_cancel()

    assert len(captured) == 1
    success, msg, mask = captured[0]
    assert success is False
    assert mask is None


# ── persist_round_outputs=True (default): existing behavior ──────────


def test_default_persists_to_store(fake_viewer, single_cell_grouping):
    """Default behavior (no explicit flag): store.write_mask is called."""
    store = MagicMock()
    store.list_masks.return_value = ["round_1"]

    data_model = MagicMock()
    data_model.df = pd.DataFrame({"label": [1], "ch0_mean_intensity": [10.0]})
    data_model.session = MagicMock()

    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, data_model=data_model,
        # persist_round_outputs default is True; do not pass it
    )
    controller._groups[0].mask = np.ones((8, 8), dtype=np.uint8)

    controller._finalize()

    # /masks/<name> AND /groups/<name> AND /measurements all write.
    assert store.write_mask.call_count == 1
    args, kwargs = store.write_mask.call_args
    assert args[0] == "round_1"
    np.testing.assert_array_equal(args[1], np.ones((8, 8), dtype=np.uint8))
    # write_dataframe called twice: once for /groups, once for /measurements
    assert store.write_dataframe.call_count == 2


def test_default_calls_3arg_callback_with_mask(
    fake_viewer, single_cell_grouping
):
    """Even in default-persist mode, a 3-arg on_complete callback
    receives the accepted mask."""
    captured = []

    def cb(success, msg, mask):
        captured.append((success, msg, mask))

    store = MagicMock()
    store.list_masks.return_value = []
    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, on_complete=cb,
    )
    accepted = np.ones((8, 8), dtype=np.uint8)
    controller._groups[0].mask = accepted

    controller._finalize()

    assert len(captured) == 1
    success, msg, mask = captured[0]
    assert success is True
    np.testing.assert_array_equal(mask, accepted)


# ── Backward compat: legacy 2-arg callbacks ──────────────────────────


def test_legacy_2arg_callback_still_works_under_persist(
    fake_viewer, single_cell_grouping
):
    """The single-cell workflow's 2-arg on_complete (success, msg) must
    keep working — arity detection routes around the new mask arg."""
    captured = []

    def legacy_cb(success, msg):
        captured.append((success, msg))

    store = MagicMock()
    store.list_masks.return_value = []
    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, on_complete=legacy_cb,
    )
    controller._groups[0].mask = np.ones((8, 8), dtype=np.uint8)

    controller._finalize()

    assert len(captured) == 1
    success, msg = captured[0]
    assert success is True


def test_legacy_2arg_callback_still_works_under_no_persist(
    fake_viewer, single_cell_grouping
):
    """Legacy callback + no-persist mode: still no TypeError from
    extra mask arg. Defends the dispatch."""
    captured = []

    def legacy_cb(success, msg):
        captured.append((success, msg))

    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        persist_round_outputs=False, on_complete=legacy_cb,
    )
    controller._groups[0].mask = np.ones((8, 8), dtype=np.uint8)

    controller._finalize()

    assert len(captured) == 1
    success, msg = captured[0]
    assert success is True


# ── write_measurements_to_store legacy flag is unchanged ──────────


def test_legacy_flag_suppresses_measurements_only(
    fake_viewer, single_cell_grouping
):
    """The existing single-cell workflow uses write_measurements_to_store
    =False because it owns /measurements in its run folder, but it DOES
    expect /masks/<round> and /groups/<round> to land in the dataset's
    store. The legacy semantic must NOT regress."""
    store = MagicMock()
    store.list_masks.return_value = ["round_1"]
    data_model = MagicMock()
    data_model.df = pd.DataFrame({"label": [1], "ch0_mean_intensity": [10.0]})
    data_model.session = MagicMock()

    controller = _make_controller(
        fake_viewer, single_cell_grouping,
        store=store, data_model=data_model,
        write_measurements_to_store=False,
    )
    controller._groups[0].mask = np.ones((8, 8), dtype=np.uint8)

    controller._finalize()

    # /masks/<name> still writes.
    assert store.write_mask.call_count == 1
    # /groups/<name> still writes (one write_dataframe call).
    # /measurements does NOT write (the second write_dataframe call is gone).
    assert store.write_dataframe.call_count == 1
    written_path = store.write_dataframe.call_args[0][0]
    assert written_path == "/groups/round_1"
