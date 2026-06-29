"""Tests for :class:`DiluteFromMaskDialog` (U3).

Drives real user-edit signals (``qtbot.keyClicks``) where the goal is to
verify wiring; programmatic ``setText``/``setValue`` is used only when the
test asserts a downstream effect of a known value. See
``docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md``.

Run-loop tests inject a stub orchestrator (the ``orchestrator=`` ctor seam)
that fires the progress callback synchronously, so the loop completes
without needing real HDF5 fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import h5py
import numpy as np
import pytest
from qtpy.QtWidgets import QLabel, QMessageBox

from percell4.application.use_cases.batch_dilute_from_mask import (
    DiluteItemResult,
    DiluteReport,
)
from percell4.gui.dilute_from_mask_dialog import (
    _DEFAULT_OUTPUT,
    _DEFAULT_RADIUS,
    _RADIUS_MAX,
    _RADIUS_MIN,
    DiluteFromMaskDialog,
)

# ── Fixture builders ──────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channel_names: list[str] = (),
    mask_names: list[str] = (),
    label_names: list[str] = (),
) -> Path:
    """Create a minimal .h5 fixture for the dialog's pre-flight reads.

    Writes ``/metadata.channel_names``, ``/masks/<name>`` groups, and
    ``/labels/<name>`` groups — the three namespaces the dialog discovers
    per file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        if channel_names:
            meta.attrs["channel_names"] = list(channel_names)
        if mask_names:
            mg = f.create_group("masks")
            for n in mask_names:
                mg.create_dataset(n, data=np.zeros((2, 2), dtype=np.uint8))
        if label_names:
            lg = f.create_group("labels")
            for n in label_names:
                lg.create_dataset(n, data=np.zeros((2, 2), dtype=np.int32))
    return path


@pytest.fixture(autouse=True)
def _clear_qsettings():
    """Isolate QSettings so dialog defaults are deterministic per test."""
    from qtpy.QtCore import QSettings

    qs = QSettings("LeeLabPerCell4", "PerCell4")
    qs.remove("dilute_from_mask/radius_px")
    qs.remove("dilute_from_mask/output_name")
    yield
    qs.remove("dilute_from_mask/radius_px")
    qs.remove("dilute_from_mask/output_name")


def _stub_orchestrator(items: list[DiluteItemResult]):
    """Build an orchestrator stub that fires progress_cb for each item and
    returns a DiluteReport of those items.

    Honors ``cancel_check`` between items (same contract as
    ``batch_dilute_from_mask``).
    """

    def _stub(
        h5_paths,
        *,
        mask_name,
        segmentation_name,
        radius_px,
        output_name,
        progress_callback=None,
        cancel_check=None,
    ):
        emitted: list[DiluteItemResult] = []
        for item in items:
            if cancel_check is not None and cancel_check():
                break
            emitted.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return DiluteReport(items=tuple(emitted))

    return _stub


# ── Construction ──────────────────────────────────────────────


def test_dialog_constructs_with_expected_widgets(qtbot):
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    assert dlg._dataset_list is not None
    assert dlg._mask_combo is not None
    assert dlg._seg_combo is not None
    assert dlg._radius_spin is not None
    assert dlg._output_edit is not None
    assert dlg._start_btn is not None
    # Defaults match the plan.
    assert dlg._radius_spin.value() == _DEFAULT_RADIUS
    assert dlg._output_edit.text() == _DEFAULT_OUTPUT
    # Start disabled with no datasets.
    assert dlg._start_btn.isEnabled() is False


def test_dialog_uses_dialog_helpers():
    """``wrap_in_scroll`` and ``cap_to_screen`` must appear in the source."""
    import inspect

    import percell4.gui.dilute_from_mask_dialog as mod

    src = inspect.getsource(mod)
    assert "wrap_in_scroll" in src
    assert "cap_to_screen" in src


# ── Happy path ────────────────────────────────────────────────


def test_happy_path_three_datasets_share_mask_and_seg(qtbot, tmp_path):
    paths = [
        _make_h5(
            tmp_path / f"d{i}.h5",
            channel_names=["mNG"],
            mask_names=["condensed"],
            label_names=["cells"],
        )
        for i in range(3)
    ]
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths(paths)

    # Both combos show the common names.
    mask_items = [dlg._mask_combo.itemText(i) for i in range(dlg._mask_combo.count())]
    seg_items = [dlg._seg_combo.itemText(i) for i in range(dlg._seg_combo.count())]
    assert mask_items == ["condensed"]
    assert seg_items == ["cells"]

    # Set radius + an unambiguous output name.
    dlg._radius_spin.setValue(4)
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")

    assert dlg._start_btn.isEnabled() is True


def test_run_happy_path_lists_three_processed(qtbot, tmp_path, monkeypatch):
    paths = [
        _make_h5(
            tmp_path / f"d{i}.h5",
            channel_names=["mNG"],
            mask_names=["condensed"],
            label_names=["cells"],
        )
        for i in range(3)
    ]
    items = [
        DiluteItemResult(h5_path=p.resolve(), status="processed", message="")
        for p in paths
    ]
    captured: dict[str, str] = {}

    def fake_exec(self):
        captured["main"] = self.text()
        captured["detail"] = self.detailedText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)

    dlg = DiluteFromMaskDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths(paths)
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")
    assert dlg._start_btn.isEnabled() is True

    dlg._on_start_clicked()

    report = dlg.last_report
    assert report is not None
    assert len(report.items) == 3
    assert report.total_processed == 3
    assert "3 processed" in captured.get("main", "")


# ── File dedup ────────────────────────────────────────────────


def test_file_dedup_same_path_twice_queues_once(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p, p])
    assert len(dlg._pending_datasets) == 1

    # Adding again later still does not duplicate.
    dlg._add_h5_paths([p])
    assert len(dlg._pending_datasets) == 1


# ── Intersection (D7) ─────────────────────────────────────────


def test_mask_present_in_only_some_files_not_offered(qtbot, tmp_path):
    p1 = _make_h5(
        tmp_path / "a.h5",
        mask_names=["condensed", "extra"],
        label_names=["cells"],
    )
    p2 = _make_h5(
        tmp_path / "b.h5",
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    mask_items = [dlg._mask_combo.itemText(i) for i in range(dlg._mask_combo.count())]
    assert mask_items == ["condensed"]
    assert "extra" not in mask_items


def test_no_common_mask_disables_start_with_message(qtbot, tmp_path):
    p1 = _make_h5(tmp_path / "a.h5", mask_names=["m1"], label_names=["cells"])
    p2 = _make_h5(tmp_path / "b.h5", mask_names=["m2"], label_names=["cells"])
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    assert dlg._mask_combo.count() == 0
    assert dlg._start_btn.isEnabled() is False
    assert "No mask name" in dlg._picker_status.text()


def test_no_common_segmentation_disables_start_with_message(qtbot, tmp_path):
    p1 = _make_h5(tmp_path / "a.h5", mask_names=["condensed"], label_names=["s1"])
    p2 = _make_h5(tmp_path / "b.h5", mask_names=["condensed"], label_names=["s2"])
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    assert dlg._seg_combo.count() == 0
    assert dlg._start_btn.isEnabled() is False
    assert "segmentation" in dlg._picker_status.text().lower()


# ── Collision validation ──────────────────────────────────────


def test_output_collides_with_mask_disables_start(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])

    # Output name == existing mask name → collision.
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "condensed")
    assert dlg._start_btn.isEnabled() is False
    assert "mask" in dlg._output_status.text().lower()
    assert "condensed" in dlg._output_status.text()


def test_output_collides_with_channel_disables_start(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])

    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "mNG")
    assert dlg._start_btn.isEnabled() is False
    assert "channel" in dlg._output_status.text().lower()


def test_output_collides_with_label_disables_start(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])

    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "cells")
    assert dlg._start_btn.isEnabled() is False
    assert "label" in dlg._output_status.text().lower()


def test_partial_collision_warns_but_keeps_start_enabled(qtbot, tmp_path):
    """A collision in SOME (not all) datasets warns but keeps Start enabled —
    the use case skips the colliding datasets and runs the rest (a normal
    incremental re-run). Mirrors the phasor dialog's graceful degradation."""
    a = _make_h5(
        tmp_path / "a.h5",
        mask_names=["condensed", "dilute"],  # already has the output mask
        label_names=["cells"],
    )
    b = _make_h5(
        tmp_path / "b.h5",
        mask_names=["condensed"],  # fresh — no 'dilute'
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([a, b])
    dlg._mask_combo.setCurrentText("condensed")
    dlg._seg_combo.setCurrentText("cells")

    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute")

    assert dlg._start_btn.isEnabled() is True  # 1 of 2 collides → not blocked
    txt = dlg._output_status.text().lower()
    assert "1 of 2" in txt and "skipped" in txt


def test_all_datasets_collide_blocks_start(qtbot, tmp_path):
    """When EVERY queued dataset already has the output name, Start is disabled."""
    a = _make_h5(
        tmp_path / "a.h5", mask_names=["condensed", "dilute"], label_names=["cells"]
    )
    b = _make_h5(
        tmp_path / "b.h5", mask_names=["condensed", "dilute"], label_names=["cells"]
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([a, b])
    dlg._mask_combo.setCurrentText("condensed")
    dlg._seg_combo.setCurrentText("cells")

    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute")

    assert dlg._start_btn.isEnabled() is False


def test_clearing_collision_re_enables_start(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])

    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "condensed")
    assert dlg._start_btn.isEnabled() is False

    # Drive a real edit to a non-colliding name → Start re-enables.
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")
    assert dlg._output_status.text() == ""
    assert dlg._start_btn.isEnabled() is True


def test_empty_output_disables_start(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        mask_names=["condensed"],
        label_names=["cells"],
    )
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])

    dlg._output_edit.clear()
    assert dlg._start_btn.isEnabled() is False


# ── Radius label / range / default ────────────────────────────


def test_radius_spinbox_labelled_as_radius_in_px(qtbot):
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    labels = [lbl.text().lower() for lbl in dlg.findChildren(QLabel)]
    assert any("radius" in t and "px" in t for t in labels)


def test_radius_range_and_default_enforced(qtbot):
    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    assert dlg._radius_spin.minimum() == _RADIUS_MIN
    assert dlg._radius_spin.maximum() == _RADIUS_MAX
    assert dlg._radius_spin.value() == _DEFAULT_RADIUS
    # Out-of-range values are clamped by the spinbox.
    dlg._radius_spin.setValue(999)
    assert dlg._radius_spin.value() == _RADIUS_MAX
    dlg._radius_spin.setValue(-5)
    assert dlg._radius_spin.value() == _RADIUS_MIN


def test_start_passes_params_to_orchestrator(qtbot, tmp_path, monkeypatch):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    captured: dict[str, object] = {}

    def stub(h5_paths, **kwargs):
        captured["h5_paths"] = list(h5_paths)
        captured["mask_name"] = kwargs.get("mask_name")
        captured["segmentation_name"] = kwargs.get("segmentation_name")
        captured["radius_px"] = kwargs.get("radius_px")
        captured["output_name"] = kwargs.get("output_name")
        return DiluteReport(items=())

    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    dlg = DiluteFromMaskDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    dlg._radius_spin.setValue(7)
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")
    dlg._on_start_clicked()

    assert captured["mask_name"] == "condensed"
    assert captured["segmentation_name"] == "cells"
    assert captured["radius_px"] == 7
    assert captured["output_name"] == "dilute_out"
    assert captured["h5_paths"] == [p.resolve()]


# ── Empty-output annotation surfaced in summary ──────────────


def test_empty_output_annotation_surfaces_in_summary(qtbot, tmp_path, monkeypatch):
    p = _make_h5(
        tmp_path / "a.h5",
        mask_names=["condensed"],
        label_names=["cells"],
    )
    items = [
        DiluteItemResult(
            h5_path=p.resolve(),
            status="processed",
            message="output empty: 0 in-cell dilute pixels",
        )
    ]
    captured: dict[str, object] = {}

    def fake_exec(self):
        captured["main"] = self.text()
        captured["detail"] = self.detailedText()
        captured["icon"] = self.icon()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)
    dlg = DiluteFromMaskDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")
    dlg._on_start_clicked()

    assert "empty" in captured.get("main", "").lower()
    assert "output empty" in captured.get("detail", "")
    assert captured.get("icon") == QMessageBox.Warning


# ── Action discipline (R6/D5) ─────────────────────────────────


def test_configuring_does_not_mutate_session(qtbot, tmp_path):
    """Adding files + setting pickers/radius/output touches NO session
    field — the dialog never even reads the session during configuration.
    """
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    fake_session = MagicMock()
    fake_host = MagicMock()
    fake_host.get_session.return_value = fake_session

    dlg = DiluteFromMaskDialog()
    qtbot.addWidget(dlg)
    dlg._host = fake_host

    dlg._add_h5_paths([p])
    dlg._radius_spin.setValue(6)
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")

    # No session interaction at all during configuration.
    fake_host.get_session.assert_not_called()
    fake_session.refresh_resource_lists.assert_not_called()
    fake_session.set_active_mask.assert_not_called()
    fake_session.set_active_segmentation.assert_not_called()


def test_refresh_fires_when_active_dataset_in_batch(qtbot, tmp_path, monkeypatch):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    items = [DiluteItemResult(h5_path=p.resolve(), status="processed", message="")]
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)

    fake_session = MagicMock()
    fake_session.dataset.path = p
    fake_host = MagicMock()
    fake_host.get_session.return_value = fake_session

    dlg = DiluteFromMaskDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._host = fake_host
    dlg._add_h5_paths([p])
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")
    dlg._on_start_clicked()

    assert fake_session.refresh_resource_lists.call_count == 1
    _, kwargs = fake_session.refresh_resource_lists.call_args
    assert "mask_names" in kwargs


def test_refresh_skipped_when_active_dataset_not_in_batch(
    qtbot, tmp_path, monkeypatch
):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    other = _make_h5(
        tmp_path / "other.h5",
        channel_names=["mNG"],
        mask_names=["condensed"],
        label_names=["cells"],
    )
    items = [DiluteItemResult(h5_path=p.resolve(), status="processed", message="")]
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)

    fake_session = MagicMock()
    fake_session.dataset.path = other
    fake_host = MagicMock()
    fake_host.get_session.return_value = fake_session

    dlg = DiluteFromMaskDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._host = fake_host
    dlg._add_h5_paths([p])
    dlg._output_edit.clear()
    qtbot.keyClicks(dlg._output_edit, "dilute_out")
    dlg._on_start_clicked()

    fake_session.refresh_resource_lists.assert_not_called()
