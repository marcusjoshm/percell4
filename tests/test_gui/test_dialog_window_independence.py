"""Every converted dialog is a freestanding, screen-centred window.

Behavioural counterpart to ``test_popup_window_compliance.py``, which
proves by inspection that no dialog *forgets* the helpers. This file
proves the helpers actually did something on each real dialog class.

Convention: ``docs/solutions/ui-bugs/gnome-attaches-parented-modal-dialogs-2026-07-29.md``

What is provable here and what is not: the offscreen platform has no
window manager, so these tests assert window type, modality, and
geometry -- never that GNOME stopped gluing the dialog to its parent.
That check is manual and lives in the plan's U7.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QMessageBox,
    QWidget,
)

# The ten converted dialog classes that construct with no arguments.
# The remaining three need fixtures and get their own tests below.
NO_ARG_DIALOGS = [
    ("percell4.gui.import_dialog", "ImportDialog"),
    ("percell4.gui.compress_dialog", "CompressDialog"),
    ("percell4.gui.workflows.single_cell.config_dialog", "WorkflowConfigDialog"),
    ("percell4.gui.batch_tcspc_dialog", "BatchTCSPCDialog"),
    ("percell4.gui.flim_fret_dialog", "FlimFretDialog"),
    ("percell4.gui.phasor_masks_dialog", "PhasorMasksDialog"),
    ("percell4.gui.dilute_from_mask_dialog", "DiluteFromMaskDialog"),
    ("percell4.gui.per_particle_donut_dialog", "PerParticleDonutDialog"),
    ("percell4.gui.per_particle_multichannel_dialog", "PerParticleMultichannelDialog"),
    ("percell4.gui.whole_field_intensity_dialog", "WholeFieldIntensityDialog"),
]

IDS = [cls for _mod, cls in NO_ARG_DIALOGS]


@pytest.fixture(autouse=True)
def _linux_branch(monkeypatch):
    """Exercise the Linux branch on any host.

    ``detach_window`` and ``center_on_screen`` are deliberate no-ops off
    Linux, so every assertion below would fail on macOS or Windows. CI is
    Linux-only, which would hide that from everyone except a developer
    running the suite locally on another platform. The production gate reads
    ``sys.platform`` at call time precisely so this works.
    """
    monkeypatch.setattr(sys, "platform", "linux")


def _build(module_path: str, class_name: str, parent: QWidget | None = None):
    """Construct a converted dialog, parented by default.

    A parent matters for the centring assertions: ``cap_to_screen`` caps the
    dialog to 90% of the work area only when it has one, and several of
    these dialogs are wider than the 800x600 offscreen screen. Unparented,
    they stay oversized and get clamped to the work-area origin, which is
    indistinguishable from never having been centred at all.
    """
    cls = getattr(importlib.import_module(module_path), class_name)
    return cls(parent) if parent is not None else cls()


def _skip_if_larger_than_work_area(dialog: QDialog) -> None:
    """Skip a geometry assertion the offscreen screen cannot support.

    ``cap_to_screen`` cannot shrink a dialog below its own minimum size, and
    several of these set a minimum wider than the 800x600 offscreen work
    area. Such a dialog is clamped to the origin, which is exactly where an
    un-placed dialog sits -- so geometry proves nothing. The call-site
    recorder test covers these.
    """
    avail = dialog.screen().availableGeometry()
    frame = dialog.frameGeometry()
    if frame.width() > avail.width() or frame.height() > avail.height():
        pytest.skip(
            f"{dialog.__class__.__name__} minimum size "
            f"({frame.width()}x{frame.height()}) exceeds the "
            f"{avail.width()}x{avail.height()} offscreen work area; centring "
            "is unassertable by geometry, see the call-site recorder test"
        )


def _assert_centred(dialog: QDialog) -> None:
    """Assert real centring -- no escape clause.

    An earlier version OR-ed this with "top-left sits at the work-area
    origin", which an un-centred dialog at (0,0) satisfies unconditionally,
    so the assertion could not fail. Six reviewers proved that by neutering
    ``center_on_screen`` and still getting green.
    """
    _skip_if_larger_than_work_area(dialog)
    avail = dialog.screen().availableGeometry()
    frame = dialog.frameGeometry()
    centre = frame.center()
    # Offscreen synthesises a 2px frame per side; a real WM adds a title bar.
    assert abs(centre.x() - avail.center().x()) <= 5, (
        f"{dialog.__class__.__name__} not horizontally centred: "
        f"{centre.x()} vs {avail.center().x()}"
    )
    assert abs(centre.y() - avail.center().y()) <= 25, (
        f"{dialog.__class__.__name__} not vertically centred: "
        f"{centre.y()} vs {avail.center().y()}"
    )


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_uses_non_attaching_window_type(qtbot, module_path, class_name):
    """R1 -- UTILITY type is what fails mutter's attach gate."""
    dialog = _build(module_path, class_name)
    qtbot.addWidget(dialog)

    assert dialog.windowType() == Qt.Tool


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_keeps_decorations(qtbot, module_path, class_name):
    """R7 -- a converted dialog still has a title bar to drag."""
    dialog = _build(module_path, class_name)
    qtbot.addWidget(dialog)

    flags = dialog.windowFlags()
    assert flags & Qt.WindowTitleHint
    assert flags & Qt.WindowCloseButtonHint


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_calls_both_helpers_at_its_own_call_site(
    qtbot, monkeypatch, module_path, class_name
):
    """R1 + R2 pinned at the call site, independent of geometry.

    This is the assertion that cannot be satisfied by accident. Geometry
    alone cannot prove ``center_on_screen`` ran: a dialog wider than the
    800x600 offscreen work area gets clamped to the origin, which is exactly
    where an un-centred dialog already sits. Recording the calls sidesteps
    that entirely, and covers every dialog rather than only the ones that
    happen to fit.
    """
    module = importlib.import_module(module_path)
    calls: list[str] = []
    monkeypatch.setattr(
        module, "detach_window", lambda popup: calls.append("detach")
    )
    monkeypatch.setattr(
        module, "center_on_screen", lambda popup: calls.append("centre")
    )

    dialog = getattr(module, class_name)()
    qtbot.addWidget(dialog)

    assert calls == ["detach", "centre"], (
        f"{class_name} must call detach_window then center_on_screen in "
        f"__init__; recorded {calls}"
    )


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_opens_centred_on_screen(qtbot, module_path, class_name):
    """R2, asserted on real geometry where the dialog can fit.

    Skipped for dialogs whose minimum size exceeds the offscreen work area:
    ``cap_to_screen`` cannot shrink below a minimum, so they are clamped to
    the origin and geometry cannot distinguish centred from never-placed.
    ``test_dialog_calls_both_helpers_at_its_own_call_site`` covers those.
    """
    parent = QMainWindow()
    qtbot.addWidget(parent)
    parent.show()
    dialog = _build(module_path, class_name, parent)
    qtbot.addWidget(dialog)

    _assert_centred(dialog)


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_is_untouched_off_linux(qtbot, monkeypatch, module_path, class_name):
    """R6 -- the platform gate is load-bearing, so prove it both ways.

    Overrides the autouse Linux fixture for this case only.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    dialog = _build(module_path, class_name)
    qtbot.addWidget(dialog)

    assert dialog.windowType() == Qt.Dialog


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_stays_visible_after_show(qtbot, module_path, class_name):
    """Regression guard: ``setWindowFlags`` hides an already-visible widget.

    The helpers run in ``__init__``, before any ``show()``, precisely so
    this cannot happen -- see
    ``docs/solutions/ui-bugs/qt-setwindowflag-hides-visible-widget-2026-05-14.md``.
    The trap reproduces under the offscreen platform, so this is a real check.
    """
    dialog = _build(module_path, class_name)
    qtbot.addWidget(dialog)

    dialog.show()

    assert dialog.isVisible()


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_keeps_screen_cap(qtbot, module_path, class_name):
    """R10 -- the flags change must not clear the Qt parent.

    ``cap_to_screen`` early-returns without a parent, so an unparenting
    regression would silently disable the screen-height cap.
    """
    parent = QMainWindow()
    qtbot.addWidget(parent)
    parent.show()
    cls = getattr(importlib.import_module(module_path), class_name)
    dialog = cls(parent)
    qtbot.addWidget(dialog)

    assert dialog.parent() is parent
    avail = parent.screen().availableGeometry()
    assert dialog.maximumHeight() == int(avail.height() * 0.9)


@pytest.mark.parametrize(("module_path", "class_name"), NO_ARG_DIALOGS, ids=IDS)
def test_dialog_stays_modal_through_exec(qtbot, module_path, class_name):
    """R3 -- modality is untouched; only the window type changed.

    ``exec_()`` is drivable offscreen: accept on the next tick so the
    nested event loop returns instead of hanging.
    """
    dialog = _build(module_path, class_name)
    qtbot.addWidget(dialog)

    observed: list[bool] = []

    def _accept_and_record() -> None:
        observed.append(dialog.isModal())
        observed.append(QApplication.activeModalWidget() is dialog)
        dialog.accept()

    QTimer.singleShot(0, _accept_and_record)
    dialog.exec_()

    assert observed == [True, True]


# ── The three dialogs needing fixtures ───────────────────────────────


def _make_store(path):
    """A minimal readable dataset, matching the pattern in
    ``test_dialog_migrations.py``."""
    import numpy as np

    from percell4.store import DatasetStore

    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["ch00", "ch01"]})
    store.write_array(
        "intensity",
        np.zeros((2, 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    return store


def test_add_layer_dialog_is_freestanding_and_centred(qtbot, tmp_path):
    from percell4.gui.add_layer_dialog import AddLayerDialog

    store = _make_store(tmp_path / "ds.h5")
    dlg = AddLayerDialog(parent=None, store=store, data_model=None, viewer_win=None)
    qtbot.addWidget(dlg)

    assert dlg.windowType() == Qt.Tool
    _assert_centred(dlg)


def test_export_images_dialog_is_freestanding_and_centred(qtbot, tmp_path):
    from percell4.gui.export_images_dialog import ExportImagesDialog

    store = _make_store(tmp_path / "ds.h5")
    dlg = ExportImagesDialog(parent=None, store=store)
    qtbot.addWidget(dlg)

    assert dlg.windowType() == Qt.Tool
    _assert_centred(dlg)


def test_configure_pair_dialog_sizes_itself_before_centring(qtbot, tmp_path):
    """This dialog set only a minimum width and never called resize().

    Centring a zero-height frame would put its title bar above the top
    edge -- the exact symptom the change exists to remove -- so U2 gave
    it an explicit size first.

    Both stores must be readable: this constructor raises a blocking
    ``QMessageBox.warning`` per unreadable path, which would hang the
    suite rather than fail it.
    """
    from percell4.gui.flim_fret_dialog import _ConfigurePairDialog, _PairConfig

    donor = tmp_path / "donor.h5"
    da = tmp_path / "da.h5"
    _make_store(donor)
    _make_store(da)

    parent = QMainWindow()
    qtbot.addWidget(parent)
    parent.show()
    dlg = _ConfigurePairDialog(
        parent,
        donor_path=donor,
        da_path=da,
        single_cell=False,
        initial=_PairConfig(),
    )
    qtbot.addWidget(dlg)

    assert dlg.windowType() == Qt.Tool
    assert dlg.height() > 0
    avail = parent.screen().availableGeometry()
    assert dlg.frameGeometry().y() >= avail.y()


# ── KTD2: nested popups are freed without being edited ───────────────


def test_nested_message_box_is_untouched_but_freed_by_its_parent(qtbot):
    """A popup raised by a converted dialog keeps the stock dialog type.

    KTD2: mutter also checks the *parent's* type, so a UTILITY parent
    frees its modal children with no edit to their call sites. That is
    why roughly forty nested calls in ``src/`` need no change, and why
    AE5 can promise the message box is draggable anyway. The freeing
    itself is a window-manager behaviour and is verified manually in U7.
    """
    from percell4.gui.compress_dialog import CompressDialog

    converted = CompressDialog()
    qtbot.addWidget(converted)
    assert converted.windowType() == Qt.Tool

    nested = QMessageBox(converted)
    qtbot.addWidget(nested)

    assert nested.windowType() == Qt.Dialog
    assert nested.parent() is converted
