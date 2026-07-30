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

import pytest
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import QApplication, QDialog, QMainWindow, QMessageBox

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


def _build(module_path: str, class_name: str) -> QDialog:
    return getattr(importlib.import_module(module_path), class_name)()


def _assert_centred(dialog: QDialog) -> None:
    avail = dialog.screen().availableGeometry()
    centre = dialog.frameGeometry().center()
    # Offscreen synthesises a 2px frame per side. cap_to_screen may clamp a
    # dialog larger than the 800x600 offscreen screen, so allow the clamp
    # to win on either axis rather than asserting a hard centre.
    assert abs(centre.x() - avail.center().x()) <= 5 or (
        dialog.frameGeometry().x() <= avail.x() + 5
    )
    assert abs(centre.y() - avail.center().y()) <= 5 or (
        dialog.frameGeometry().y() <= avail.y() + 5
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
def test_dialog_opens_centred_on_screen(qtbot, module_path, class_name):
    """R2 -- placement is deliberate, not inherited from the parent."""
    dialog = _build(module_path, class_name)
    qtbot.addWidget(dialog)

    _assert_centred(dialog)


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
