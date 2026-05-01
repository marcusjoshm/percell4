"""Regression test for Anchor Bug B — `M` shadowed by napari's Labels keymap.

Origin: ``docs/brainstorms/2026-05-01-gui-state-handling-audit-requirements.md``
(Bug B); plan unit U7 in
``docs/plans/2026-05-01-refactor-gui-state-handling-audit-plan.md``.

Acceptance Example AE2: After Load Dataset, pressing ``M`` on the napari
viewer immediately opens the Multi-select dialog. The native napari
"next-label" behavior does not fire.

This test must be **red** on ``main`` (before U11 lands) and **green** after
U11 binds ``M`` via napari's keymap chain instead of as a launcher-window
``QAction.setShortcut("M")``.

Keystroke delivery follows napari's own internal test recipe at
``napari/_tests/test_key_bindings.py:45``:

    canvas._scene_canvas.events.key_press(key=keys.Key('M'))

Qt-level injections (``qtbot.keyClick(window, Qt.Key_M)``) do **not** exercise
napari's keymap chain — napari routes through vispy scene-canvas events, not
top-level Qt key events.
"""

from __future__ import annotations

import numpy as np
import pytest
from vispy import keys

from percell4.application.session import Event, Session
from percell4.gui.multi_select import _OVERLAY_LAYER_NAME
from percell4.gui.viewer import (
    LAYER_TYPE_MASK,
    LAYER_TYPE_SEGMENTATION,
    PERCELL_TYPE_KEY,
    ViewerWindow,
)
from percell4.interfaces.gui.main_window import LauncherWindow
from percell4.model import CellDataModel


# ──────────────────────────────────────────────────────────────────────
# Fixture — real Session + CellDataModel + ViewerWindow + LauncherWindow.
#
# Mirrors ``tests/test_gui_workflows/test_multi_select_e2e.py:43-69`` but
# uses a real ``LauncherWindow`` (not ``_FakeLauncher``) so that the real
# ``_on_multi_select`` slot is wired, and pre-populates
# ``launcher._windows["viewer"]`` with the test's ``ViewerWindow`` so the
# launcher does not construct a second one.
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def harness(qtbot, sample_labels: np.ndarray):
    """Real LauncherWindow + ViewerWindow with seg labels and a mask
    labels layer + an image layer. Yields ``(session, data_model, launcher,
    viewer_win, layers)`` where ``layers`` is a dict by role.

    The labels layers are added with ``percell_type`` metadata so
    ``_get_active_labels_layer`` recognises them.
    """
    session = Session()
    data_model = CellDataModel(session=session)
    launcher = LauncherWindow(data_model)
    qtbot.addWidget(launcher)

    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()
    viewer = viewer_win.viewer

    # Pre-populate the launcher's window registry so its
    # ``get_viewer_window()`` returns *this* viewer_win — otherwise the
    # launcher would lazily build a second ViewerWindow and our scene-canvas
    # injection would target the wrong one.
    launcher._windows["viewer"] = viewer_win

    # Image layer (mNG) — for scenario 3 (image active, no labels in scope).
    rng = np.random.default_rng(42)
    image_data = rng.random(sample_labels.shape, dtype=np.float32)
    image_layer = viewer.add_image(image_data, name="mNG")

    # Segmentation labels layer (default active for AE2 happy path).
    seg_layer = viewer.add_labels(
        sample_labels,
        name="seg",
        metadata={PERCELL_TYPE_KEY: LAYER_TYPE_SEGMENTATION},
    )
    data_model.set_active_segmentation("seg")

    # Mask labels layer.
    mask_data = (sample_labels > 0).astype(np.int32)
    mask_layer = viewer.add_labels(
        mask_data,
        name="mask",
        metadata={PERCELL_TYPE_KEY: LAYER_TYPE_MASK},
    )

    layers = {
        "image": image_layer,
        "seg": seg_layer,
        "mask": mask_layer,
    }

    yield session, data_model, launcher, viewer_win, layers

    # Teardown — close the napari viewer first, then the launcher.
    try:
        viewer.close()
    except Exception:
        pass
    try:
        launcher.close()
    except Exception:
        pass


def _press_m(viewer, qtbot) -> None:
    """Inject a key_press 'M' on the napari scene canvas and flush the
    event loop so any single-shot QTimers fire.

    This is napari's documented internal test path
    (``napari/_tests/test_key_bindings.py``). Equivalent to a real user
    keystroke routed through vispy → napari's keymap chain.
    """
    canvas = viewer.window._qt_viewer.canvas
    canvas._scene_canvas.events.key_press(key=keys.Key("M"))
    qtbot.wait(50)


def _multi_select_dialog_open(viewer_win: ViewerWindow) -> bool:
    """The Multi-select controller is alive (held on the viewer window to
    prevent GC) and the staged-overlay layer is in the layer list."""
    if viewer_win._multi_select_controller is None:
        return False
    return _OVERLAY_LAYER_NAME in viewer_win.viewer.layers


# ──────────────────────────────────────────────────────────────────────
# Scenarios 1–3: M opens Multi-select regardless of which layer is active.
# Each variant must FAIL on `main` (label increments instead) and PASS
# after U11 lands.
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "active_role",
    ["mask", "seg", "image"],
    ids=["mask-active", "seg-active", "image-active"],
)
def test_m_opens_multi_select_regardless_of_active_layer(
    harness, qtbot, active_role: str
) -> None:
    """AE2: pressing M on the napari viewer opens Multi-select for every
    active-layer variant. The native napari "next-label" binding (max+1)
    must not fire on the seg layer."""
    session, data_model, launcher, viewer_win, layers = harness

    viewer = viewer_win.viewer
    target_layer = layers[active_role]
    viewer.layers.selection.active = target_layer
    qtbot.wait(20)

    # Capture every labels layer's ``selected_label`` so we can prove
    # napari's native ``new_label`` (max+1) binding did not fire on
    # whichever layer was active. sample_labels has max=5, so native M on
    # the seg layer would push selected_label to 6; on the mask layer
    # (binary, max=1) M would push it to 2.
    before = {
        name: int(layers[name].selected_label) for name in ("seg", "mask")
    }

    _press_m(viewer, qtbot)

    # Assertion 1 — Multi-select dialog opened.
    assert _multi_select_dialog_open(viewer_win), (
        f"[{active_role}-active] Pressing M did not open Multi-select. "
        f"Either the launcher's QAction shortcut never reached the canvas "
        f"(today's bug) or U11's keymap rebinding does not cover this "
        f"active-layer variant."
    )

    # Assertion 2 — napari's native new_label did not run on any labels
    # layer. (Native M only fires on the *active* labels layer; checking
    # every labels layer ensures the assertion is meaningful no matter
    # which one was active.)
    after = {
        name: int(layers[name].selected_label) for name in ("seg", "mask")
    }
    assert after == before, (
        f"[{active_role}-active] napari's native 'next-label' binding fired. "
        f"selected_label went from {before} to {after}. The M keystroke "
        f"leaked through napari's keymap chain instead of being claimed by "
        f"PerCell4's Multi-select handler."
    )


# ──────────────────────────────────────────────────────────────────────
# Scenario 4: precondition I4 — no labels layer at all.
# Multi-select must refuse with explicit status-bar feedback.
# ──────────────────────────────────────────────────────────────────────


def test_m_with_no_labels_layer_surfaces_status_message(
    qtbot, sample_labels: np.ndarray
) -> None:
    """When no labels layer exists at all, pressing M must NOT silently
    fail. The launcher's status bar must show a per-cause message
    (Invariant I4). The native napari binding must not fire either (no
    labels layer means there is nothing for ``new_label`` to act on)."""
    session = Session()
    data_model = CellDataModel(session=session)
    launcher = LauncherWindow(data_model)
    qtbot.addWidget(launcher)

    viewer_win = ViewerWindow(data_model)
    viewer_win._ensure_viewer()
    viewer = viewer_win.viewer
    launcher._windows["viewer"] = viewer_win

    # Image-only scene — no labels layer at all.
    rng = np.random.default_rng(0)
    image_data = rng.random(sample_labels.shape, dtype=np.float32)
    img_layer = viewer.add_image(image_data, name="mNG")
    viewer.layers.selection.active = img_layer
    qtbot.wait(20)

    try:
        _press_m(viewer, qtbot)

        # Multi-select must not have opened — there is no labels layer.
        assert viewer_win._multi_select_controller is None or not (
            _OVERLAY_LAYER_NAME in viewer.layers
        )

        # Launcher's status bar must convey the per-cause refusal.
        # The exact text comes from U11's implementation — we use a fuzzy
        # substring match keyed on the I4 contract.
        status = launcher.statusBar().currentMessage().lower()
        assert (
            "multi-select" in status or "labels" in status
        ), (
            f"Expected per-cause status message about Multi-select / labels "
            f"layer; got: {launcher.statusBar().currentMessage()!r}"
        )
    finally:
        try:
            viewer.close()
        except Exception:
            pass
        try:
            launcher.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# Scenario 5 (integration — Accept after staging): DEFERRED.
#
# The full keystroke-driven Accept flow (M → toggle cells via canvas mouse
# events → Accept → assert ``session.filter_ids`` and a single
# ``state_changed.filter`` emission) requires injecting mouse events with
# precise scene-canvas coordinates. ``test_multi_select_e2e.py`` already
# exercises the toggle/accept path by calling ``ctrl.toggle(...)`` and
# ``ctrl.accept()`` directly; the keystroke-routing concern this file owns
# is satisfied by scenarios 1–3.
#
# If a regression in the Accept path is suspected, see
# ``test_multi_select_e2e.py::test_stage_and_accept_commits_selection``.
#
# (Plan U7 explicitly marks this scenario optional and points back to
# ``test_multi_select_e2e.py``.)
# ──────────────────────────────────────────────────────────────────────


def test_accept_path_covered_by_existing_e2e_test_marker() -> None:
    """Documentation marker: the keystroke→toggle→Accept integration is
    deferred to ``test_multi_select_e2e.py``. Kept here as a discoverable
    breadcrumb so a future contributor doesn't duplicate that coverage."""
    # Intentional no-op — see module docstring.
    assert True
