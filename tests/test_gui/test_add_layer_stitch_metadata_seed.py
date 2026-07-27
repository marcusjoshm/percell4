"""Tests for seeding AddLayerDialog's TCSPC stitch controls from /metadata (U2).

This path had NO coverage before this refactor, which is why it warranted its
own file. It is the sharpest edge in the consolidation: the seeding used to
locate combo entries with ``findText`` and now uses ``findData``, and a miss is
**silent** — ``idx < 0`` simply skips ``setCurrentIndex``, leaving the default
selected with no error. Because that seeded geometry places decay tiles relative
to already-stitched intensity, a silent fallback misaligns ``/decay`` against
``/intensity`` — the failure class
``docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md``
exists to prevent.

Every assertion here therefore checks the **resulting TileConfig**, never a
combo index.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.store import DatasetStore


def _store_with_stitch_metadata(path, **stitch):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch00", "ch01"], **stitch})
    store.write_array(
        "intensity",
        np.zeros((2, 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    return store


def _dialog(qtbot, store):
    from percell4.gui.add_layer_dialog import AddLayerDialog

    dlg = AddLayerDialog(parent=None, store=store, data_model=None, viewer_win=None)
    qtbot.addWidget(dlg)
    return dlg


def _seeded_tile_config(dlg):
    """Read back what the seeded controls would hand to the domain."""
    from percell4.domain.io.models import TileConfig

    return TileConfig(
        grid_rows=dlg._tcspc_stitch_rows.value(),
        grid_cols=dlg._tcspc_stitch_cols.value(),
        grid_type=dlg._tcspc_stitch_type.currentData(),
        order=dlg._tcspc_stitch_order.currentData(),
    )


@pytest.mark.parametrize(
    "stored_order",
    [
        "top_left", "top_right", "bottom_left", "bottom_right",
        "right_down", "right_up", "left_down", "left_up",
    ],
)
def test_every_persisted_order_seeds_the_matching_control(
    qtbot, tmp_path, stored_order: str
) -> None:
    """All eight accepted values live in real .h5 files on disk.

    Whichever vocabulary a dataset was written with must round-trip.
    """
    store = _store_with_stitch_metadata(
        tmp_path / f"{stored_order}.h5",
        stitch_grid_rows=3,
        stitch_grid_cols=4,
        stitch_grid_type="snake_by_row",
        stitch_order=stored_order,
    )
    dlg = _dialog(qtbot, store)
    dlg._tcspc_seed_stitching_from_metadata()

    tc = _seeded_tile_config(dlg)
    assert tc.grid_rows == 3
    assert tc.grid_cols == 4
    assert tc.grid_type == "snake_by_row"
    assert tc.order == stored_order


def test_seeded_grid_is_not_transposed(qtbot, tmp_path) -> None:
    """rows→grid_rows, cols→grid_cols. A swap is invisible on square grids and
    changes native_shape on the registered path."""
    store = _store_with_stitch_metadata(
        tmp_path / "nonsquare.h5",
        stitch_grid_rows=2,
        stitch_grid_cols=5,
        stitch_grid_type="row_by_row",
        stitch_order="top_left",
    )
    dlg = _dialog(qtbot, store)
    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_rows.value() == 2
    assert dlg._tcspc_stitch_cols.value() == 5


def test_unrecognized_order_leaves_default_and_does_not_raise(qtbot, tmp_path) -> None:
    """A junk value must not crash the Scan flow, and must not silently pick
    something arbitrary — the default stands."""
    store = _store_with_stitch_metadata(
        tmp_path / "junk.h5",
        stitch_grid_rows=2,
        stitch_grid_cols=2,
        stitch_grid_type="row_by_row",
        stitch_order="sideways",
    )
    dlg = _dialog(qtbot, store)
    default_order = dlg._tcspc_stitch_order.currentData()

    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_order.currentData() == default_order


def test_single_tile_grid_does_not_enable_stitching(qtbot, tmp_path) -> None:
    store = _store_with_stitch_metadata(
        tmp_path / "single.h5",
        stitch_grid_rows=1,
        stitch_grid_cols=1,
        stitch_grid_type="row_by_row",
        stitch_order="top_left",
    )
    dlg = _dialog(qtbot, store)
    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_check.isChecked() is False


def test_absent_stitch_metadata_is_a_no_op(qtbot, tmp_path) -> None:
    """Datasets imported without stitching carry no stitch_* attrs."""
    store = _store_with_stitch_metadata(tmp_path / "none.h5")
    dlg = _dialog(qtbot, store)
    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_check.isChecked() is False


def test_seeding_does_not_mark_the_form_user_edited(qtbot, tmp_path) -> None:
    """Seeding is programmatic. If it tripped the flag, a later re-Scan would
    wrongly believe the user had made a choice and refuse to re-seed."""
    store = _store_with_stitch_metadata(
        tmp_path / "flag.h5",
        stitch_grid_rows=2,
        stitch_grid_cols=3,
        stitch_grid_type="row_by_row",
        stitch_order="top_left",
    )
    dlg = _dialog(qtbot, store)
    assert dlg._tcspc_stitching_user_edited is False

    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitching_user_edited is False


def test_user_edit_suppresses_reseeding(qtbot, tmp_path) -> None:
    """LASX .bin scan order is independent of how the TIFF was stitched, so a
    user choice must survive a re-Scan."""
    store = _store_with_stitch_metadata(
        tmp_path / "edited.h5",
        stitch_grid_rows=2,
        stitch_grid_cols=3,
        stitch_grid_type="row_by_row",
        stitch_order="top_left",
    )
    dlg = _dialog(qtbot, store)
    dlg._tcspc_stitching_user_edited = True
    dlg._tcspc_stitch_rows.setValue(7)

    dlg._tcspc_seed_stitching_from_metadata()

    assert dlg._tcspc_stitch_rows.value() == 7
