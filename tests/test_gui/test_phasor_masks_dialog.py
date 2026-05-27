"""Tests for :class:`PhasorMasksDialog` (U3).

Drives real user-edit signals (``qtbot.keyClicks``, ``qtbot.mouseClick``)
where possible — programmatic ``setValue``/``setText`` is used only when
the goal of the test is to assert downstream effects of a known value,
not to verify that the user-edit signal is wired. See
``docs/solutions/conventions/qt-wire-user-edit-signals-2026-05-12.md``.

Run-loop tests monkeypatch :func:`batch_fit_phasor_masks` to a
deterministic stub that fires the progress callback synchronously, so the
loop completes without needing real HDF5 fixtures with phasor maps.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import h5py
import numpy as np
import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
)

from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    BatchPhasorReport,
)
from percell4.gui.phasor_masks_dialog import (
    PhasorMasksDialog,
    _DEFAULT_SUFFIX_A,
    _DEFAULT_SUFFIX_B,
    _DEFAULT_T_FIT,
    _DEFAULT_T_MASK_A,
    _DEFAULT_T_MASK_B,
)


# ── Fixture builders ──────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channel_names: list[str],
    decay_channels: list[str] | None = None,
    mask_names: list[str] = (),
    bytes_channel_names: bool = False,
) -> Path:
    """Create a minimal .h5 fixture for the dialog's pre-flight reads.

    ``decay_channels`` defaults to ``channel_names``; pass an explicit
    subset to simulate "channel listed but no /decay group".
    """
    if decay_channels is None:
        decay_channels = list(channel_names)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        if bytes_channel_names:
            meta.attrs["channel_names"] = [
                np.bytes_(c) for c in channel_names
            ]
        else:
            meta.attrs["channel_names"] = channel_names
        if decay_channels:
            dg = f.create_group("decay")
            for ch in decay_channels:
                # Tiny synthetic decay; dialog doesn't read it, only the
                # group names.
                dg.create_dataset(ch, data=np.zeros((2, 2, 4), dtype=np.float32))
        if mask_names:
            mg = f.create_group("masks")
            for n in mask_names:
                mg.create_dataset(n, data=np.zeros((2, 2), dtype=np.uint8))
    return path


@pytest.fixture(autouse=True)
def _clear_qsettings(monkeypatch):
    """Isolate QSettings so dialog defaults are deterministic per test."""
    from qtpy.QtCore import QSettings
    qs = QSettings("LeeLabPerCell4", "PerCell4")
    qs.remove("phasor_masks/t_fit")
    qs.remove("phasor_masks/t_mask_a")
    qs.remove("phasor_masks/t_mask_b")
    qs.remove("phasor_masks/suffix_a")
    qs.remove("phasor_masks/suffix_b")
    yield
    qs.remove("phasor_masks/t_fit")
    qs.remove("phasor_masks/t_mask_a")
    qs.remove("phasor_masks/t_mask_b")
    qs.remove("phasor_masks/suffix_a")
    qs.remove("phasor_masks/suffix_b")


# ── Construction ──────────────────────────────────────────────


def test_dialog_constructs_with_expected_widgets(qtbot):
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    assert dlg._dataset_list is not None
    assert dlg._channel_list is not None
    assert dlg._t_fit_spin is not None
    assert dlg._t_mask_a_spin is not None
    assert dlg._t_mask_b_spin is not None
    assert dlg._suffix_a_edit is not None
    assert dlg._suffix_b_edit is not None
    assert dlg._start_btn is not None
    # Defaults match the plan.
    assert dlg._t_fit_spin.value() == _DEFAULT_T_FIT
    assert dlg._t_mask_a_spin.value() == _DEFAULT_T_MASK_A
    assert dlg._t_mask_b_spin.value() == _DEFAULT_T_MASK_B
    assert dlg._suffix_a_edit.text() == _DEFAULT_SUFFIX_A
    assert dlg._suffix_b_edit.text() == _DEFAULT_SUFFIX_B
    # Start disabled with no datasets.
    assert dlg._start_btn.isEnabled() is False


def test_dialog_uses_dialog_helpers():
    """``wrap_in_scroll`` and ``cap_to_screen`` must appear in the source."""
    import inspect

    import percell4.gui.phasor_masks_dialog as mod

    src = inspect.getsource(mod)
    assert "wrap_in_scroll" in src
    assert "cap_to_screen" in src


# ── Channel-picker behavior ───────────────────────────────────


def test_happy_path_two_datasets_sharing_channels(qtbot, tmp_path):
    p1 = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG", "Halo"],
    )
    p2 = _make_h5(
        tmp_path / "b.h5",
        channel_names=["mNG", "Halo"],
    )
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    # Picker shows both channels.
    items = [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    assert items == ["Halo", "mNG"] or items == ["mNG", "Halo"]
    assert len(dlg._eligible_channels) == 2


def test_intersection_empty_disables_start(qtbot, tmp_path):
    p1 = _make_h5(tmp_path / "a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "b.h5", channel_names=["Halo"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    assert dlg._channel_list.count() == 0
    assert "No channels" in dlg._channel_status.text()
    assert dlg._start_btn.isEnabled() is False


def test_channel_name_collision_excludes_channel(qtbot, tmp_path):
    """Dataset has `mNG` + `mNG_phasor_1`; default suffix `_phasor_1`
    collides → mNG excluded."""
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG", "mNG_phasor_1"],
        decay_channels=["mNG", "mNG_phasor_1"],
    )
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    items = [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    assert "mNG" not in items
    # mNG_phasor_1 itself doesn't collide → still listed.
    assert "mNG_phasor_1" in items
    assert any("mNG" in r and "mNG_phasor_1" in r for r in dlg._collision_reasons)


def test_changing_suffix_clears_collision(qtbot, tmp_path):
    """Changing the suffix removes the collision so mNG re-appears."""
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG", "mNG_phasor_1"],
    )
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    assert "mNG" not in [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    # Drive a real user edit so the slot wiring is exercised.
    dlg._suffix_a_edit.clear()
    qtbot.keyClicks(dlg._suffix_a_edit, "_p1")
    items = [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    assert "mNG" in items


def test_dataset_with_channel_but_no_decay_excludes_channel(qtbot, tmp_path):
    """Dataset 1 has /decay/mNG; dataset 2 lists mNG but has no decay
    group for it → mNG excluded from the intersection."""
    p1 = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        decay_channels=["mNG"],
    )
    p2 = _make_h5(
        tmp_path / "b.h5",
        channel_names=["mNG"],
        decay_channels=[],
    )
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    assert dlg._channel_list.count() == 0


def test_bytes_channel_names_normalized(qtbot, tmp_path):
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        bytes_channel_names=True,
    )
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    items = [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    assert items == ["mNG"]


# ── Start-enabled gating ─────────────────────────────────────


def _check_first_channel(dlg: PhasorMasksDialog) -> None:
    """Helper: tick the first eligible channel."""
    assert dlg._channel_list.count() > 0
    item = dlg._channel_list.item(0)
    item.setCheckState(Qt.Checked)
    # itemChanged emits naturally for setCheckState.


def test_empty_suffix_disables_start(qtbot, tmp_path):
    p = _make_h5(tmp_path / "a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    _check_first_channel(dlg)
    assert dlg._start_btn.isEnabled()

    # Drive a real user edit: clear suffix A → Start disables.
    dlg._suffix_a_edit.clear()
    assert dlg._start_btn.isEnabled() is False

    # Drive a real user edit: type a new suffix → Start re-enables.
    qtbot.keyClicks(dlg._suffix_a_edit, "_x")
    assert dlg._start_btn.isEnabled()


def test_identical_suffixes_disable_start(qtbot, tmp_path):
    p = _make_h5(tmp_path / "a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    _check_first_channel(dlg)
    assert dlg._start_btn.isEnabled()

    # Programmatic clear + qtbot.keyClicks to exercise the textChanged path.
    dlg._suffix_b_edit.clear()
    qtbot.keyClicks(dlg._suffix_b_edit, _DEFAULT_SUFFIX_A)
    assert dlg._suffix_a_edit.text() == dlg._suffix_b_edit.text()
    assert dlg._start_btn.isEnabled() is False


def test_suffix_user_edit_refreshes_channel_picker(qtbot, tmp_path):
    """``qtbot.keyClicks`` on Suffix A (not setText) triggers both
    `_update_start_enabled` AND `_refresh_channel_picker`."""
    p = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG", "mNG_xyz"],
    )
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    # Default suffix `_phasor_1` does NOT collide → mNG present.
    assert "mNG" in [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    # Type a colliding suffix into Suffix A.
    dlg._suffix_a_edit.clear()
    qtbot.keyClicks(dlg._suffix_a_edit, "_xyz")
    items_after = [
        dlg._channel_list.item(i).text()
        for i in range(dlg._channel_list.count())
    ]
    # mNG_xyz collides with channel mNG_xyz now (because mNG + _xyz =
    # mNG_xyz, an existing channel name) → mNG dropped.
    assert "mNG" not in items_after


def test_no_dataset_disables_start(qtbot):
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    # Even with valid suffixes and checked-but-nonexistent channels,
    # an empty dataset list means Start is disabled.
    assert dlg._start_btn.isEnabled() is False


def test_no_channel_checked_disables_start(qtbot, tmp_path):
    p = _make_h5(tmp_path / "a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])
    # Picker has one item but it's unchecked.
    assert dlg._channel_list.count() == 1
    assert dlg._start_btn.isEnabled() is False


# ── Run mechanics ─────────────────────────────────────────────


def _stub_orchestrator(items: list[BatchPhasorItemResult]):
    """Build an orchestrator stub that fires progress_cb for each item
    and returns a BatchPhasorReport of those items.

    Honors ``cancel_check`` between items (same contract as
    ``batch_fit_phasor_masks``).
    """

    def _stub(
        h5_paths,
        *,
        channels,
        t_fit,
        t_mask_a,
        t_mask_b,
        suffix_a,
        suffix_b,
        ensure_phasor=True,
        progress_callback=None,
        cancel_check=None,
    ):
        emitted: list[BatchPhasorItemResult] = []
        for item in items:
            if cancel_check is not None and cancel_check():
                break
            emitted.append(item)
            if progress_callback is not None:
                progress_callback(item)
        return BatchPhasorReport(items=tuple(emitted))

    return _stub


def _setup_two_datasets(qtbot, tmp_path):
    p1 = _make_h5(tmp_path / "a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "b.h5", channel_names=["mNG"])
    return p1, p2


def test_run_happy_path_all_succeeded(qtbot, tmp_path, monkeypatch):
    p1, p2 = _setup_two_datasets(qtbot, tmp_path)
    items = [
        BatchPhasorItemResult(h5_path=p1, status="succeeded", processed=("mNG",)),
        BatchPhasorItemResult(h5_path=p2, status="succeeded", processed=("mNG",)),
    ]
    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    _check_first_channel(dlg)
    # Suppress the summary QMessageBox via monkeypatch on exec_.
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    dlg._on_start_clicked()
    report = dlg.last_report
    assert report is not None
    assert len(report.items) == 2
    assert all(it.status == "succeeded" for it in report.items)


def test_run_partial_uses_warning_icon(qtbot, tmp_path, monkeypatch):
    p1, _ = _setup_two_datasets(qtbot, tmp_path)
    items = [
        BatchPhasorItemResult(
            h5_path=p1,
            status="partial",
            processed=(),
            errors={"mNG": "degenerate fit (ellipse has zero area)"},
        )
    ]
    captured: dict[str, int] = {}

    def fake_exec(self):
        # QMessageBox.icon() returns the icon enum.
        captured["icon"] = self.icon()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)
    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1])
    _check_first_channel(dlg)
    dlg._on_start_clicked()
    assert captured.get("icon") == QMessageBox.Warning


def test_run_cancel_breaks_loop(qtbot, tmp_path, monkeypatch):
    p1, p2 = _setup_two_datasets(qtbot, tmp_path)
    items = [
        BatchPhasorItemResult(h5_path=p1, status="succeeded", processed=("mNG",)),
        BatchPhasorItemResult(h5_path=p2, status="succeeded", processed=("mNG",)),
    ]

    # The dialog creates the QProgressDialog inside _on_start_clicked.
    # Capture it from __init__ via monkeypatch, then call cancel() before
    # the second item's progress callback runs.
    progress_holder: dict[str, QProgressDialog] = {}
    real_init = QProgressDialog.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        progress_holder["pd"] = self

    monkeypatch.setattr(QProgressDialog, "__init__", patched_init)
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)

    def cancel_after_first(item):
        # Once we've recorded the first item, immediately cancel.
        progress_holder["pd"].cancel()

    def stub(
        h5_paths,
        *,
        channels,
        t_fit,
        t_mask_a,
        t_mask_b,
        suffix_a,
        suffix_b,
        ensure_phasor=True,
        progress_callback=None,
        cancel_check=None,
    ):
        emitted: list[BatchPhasorItemResult] = []
        for i, item in enumerate(items):
            if cancel_check is not None and cancel_check():
                break
            emitted.append(item)
            if progress_callback is not None:
                progress_callback(item)
            if i == 0:
                cancel_after_first(item)
        return BatchPhasorReport(items=tuple(emitted))

    dlg = PhasorMasksDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    _check_first_channel(dlg)
    dlg._on_start_clicked()
    report = dlg.last_report
    assert report is not None
    # Only the first item should land before cancel takes effect on the
    # second iteration.
    assert len(report.items) == 1
    assert dlg.cancelled is True


def test_end_of_run_refresh_fires_when_active_dataset_matches(
    qtbot, tmp_path, monkeypatch
):
    p1, _ = _setup_two_datasets(qtbot, tmp_path)
    items = [
        BatchPhasorItemResult(h5_path=p1, status="succeeded", processed=("mNG",))
    ]
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)

    # Build a fake host with get_session().dataset.path matching p1.
    fake_session = MagicMock()
    fake_session.dataset.path = p1
    fake_host = MagicMock()
    fake_host.get_session.return_value = fake_session

    dlg = PhasorMasksDialog(
        parent=None,
        orchestrator=_stub_orchestrator(items),
    )
    qtbot.addWidget(dlg)
    # Inject the host post-hoc; the dialog's parent() is None for testing.
    dlg._host = fake_host
    dlg._add_h5_paths([p1])
    _check_first_channel(dlg)
    dlg._on_start_clicked()

    assert fake_session.refresh_resource_lists.called
    _, kwargs = fake_session.refresh_resource_lists.call_args
    # mask_names kwarg is present.
    assert "mask_names" in kwargs


def test_end_of_run_refresh_skipped_when_active_dataset_unrelated(
    qtbot, tmp_path, monkeypatch
):
    p1, _ = _setup_two_datasets(qtbot, tmp_path)
    other = _make_h5(tmp_path / "other.h5", channel_names=["mNG"])
    items = [
        BatchPhasorItemResult(h5_path=p1, status="succeeded", processed=("mNG",))
    ]
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    fake_session = MagicMock()
    fake_session.dataset.path = other
    fake_host = MagicMock()
    fake_host.get_session.return_value = fake_session

    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._host = fake_host
    dlg._add_h5_paths([p1])
    _check_first_channel(dlg)
    dlg._on_start_clicked()

    fake_session.refresh_resource_lists.assert_not_called()


def test_end_of_run_refresh_skipped_when_no_active_dataset(
    qtbot, tmp_path, monkeypatch
):
    p1, _ = _setup_two_datasets(qtbot, tmp_path)
    items = [
        BatchPhasorItemResult(h5_path=p1, status="succeeded", processed=("mNG",))
    ]
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    fake_session = MagicMock()
    fake_session.dataset = None
    fake_host = MagicMock()
    fake_host.get_session.return_value = fake_session

    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._host = fake_host
    dlg._add_h5_paths([p1])
    _check_first_channel(dlg)
    dlg._on_start_clicked()

    fake_session.refresh_resource_lists.assert_not_called()


def test_overwrite_tracking_surfaces_in_summary(qtbot, tmp_path, monkeypatch):
    """Pre-populating a dataset's /masks/<ch><suffix_a> shows up in the
    detailed text of the end-of-run summary."""
    p1 = _make_h5(
        tmp_path / "a.h5",
        channel_names=["mNG"],
        decay_channels=["mNG"],
        mask_names=[f"mNG{_DEFAULT_SUFFIX_A}"],
    )
    items = [
        BatchPhasorItemResult(h5_path=p1, status="succeeded", processed=("mNG",))
    ]
    captured_detailed: dict[str, str] = {}

    def fake_exec(self):
        captured_detailed["text"] = self.detailedText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)

    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1])
    _check_first_channel(dlg)
    dlg._on_start_clicked()

    detail = captured_detailed.get("text", "")
    assert "Overwrote" in detail
    assert f"mNG{_DEFAULT_SUFFIX_A}" in detail


# ── Launcher slot ─────────────────────────────────────────────


def _find_button(win, label: str):
    for btn in win.findChildren(QPushButton):
        if btn.text() == label:
            return btn
    raise AssertionError(f"button {label!r} not found")


def test_workflows_panel_has_phasor_masks_button(qtbot):
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_button(win, "Automated phasor-masks workflow")
    assert btn is not None
    assert btn.toolTip()


def test_clicking_button_opens_phasor_masks_dialog(qtbot):
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_button(win, "Automated phasor-masks workflow")

    fake_dialog_cls = MagicMock()
    fake_dialog = MagicMock()
    fake_dialog.last_report = None
    fake_dialog_cls.return_value = fake_dialog

    with patch(
        "percell4.gui.phasor_masks_dialog.PhasorMasksDialog", fake_dialog_cls
    ):
        btn.click()

    fake_dialog_cls.assert_called_once()
    fake_dialog.exec_.assert_called_once()
    fake_dialog.deleteLater.assert_called_once()


def test_button_respects_workflow_lock(qtbot):
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    win.set_workflow_locked(True)
    btn = _find_button(win, "Automated phasor-masks workflow")

    fake_dialog_cls = MagicMock()
    with patch(
        "percell4.gui.phasor_masks_dialog.PhasorMasksDialog", fake_dialog_cls
    ):
        btn.click()

    # The reentrance guard short-circuits before PhasorMasksDialog is
    # constructed. (The central widget — including this button — is
    # disabled while a workflow is locked, so the click is a no-op; we
    # assert via assert_not_called rather than the status-bar message.)
    fake_dialog_cls.assert_not_called()


def test_handler_updates_status_bar_with_report(qtbot, tmp_path):
    """When the dialog returns a non-None report, the status bar shows a
    completion message."""
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    win = LauncherWindow(CellDataModel())
    qtbot.addWidget(win)
    btn = _find_button(win, "Automated phasor-masks workflow")

    fake_dialog = MagicMock()
    fake_dialog.last_report = BatchPhasorReport(
        items=(
            BatchPhasorItemResult(
                h5_path=tmp_path / "x.h5",
                status="succeeded",
                processed=("mNG",),
            ),
        )
    )

    with patch(
        "percell4.gui.phasor_masks_dialog.PhasorMasksDialog",
        return_value=fake_dialog,
    ):
        btn.click()

    msg = win.statusBar().currentMessage()
    assert "Phasor-masks workflow complete" in msg
    assert "1 dataset" in msg
