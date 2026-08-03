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
    QComboBox,
    QMessageBox,
    QProgressDialog,
    QPushButton,
)

from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
    BatchPhasorReport,
)
from percell4.gui.phasor_masks_dialog import (
    _DEFAULT_SUFFIX_A,
    _DEFAULT_SUFFIX_B,
    _DEFAULT_T_FIT,
    _DEFAULT_T_MASK_A,
    _DEFAULT_T_MASK_B,
    PhasorMasksDialog,
)
from percell4.gui.settings import app_settings

# ── Fixture builders ──────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channel_names: list[str],
    decay_channels: list[str] | None = None,
    mask_names: list[str] = (),
    bytes_channel_names: bool = False,
    n_timepoints: int = 1,
) -> Path:
    """Create a minimal .h5 fixture for the dialog's pre-flight reads.

    ``decay_channels`` defaults to ``channel_names``; pass an explicit
    subset to simulate "channel listed but no /decay group". ``n_timepoints``
    > 1 marks the dataset as time-lapse (the U2 self-fit path is supported).
    """
    if decay_channels is None:
        decay_channels = list(channel_names)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        if n_timepoints != 1:
            meta.attrs["n_timepoints"] = n_timepoints
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
    qs = app_settings()
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
        roi_sources=None,
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
        roi_sources=None,
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


# ── ROI source dropdown: state + cascade ─────────────────────


def _select_combo_userdata(combo: QComboBox, userdata):
    """Locate the index whose `itemData` matches `userdata` and fire the
    user-interaction signal via ``activated[int].emit``.

    Mirrors a real user clicking the combo and picking that item — the
    canonical way to drive ``QComboBox`` in a test without programmatic
    ``setCurrentIndex`` (which bypasses the wiring per the
    qt-wire-user-edit-signals learning).
    """
    for i in range(combo.count()):
        if combo.itemData(i) == userdata:
            combo.setCurrentIndex(i)
            combo.activated[int].emit(i)
            return i
    raise AssertionError(
        f"no item with userData={userdata!r} in combo "
        f"(have {[combo.itemData(i) for i in range(combo.count())]})"
    )


def test_default_state_two_datasets_each_offer_the_other(qtbot, tmp_path):
    """Two datasets queued → each row's combo shows
    ``fit own GMM`` (selected) plus the other dataset's basename as the
    only alternative source. Neither row has a non-None roi_source.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    # Default: both rows self-fit.
    assert dlg._pending_datasets[0].roi_source is None
    assert dlg._pending_datasets[1].roi_source is None
    assert dlg._pending_datasets[0].fell_back is False
    assert dlg._pending_datasets[1].fell_back is False

    c0 = dlg._row_combo(0)
    c1 = dlg._row_combo(1)
    assert c0 is not None and c1 is not None
    # 2 items each: "fit own GMM" + the other dataset.
    assert c0.count() == 2
    assert c1.count() == 2
    assert c0.itemData(0) is None
    assert c0.itemData(1) == p2.resolve()
    assert c1.itemData(0) is None
    assert c1.itemData(1) == p1.resolve()
    # Selected item is "fit own GMM".
    assert c0.currentIndex() == 0
    assert c1.currentIndex() == 0


def test_assign_source_via_user_interaction(qtbot, tmp_path):
    """User picks AsTreated_a's combo and switches to untreated_a.
    The dialog updates ``_PendingDataset.roi_source``, and untreated_a's
    combo no longer offers AsTreated_a (which is no longer self-fitting).
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    c1 = dlg._row_combo(1)
    assert c1 is not None
    # Drive a real user-interaction signal on the combo.
    _select_combo_userdata(c1, p1.resolve())

    assert dlg._pending_datasets[1].roi_source == p1.resolve()
    # untreated_a's combo: only "fit own GMM" — AsTreated_a is no longer
    # self-fitting so it's not offered.
    c0 = dlg._row_combo(0)
    assert c0 is not None
    assert c0.count() == 1
    assert c0.itemData(0) is None


def test_change_source_back_to_self_fitting(qtbot, tmp_path):
    """Assign, then re-pick `fit own GMM`. The previously-assigned row
    becomes self-fitting again and reappears as a candidate on the other
    row's dropdown.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    c1 = dlg._row_combo(1)
    assert c1 is not None
    _select_combo_userdata(c1, p1.resolve())
    assert dlg._pending_datasets[1].roi_source == p1.resolve()

    # Pick "fit own GMM" again.
    c1 = dlg._row_combo(1)
    assert c1 is not None
    _select_combo_userdata(c1, None)

    assert dlg._pending_datasets[1].roi_source is None
    c0 = dlg._row_combo(0)
    assert c0 is not None
    # AsTreated_a is once again a candidate source.
    assert any(c0.itemData(i) == p2.resolve() for i in range(c0.count()))


def test_persistent_fell_back_disables_start_until_acknowledged(
    qtbot, tmp_path
):
    """Load-bearing UX test.

    3 datasets [untreated_a, AsTreated_a, AsTreated_c]. AsTreated_a is
    assigned to untreated_a. User reassigns untreated_a → AsTreated_c.
    AsTreated_a is auto-flagged ``fell_back = True`` with prev_source =
    untreated_a, the dialog's status label fires once mentioning both
    names, and Start is disabled until the user clicks AsTreated_a's
    combo to acknowledge.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    p3 = _make_h5(tmp_path / "AsTreated_c.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2, p3])
    _check_first_channel(dlg)

    # AsTreated_a (row 1) → untreated_a.
    c1 = dlg._row_combo(1)
    assert c1 is not None
    _select_combo_userdata(c1, p1.resolve())
    assert dlg._pending_datasets[1].roi_source == p1.resolve()
    assert dlg._start_btn.isEnabled() is True

    # untreated_a (row 0) → AsTreated_c.
    c0 = dlg._row_combo(0)
    assert c0 is not None
    _select_combo_userdata(c0, p3.resolve())

    # AsTreated_a fell back.
    pa = dlg._pending_datasets[1]
    assert pa.fell_back is True
    assert pa.roi_source is None
    assert pa._previous_roi_source == p1.resolve()

    # Combo's first-item label mentions "was: ... fell back".
    c1 = dlg._row_combo(1)
    assert c1 is not None
    label_text = c1.itemText(0)
    assert "was:" in label_text
    assert "untreated_a" in label_text
    assert "fell back" in label_text.lower()

    # Status label fired once naming both rows.
    assert dlg._status_label is not None
    status = dlg._status_label.text()
    assert "AsTreated_a" in status
    assert "untreated_a" in status

    # Start disabled with tooltip.
    assert dlg._start_btn.isEnabled() is False
    assert "Acknowledge" in dlg._start_btn.toolTip()

    # User clicks AsTreated_a's combo, picks "fit own GMM" → ack.
    c1 = dlg._row_combo(1)
    assert c1 is not None
    _select_combo_userdata(c1, None)

    pa = dlg._pending_datasets[1]
    assert pa.fell_back is False
    assert pa._previous_roi_source is None

    # Combo label is plain again.
    c1 = dlg._row_combo(1)
    assert c1 is not None
    assert c1.itemText(0) == "fit own GMM"

    # Start re-enabled.
    assert dlg._start_btn.isEnabled() is True


def test_remove_source_row_flags_dependent_fell_back(qtbot, tmp_path):
    """untreated_a is the source for AsTreated_a. User clicks ×
    on UNTREATED_A's row. AsTreated_a is fell_back, status label uses
    "removed" wording, Start disabled.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    _check_first_channel(dlg)

    # Assign AsTreated_a → untreated_a.
    c1 = dlg._row_combo(1)
    assert c1 is not None
    _select_combo_userdata(c1, p1.resolve())
    assert dlg._start_btn.isEnabled() is True

    # × on untreated_a (row 0).
    dlg._on_remove_row(0)

    # AsTreated_a is now row 0, with fell_back set.
    assert len(dlg._pending_datasets) == 1
    pa = dlg._pending_datasets[0]
    assert pa.h5_path == p2.resolve()
    assert pa.fell_back is True
    assert pa.roi_source is None
    assert pa._previous_roi_source == p1.resolve()

    # Status uses "removed" wording.
    assert dlg._status_label is not None
    status = dlg._status_label.text()
    assert "removed" in status
    assert "untreated_a" in status

    # Start disabled.
    assert dlg._start_btn.isEnabled() is False


def test_multiple_dependents_fall_back_together(qtbot, tmp_path):
    """untreated_a is the source for both AsTreated_a AND AsTreated_b.
    User reassigns untreated_a to AsTreated_c. Both AsTreated_a and
    AsTreated_b get ``fell_back = True``. Single status message names
    both.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    p3 = _make_h5(tmp_path / "AsTreated_b.h5", channel_names=["mNG"])
    p4 = _make_h5(tmp_path / "AsTreated_c.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2, p3, p4])
    _check_first_channel(dlg)

    # Assign AsTreated_a + AsTreated_b → untreated_a.
    _select_combo_userdata(dlg._row_combo(1), p1.resolve())
    _select_combo_userdata(dlg._row_combo(2), p1.resolve())

    # Reassign untreated_a → AsTreated_c.
    _select_combo_userdata(dlg._row_combo(0), p4.resolve())

    pa = dlg._pending_datasets[1]
    pb = dlg._pending_datasets[2]
    assert pa.fell_back is True
    assert pb.fell_back is True

    # Single status message names both.
    assert dlg._status_label is not None
    status = dlg._status_label.text()
    assert "AsTreated_a" in status
    assert "AsTreated_b" in status
    # Both names appear together separated by a comma (single message).
    assert status.count(": fell back") == 1

    assert dlg._start_btn.isEnabled() is False


def test_channel_deselection_after_source_assignment_does_not_cascade(
    qtbot, tmp_path, monkeypatch
):
    """Two datasets share [mNG, Halo]. Assign source on AsTreated_a.
    Deselect Halo from the channel picker → roi_source on AsTreated_a is
    unchanged. Start uses the post-deselection channels.
    """
    p1 = _make_h5(
        tmp_path / "untreated_a.h5", channel_names=["mNG", "Halo"]
    )
    p2 = _make_h5(
        tmp_path / "AsTreated_a.h5", channel_names=["mNG", "Halo"]
    )
    captured: dict[str, object] = {}

    def stub(h5_paths, **kwargs):
        captured["h5_paths"] = list(h5_paths)
        captured["channels"] = tuple(kwargs.get("channels", ()))
        captured["roi_sources"] = dict(kwargs.get("roi_sources") or {})
        return BatchPhasorReport(items=())

    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    dlg = PhasorMasksDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])
    # Tick both channels first.
    for i in range(dlg._channel_list.count()):
        dlg._channel_list.item(i).setCheckState(Qt.Checked)

    # Assign source on AsTreated_a (row 1).
    _select_combo_userdata(dlg._row_combo(1), p1.resolve())
    assert dlg._pending_datasets[1].roi_source == p1.resolve()

    # Deselect Halo.
    for i in range(dlg._channel_list.count()):
        item = dlg._channel_list.item(i)
        if item.text() == "Halo":
            item.setCheckState(Qt.Unchecked)

    # roi_source unchanged.
    assert dlg._pending_datasets[1].roi_source == p1.resolve()
    assert dlg._pending_datasets[1].fell_back is False

    dlg._on_start_clicked()
    assert captured.get("channels") == ("mNG",)


def test_start_disabled_when_source_not_in_queue(qtbot, tmp_path):
    """Defense-in-depth: directly mutate roi_source to a path not in the
    queue. Start should be disabled.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1])
    _check_first_channel(dlg)
    assert dlg._start_btn.isEnabled() is True

    # Directly mutate roi_source to a phantom path.
    dlg._pending_datasets[0].roi_source = tmp_path / "missing.h5"
    dlg._update_start_enabled()
    assert dlg._start_btn.isEnabled() is False


def test_combo_activated_signal_fires_on_user_interaction(qtbot, tmp_path):
    """Confirm `activated` fires when emitted (the test driver) and that
    programmatic ``setCurrentIndex`` alone does NOT trigger the cascade.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    p2 = _make_h5(tmp_path / "AsTreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1, p2])

    c1 = dlg._row_combo(1)
    assert c1 is not None

    # Programmatic setCurrentIndex DOES NOT fire activated → cascade
    # does not run, so the model is unchanged.
    target_idx = None
    for i in range(c1.count()):
        if c1.itemData(i) == p1.resolve():
            target_idx = i
            break
    assert target_idx is not None
    c1.setCurrentIndex(target_idx)
    # Model unchanged.
    assert dlg._pending_datasets[1].roi_source is None

    # Reset index to 0 to mimic a fresh state.
    c1.setCurrentIndex(0)

    # Now use waitSignal to confirm the activated signal really fires
    # via our test driver, and the model updates.
    with qtbot.waitSignal(c1.activated, timeout=1000):
        _select_combo_userdata(c1, p1.resolve())
    assert dlg._pending_datasets[1].roi_source == p1.resolve()


def test_row_local_remove_button_removes_only_that_row(qtbot, tmp_path):
    """Click × on row 2 of 4 → row 2 removed, rows 1/3/4 remain. The old
    "Remove selected" bottom button is gone (no child QPushButton with
    that label).
    """
    paths = [
        _make_h5(tmp_path / f"d{i}.h5", channel_names=["mNG"])
        for i in range(4)
    ]
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths(paths)
    assert len(dlg._pending_datasets) == 4

    # Click × on row 1 (second row, 0-indexed).
    btn = dlg._row_remove_button(1)
    assert btn is not None
    btn.click()

    assert len(dlg._pending_datasets) == 3
    remaining_paths = [pd.h5_path for pd in dlg._pending_datasets]
    assert remaining_paths == [paths[0].resolve(), paths[2].resolve(), paths[3].resolve()]

    # No QPushButton with text "Remove selected" anywhere in the dialog.
    for child_btn in dlg.findChildren(QPushButton):
        assert child_btn.text() != "Remove selected"


def test_summary_main_text_includes_self_shared_counts(
    qtbot, tmp_path, monkeypatch
):
    """Stub returns three succeeded items: s1 (self) + t1, t2 (shared).
    Main text: "3 succeeded, 0 partial, 0 failed. 1 self-fitted, 2 used
    shared ROI."
    """
    s1 = _make_h5(tmp_path / "s1.h5", channel_names=["mNG"])
    t1 = _make_h5(tmp_path / "t1.h5", channel_names=["mNG"])
    t2 = _make_h5(tmp_path / "t2.h5", channel_names=["mNG"])

    items = [
        BatchPhasorItemResult(h5_path=s1.resolve(), status="succeeded", processed=("mNG",)),
        BatchPhasorItemResult(h5_path=t1.resolve(), status="succeeded", processed=("mNG",)),
        BatchPhasorItemResult(h5_path=t2.resolve(), status="succeeded", processed=("mNG",)),
    ]
    captured: dict[str, str] = {}

    def fake_exec(self):
        captured["main"] = self.text()
        captured["detail"] = self.detailedText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)

    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([s1, t1, t2])
    _check_first_channel(dlg)

    # Assign t1 and t2 → s1.
    _select_combo_userdata(dlg._row_combo(1), s1.resolve())
    _select_combo_userdata(dlg._row_combo(2), s1.resolve())

    dlg._on_start_clicked()

    main = captured.get("main", "")
    assert "3 succeeded, 0 partial, 0 failed" in main
    assert "1 self-fitted, 2 used shared ROI" in main

    detail = captured.get("detail", "")
    assert "[source: self]" in detail
    assert "[source: s1.h5]" in detail


def test_summary_detail_line_includes_source_tag(
    qtbot, tmp_path, monkeypatch
):
    """Source's mNG processed + Halo failed. Target uses source → Halo
    error message names source. Detail text contains both lines tagged
    with their source.
    """
    s1 = _make_h5(tmp_path / "s1.h5", channel_names=["mNG", "Halo"])
    t1 = _make_h5(tmp_path / "t1.h5", channel_names=["mNG", "Halo"])

    items = [
        BatchPhasorItemResult(
            h5_path=s1.resolve(),
            status="partial",
            processed=("mNG",),
            errors={"Halo": "degenerate fit (ellipse has zero area)"},
        ),
        BatchPhasorItemResult(
            h5_path=t1.resolve(),
            status="partial",
            processed=("mNG",),
            errors={"Halo": "ROI source s1.h5 fit failed: see source's item for details"},
        ),
    ]
    captured: dict[str, str] = {}

    def fake_exec(self):
        captured["detail"] = self.detailedText()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)

    dlg = PhasorMasksDialog(orchestrator=_stub_orchestrator(items))
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([s1, t1])
    _check_first_channel(dlg)

    _select_combo_userdata(dlg._row_combo(1), s1.resolve())

    dlg._on_start_clicked()
    detail = captured.get("detail", "")
    assert "s1.h5 [source: self]" in detail
    assert "t1.h5 [source: s1.h5]" in detail
    # Both error lines are present.
    assert "degenerate fit" in detail
    assert "ROI source s1.h5 fit failed" in detail


def test_start_passes_roi_sources_to_use_case(qtbot, tmp_path, monkeypatch):
    """Click Start → capture the use-case call. ``roi_sources`` keys
    cover every ``_PendingDataset.h5_path``; target rows map to source
    paths; self-fitting rows map to None.
    """
    s1 = _make_h5(tmp_path / "s1.h5", channel_names=["mNG"])
    t1 = _make_h5(tmp_path / "t1.h5", channel_names=["mNG"])
    t2 = _make_h5(tmp_path / "t2.h5", channel_names=["mNG"])

    captured: dict[str, object] = {}

    def stub(h5_paths, **kwargs):
        captured["roi_sources"] = dict(kwargs.get("roi_sources") or {})
        return BatchPhasorReport(items=())

    monkeypatch.setattr(QMessageBox, "exec_", lambda self: 0)
    dlg = PhasorMasksDialog(orchestrator=stub)
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([s1, t1, t2])
    _check_first_channel(dlg)

    _select_combo_userdata(dlg._row_combo(1), s1.resolve())
    _select_combo_userdata(dlg._row_combo(2), s1.resolve())

    dlg._on_start_clicked()

    roi = captured["roi_sources"]
    assert isinstance(roi, dict)
    assert roi == {
        s1.resolve(): None,
        t1.resolve(): s1.resolve(),
        t2.resolve(): s1.resolve(),
    }


def test_row_path_label_tooltip_is_full_path(qtbot, tmp_path):
    """Each row's path label has a tooltip equal to the full resolved
    path. Label widget exists and label text matches the path string.
    """
    p1 = _make_h5(tmp_path / "untreated_a.h5", channel_names=["mNG"])
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p1])

    label = dlg._row_path_label(0)
    assert label is not None
    assert label.toolTip() == str(p1.resolve())


# ── Time-lapse (U2): self-fit datasets are no longer skipped ────────────


def test_timelapse_self_fit_dataset_is_queued_not_skipped(
    qtbot, tmp_path, monkeypatch
):
    """U2: a multi-timepoint dataset enters the queue for the self-fit path;
    the old 'Time-lapse FLIM not supported' QMessageBox no longer appears."""
    info_calls: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: (info_calls.append(a), QMessageBox.Ok)[1],
    )

    p = _make_h5(tmp_path / "timelapse.h5", channel_names=["mNG"], n_timepoints=3)
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([p])

    # Dataset queued (not silently dropped).
    assert len(dlg._pending_datasets) == 1
    assert dlg._pending_datasets[0].h5_path == p.resolve()
    # Channel picker populated → the dataset is a real candidate.
    assert "mNG" in dlg._eligible_channels
    # No "not supported" information dialog was shown.
    assert info_calls == []


def test_timelapse_and_single_t_datasets_coexist_in_queue(qtbot, tmp_path):
    """A mixed queue (single-t + time-lapse) keeps both — the self-fit path
    handles each per its own ``n_timepoints``."""
    single = _make_h5(tmp_path / "single.h5", channel_names=["mNG"])
    multi = _make_h5(tmp_path / "multi.h5", channel_names=["mNG"], n_timepoints=4)
    dlg = PhasorMasksDialog()
    qtbot.addWidget(dlg)
    dlg._add_h5_paths([single, multi])

    queued = {pd.h5_path for pd in dlg._pending_datasets}
    assert queued == {single.resolve(), multi.resolve()}
    assert "mNG" in dlg._eligible_channels
