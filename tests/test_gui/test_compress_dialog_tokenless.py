"""Tests for the Compress dialog's Tokenless (by name) discovery mode."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from percell4.domain.io.models import LayerType

_PREFIXES = [
    "CellProfiler_U2OS_60min_As_3x4",
    "CellProfiler_U2OS_90min_Washout_2",
    "CellProfiler_U2OS_90min_Washout_4x4",
]
_CHANNELS = ["cells", "DNA", "G3BP1", "SG_mask"]
_TOKENLESS_INDEX = 2


@pytest.fixture
def flat_named_dir(tmp_path: Path) -> Path:
    """Flat folder: 3 datasets x 4 name-suffixed channels (incl. SG_mask)."""
    src = tmp_path / "src"
    src.mkdir()
    for p in _PREFIXES:
        for c in _CHANNELS:
            tifffile.imwrite(src / f"{p}_{c}.tif", np.zeros((4, 4), dtype=np.uint16))
    return src


def _open_tokenless(qtbot, source: Path):
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)
    dlg._source_edit.setText(str(source))
    dlg._discovery_combo.setCurrentIndex(_TOKENLESS_INDEX)  # triggers discovery
    return dlg


def test_tokenless_discovers_named_datasets_and_channels(qtbot, flat_named_dir):
    dlg = _open_tokenless(qtbot, flat_named_dir)
    assert {ds.name for ds in dlg._datasets} == set(_PREFIXES)
    assert set(dlg._all_channels) == {"cells", "DNA", "G3BP1", "SG_mask"}


def test_tokenless_channel_labels_are_verbatim(qtbot, flat_named_dir):
    """Auto-mode channel list shows the names, never a 'ch' prefix."""
    dlg = _open_tokenless(qtbot, flat_named_dir)
    labels = {dlg._ch_list.item(i).text() for i in range(dlg._ch_list.count())}
    assert labels == {"cells", "DNA", "G3BP1", "SG_mask"}


def test_tokenless_config_threads_synthesized_regex(qtbot, flat_named_dir):
    dlg = _open_tokenless(qtbot, flat_named_dir)
    cfg = dlg.compress_config
    tc = cfg.token_config
    # A name-token regex, not the numeric default; other tokens disabled.
    assert tc.channel is not None and tc.channel != r"_ch(\d+)"
    assert tc.timepoint is None and tc.z_slice is None and tc.tile is None
    import re

    m = re.search(tc.channel, "foo_bar_SG_mask")
    assert m is not None and m.group(1) == "SG_mask"
    # Datasets stay scoped: each carries only its own 4 files.
    assert len(cfg.datasets) == 3
    assert all(len(ds.files) == 4 for ds in cfg.datasets)


def test_tokenless_manual_rename_and_type_assignment(qtbot, flat_named_dir):
    dlg = _open_tokenless(qtbot, flat_named_dir)
    dlg._manual_radio.setChecked(True)

    # Configs are keyed by the derived name token.
    assert set(dlg._channel_configs) == {"cells", "DNA", "G3BP1", "SG_mask"}
    dlg._channel_configs["DNA"].name_edit.setText("GFP")
    dlg._channel_configs["SG_mask"].type_combo.setCurrentText("Mask")
    dlg._channel_configs["cells"].type_combo.setCurrentText("Segmentation")

    cfg = dlg.compress_config
    la = cfg.layer_assignments
    assert la["DNA"].name == "GFP"
    assert la["SG_mask"].layer_type == LayerType.MASK
    assert la["cells"].layer_type == LayerType.SEGMENTATION
    assert la["G3BP1"].layer_type == LayerType.CHANNEL


def test_tokenless_hides_token_regex_group(qtbot, flat_named_dir):
    # isVisibleTo(dlg) reports intended visibility without show()-ing the dialog.
    dlg = _open_tokenless(qtbot, flat_named_dir)
    assert not dlg._token_group.isVisibleTo(dlg)
    # Switching back to Flat Directory restores the regex group + config.
    dlg._discovery_combo.setCurrentIndex(1)
    assert dlg._token_group.isVisibleTo(dlg)
    assert dlg._current_token_config().channel == r"_ch(\d+)"


def test_tokenless_empty_folder_surfaces_message(qtbot, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    dlg = _open_tokenless(qtbot, empty)
    assert dlg._datasets == []
    assert "No name-suffixed TIFFs" in dlg._ds_count_label.text()
