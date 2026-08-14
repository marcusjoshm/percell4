"""Regression test: Compress button must react to single-checkbox toggles.

Bug: ``_ds_list.itemChanged`` and ``_ch_list.itemChanged`` were never
connected to ``_update_compress_button``. Clicking a single dataset
checkbox toggled its state but didn't refresh the Compress button's
enabled flag — so a user who deselected all, then ticked exactly one
dataset, saw Compress stay greyed out. The only working enable path
was Select All (which routes through ``_set_list_check_state`` and
calls the update slot manually).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile
from qtpy.QtCore import Qt


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    """Two TIFF subdirectories so discovery has multiple datasets to list."""
    src = tmp_path / "src"
    for ds_name in ("dataset_a", "dataset_b"):
        ds_dir = src / ds_name
        ds_dir.mkdir(parents=True)
        for ch in (0, 1):
            tifffile.imwrite(
                ds_dir / f"img_ch{ch}.tif",
                np.zeros((4, 4), dtype=np.uint16),
            )
    return src


def test_single_dataset_checkbox_toggle_enables_compress(
    qtbot, tmp_path: Path, source_tree: Path
) -> None:
    """Deselect all, then check exactly one dataset → Compress button enables."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    dlg._source_edit.setText(str(source_tree))
    dlg._output_edit.setText(str(tmp_path / "out"))
    dlg._run_discovery()

    assert dlg._ds_list.count() == 2  # sanity

    # Deselect All → Compress disables.
    dlg._on_deselect_all_datasets()
    assert not dlg._btn_compress.isEnabled()

    # User toggles exactly one dataset's checkbox.
    dlg._ds_list.item(0).setCheckState(Qt.Checked)

    # Compress must now be enabled — without the itemChanged connection
    # this assertion fails (the slot is never called).
    assert dlg._btn_compress.isEnabled(), (
        "Compress stayed disabled after checking one dataset — "
        "itemChanged signal is not wired to _update_compress_button"
    )


def test_single_channel_checkbox_toggle_enables_compress(
    qtbot, tmp_path: Path, source_tree: Path
) -> None:
    """Symmetric coverage: the channel list has the same bug pattern."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    dlg._source_edit.setText(str(source_tree))
    dlg._output_edit.setText(str(tmp_path / "out"))
    dlg._run_discovery()

    assert dlg._ch_list.count() >= 1  # sanity — at least one channel discovered

    dlg._on_deselect_all_channels()
    assert not dlg._btn_compress.isEnabled()

    dlg._ch_list.item(0).setCheckState(Qt.Checked)
    assert dlg._btn_compress.isEnabled(), (
        "Compress stayed disabled after checking one channel — "
        "itemChanged signal is not wired to _update_compress_button"
    )


def test_unchecking_last_dataset_disables_compress(
    qtbot, tmp_path: Path, source_tree: Path
) -> None:
    """The reverse path also needs the signal: unchecking the only checked
    dataset should disable Compress."""
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    dlg._source_edit.setText(str(source_tree))
    dlg._output_edit.setText(str(tmp_path / "out"))
    dlg._run_discovery()

    # Discovery defaults everything checked → Compress enabled.
    assert dlg._btn_compress.isEnabled()

    # Uncheck every dataset one at a time.
    for i in range(dlg._ds_list.count()):
        dlg._ds_list.item(i).setCheckState(Qt.Unchecked)

    assert not dlg._btn_compress.isEnabled(), (
        "Compress stayed enabled after every dataset was unchecked — "
        "itemChanged signal is not wired to _update_compress_button"
    )
