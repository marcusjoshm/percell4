"""Regression tests for the Compress dialog's Output field.

The bug being pinned: editing the Output field after Browse-Source ran
discovery once would silently fail to update the per-dataset output
paths, so .h5 files always landed where the auto-fill placed them
(``Path(source).parent``) regardless of what the user typed.

Root cause: ``_run_discovery`` baked ``output_path`` into each frozen
``DatasetSpec`` at discovery time. ``compress_config`` returned the
new Output text as ``output_dir`` but the stale ``datasets`` list with
the old baked-in paths. The runner uses ``ds.output_path``, not
``config.output_dir``.

Fix: rebuild ``DatasetSpec.output_path`` for every spec inside the
``compress_config`` materialization, so the property is the single
source of truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    """Create a source dir with two subdirs of TIFFs (subdirectory mode)."""
    import numpy as np
    import tifffile

    src = tmp_path / "src"
    for ds_name in ("dataset_a", "dataset_b"):
        ds_dir = src / ds_name
        ds_dir.mkdir(parents=True)
        for ch in (0, 1):
            arr = np.zeros((4, 4), dtype=np.uint16)
            tifffile.imwrite(ds_dir / f"img_ch{ch}.tif", arr)
    return src


def test_edited_output_field_updates_dataset_output_paths(
    qtbot, tmp_path: Path, source_tree: Path
) -> None:
    """Edit Output after discovery; compress_config must reflect the new path.

    Reproduces the bug: without the fix, every DatasetSpec.output_path
    keeps the auto-fill location (Path(source).parent) regardless of
    what the user typed in the Output field.
    """
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    # Simulate Browse-Source: sets source, auto-fills Output to parent,
    # runs discovery. We replicate the relevant calls directly to avoid
    # the file dialog.
    dlg._source_edit.setText(str(source_tree))
    dlg._output_edit.setText(str(source_tree.parent))  # auto-fill location
    dlg._run_discovery()

    # Sanity: discovery found the two subdirectories and baked the
    # auto-fill location into each spec.
    assert len(dlg._datasets) == 2
    for ds in dlg._datasets:
        assert ds.output_path.parent == source_tree.parent

    # User edits Output to where they actually want the .h5 files. No
    # signal fires (the Output field has no editingFinished/textChanged
    # connection), so the cached _datasets list is NOT regenerated.
    real_output = tmp_path / "real_output"
    real_output.mkdir()
    dlg._output_edit.setText(str(real_output))

    # Materialize the config the way main_window does at Accept time.
    cfg = dlg.compress_config

    # Every dataset's output_path must reflect the edited Output field,
    # not the stale auto-fill location. This is the load-bearing assertion.
    assert cfg.output_dir == real_output
    for ds in cfg.datasets:
        assert ds.output_path.parent == real_output, (
            f"DatasetSpec.output_path for {ds.name!r} is "
            f"{ds.output_path} — should be under {real_output}"
        )
        assert ds.output_path.name == f"{ds.name}.h5"


def test_empty_output_field_falls_back_to_baked_path(
    qtbot, tmp_path: Path, source_tree: Path
) -> None:
    """Clearing the Output field must not null out per-spec output paths.

    The fix only rewrites output_paths when Output has a value. Clearing
    it should leave the discovery-time baked path in place, so the user
    is never silently confronted with empty/None paths.
    """
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    dlg._source_edit.setText(str(source_tree))
    initial_output = tmp_path / "initial_output"
    initial_output.mkdir()
    dlg._output_edit.setText(str(initial_output))
    dlg._run_discovery()

    # Sanity: discovery used initial_output.
    for ds in dlg._datasets:
        assert ds.output_path.parent == initial_output

    # User clears the Output field.
    dlg._output_edit.setText("")

    cfg = dlg.compress_config

    # output_dir on the config is None.
    assert cfg.output_dir is None
    # But the per-spec output_paths still point somewhere — at the
    # discovery-time location, not None or empty.
    for ds in cfg.datasets:
        assert ds.output_path is not None
        assert ds.output_path.parent == initial_output


def test_unchanged_output_field_leaves_discovery_paths_alone(
    qtbot, tmp_path: Path, source_tree: Path
) -> None:
    """When the user doesn't touch Output, paths from discovery survive.

    Guards against a fix that over-eagerly rewrites paths and breaks the
    common case where the user accepts the auto-filled value.
    """
    from percell4.gui.compress_dialog import CompressDialog

    dlg = CompressDialog()
    qtbot.addWidget(dlg)

    dlg._source_edit.setText(str(source_tree))
    auto_filled = tmp_path / "auto_filled"
    auto_filled.mkdir()
    dlg._output_edit.setText(str(auto_filled))
    dlg._run_discovery()

    cfg = dlg.compress_config

    assert cfg.output_dir == auto_filled
    for ds in cfg.datasets:
        assert ds.output_path.parent == auto_filled
        assert ds.output_path.name == f"{ds.name}.h5"
