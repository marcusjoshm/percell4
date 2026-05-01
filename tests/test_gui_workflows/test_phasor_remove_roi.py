"""Regression test for Anchor Bug A — Phasor Remove must not corrupt mask state.

Background: clicking *Remove* on a phasor ROI used to silently call
``session.set_active_mask(None)`` (the off-label session write at
``peer_views/phasor_plot.py:360``). That clobbered the active-mask filter,
disabled the ``Filter by active mask`` checkbox, and left the Selected ROI
panel + status bar showing post-removal stale data.

The expected post-fix contract (origin doc Invariant I2):
1. ``session.active_mask`` is unchanged across Remove.
2. The ``Filter by active mask`` checkbox stays checked + enabled.
3. The Selected ROI panel widgets reset to defaults.
4. The status bar reverts to the standard no-ROI "Phasor: N valid pixels"
   message (or, with surviving ROIs, shows their per-ROI stats).

This test is intentionally written **red-first**: it must fail on ``main``
and pass after U8 lands. See
``docs/plans/2026-05-01-refactor-gui-state-handling-audit-plan.md`` U6.

The ``_phasor_roi_preview`` napari layer removal goes through the
``preview_mask_ready`` signal → napari subscriber chain. The fixture has no
real napari viewer, so layer-list state is **not** asserted here (per the
plan's explicit scope note).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.interfaces.gui.peer_views.phasor_plot import PhasorPlotWindow


# Standard "no per-ROI selection" status format emitted by _refresh_histogram.
_STD_STATUS_RE = re.compile(r"Phasor: \d[\d,]* valid pixels")


@pytest.fixture
def session_with_dataset(tmp_path) -> Session:
    """A Session with a fake DatasetHandle attached."""
    sess = Session()
    handle = DatasetHandle(path=tmp_path / "fake.h5", metadata={})
    sess._dataset = handle
    return sess


@pytest.fixture
def fake_repo() -> MagicMock:
    """A repo with read_mask() returning a 64x64 binary mask by default."""
    repo = MagicMock()
    repo.read_mask.return_value = np.ones((64, 64), dtype=np.uint8)
    return repo


@pytest.fixture
def phasor_window(qtbot, session_with_dataset, fake_repo) -> PhasorPlotWindow:
    """A PhasorPlotWindow wired to a fake session + repo, with phasor data set.

    Mirrors ``test_phasor_mask_filter.py``'s fixture, plus a 64x64 g/s/intensity
    map so ``_refresh_histogram`` produces a valid status message and the
    standard "Phasor: N valid pixels" format is reachable on ROI removal.
    """
    win = PhasorPlotWindow(session_with_dataset, get_repo=lambda: fake_repo)
    qtbot.addWidget(win)

    # Provide phasor data so _refresh_histogram emits the standard status.
    rng = np.random.default_rng(42)
    g_map = rng.uniform(0.1, 0.9, size=(64, 64)).astype(np.float32)
    s_map = rng.uniform(0.05, 0.5, size=(64, 64)).astype(np.float32)
    intensity = np.ones((64, 64), dtype=np.float32)
    win.set_phasor_data(g_map=g_map, s_map=s_map, intensity=intensity)
    return win


def _force_check_mask_filter(window: PhasorPlotWindow) -> None:
    """Engage the active-mask filter (mirrors the AE1 setup in the plan).

    The checkbox is enabled by ``_on_active_mask_changed`` when the session
    has an active mask name, but it never auto-engages — opt-in is
    load-bearing. This helper checks it explicitly.
    """
    assert window._mask_filter_check.isEnabled(), (
        "checkbox must be enabled before forcing it on"
    )
    if not window._mask_filter_check.isChecked():
        window._mask_filter_check.setChecked(True)


# ── Happy path (Covers AE1) ─────────────────────────────────────


def test_remove_roi_preserves_active_mask_and_panel_state(
    qtbot, phasor_window, session_with_dataset
):
    """Bug A: Remove on a single ROI must not corrupt mask state.

    AE1 sequence: active mask set, mask-filter checkbox engaged, one ROI
    added. Clicking Remove must:
      - leave ``session.active_mask`` unchanged
      - keep ``_mask_filter_check`` checked AND enabled
      - clear ``_name_edit``
      - restore the standard "Phasor: N valid pixels" status format
    """
    # Setup: active mask + engaged mask filter
    session_with_dataset.set_active_mask("nucleus")
    _force_check_mask_filter(phasor_window)
    assert session_with_dataset.active_mask == "nucleus"
    assert phasor_window._mask_filter_check.isChecked()

    # Add one ROI and let single-shot timers (preview_timer at 100ms) flush
    phasor_window._on_add_roi()
    qtbot.wait(200)  # let _preview_timer (100ms) and any debounces drain
    assert len(phasor_window._roi_widgets) == 1
    assert phasor_window._selected_roi_index == 0

    # Verify the Selected ROI panel reflects the just-added ROI before Remove
    assert phasor_window._name_edit.text() == "ROI_1"

    # Action: click Remove
    phasor_window._on_remove_roi()
    qtbot.wait(200)  # flush any timers fired by the rebind chain

    # Assertions (the five red-first checks from the plan)
    # 1) session.active_mask preserved (THIS is Bug A's smoking-gun assertion)
    assert session_with_dataset.active_mask == "nucleus", (
        "Remove must not call set_active_mask(None) — that's the off-label "
        "session write Bug A is about"
    )
    # 2) checkbox still checked
    assert phasor_window._mask_filter_check.isChecked(), (
        "mask-filter checkbox must stay checked across Remove"
    )
    # 3) checkbox still enabled (it tracks active_mask presence)
    assert phasor_window._mask_filter_check.isEnabled(), (
        "checkbox must stay enabled because active_mask is still set"
    )
    # 4) Selected ROI panel cleared (subscriber-side rebind)
    assert phasor_window._name_edit.text() == "", (
        "Selected ROI name field must clear when the selected ROI is removed"
    )
    # 5) Status bar reverts to standard no-ROI format
    status_text = phasor_window._status.currentMessage()
    assert _STD_STATUS_RE.search(status_text), (
        f"status bar must show standard 'Phasor: N valid pixels' format "
        f"after Remove, got: {status_text!r}"
    )


# ── Edge case: Remove with no ROI present ───────────────────────


def test_remove_with_no_roi_is_noop(qtbot, phasor_window, session_with_dataset):
    """Calling Remove with no ROI present must not raise or change state."""
    session_with_dataset.set_active_mask("nucleus")
    _force_check_mask_filter(phasor_window)

    # Capture pre-state
    assert phasor_window._roi_widgets == []
    pre_status = phasor_window._status.currentMessage()
    pre_active_mask = session_with_dataset.active_mask
    pre_checked = phasor_window._mask_filter_check.isChecked()
    pre_enabled = phasor_window._mask_filter_check.isEnabled()

    # Action: Remove with no ROI — must be a silent no-op
    phasor_window._on_remove_roi()
    qtbot.wait(50)

    # Nothing changed
    assert phasor_window._roi_widgets == []
    assert session_with_dataset.active_mask == pre_active_mask
    assert phasor_window._mask_filter_check.isChecked() == pre_checked
    assert phasor_window._mask_filter_check.isEnabled() == pre_enabled
    assert phasor_window._status.currentMessage() == pre_status


# ── Edge case: Remove with two ROIs present ─────────────────────


def test_remove_with_two_rois_keeps_other_roi(
    qtbot, phasor_window, session_with_dataset
):
    """With two ROIs, Remove deletes only the selected one.

    The surviving ROI's per-ROI stats remain in the status bar (NOT the
    standard "Phasor: N valid pixels" format), and ``session.active_mask``
    is unchanged.
    """
    session_with_dataset.set_active_mask("nucleus")
    _force_check_mask_filter(phasor_window)

    # Add two ROIs; second is auto-selected by _on_add_roi
    phasor_window._on_add_roi()
    phasor_window._on_add_roi()
    qtbot.wait(250)  # let preview timers drain so _update_preview writes status
    assert len(phasor_window._roi_widgets) == 2
    assert phasor_window._selected_roi_index == 1
    surviving_name = phasor_window._roi_widgets[0].phasor_roi.name  # "ROI_1"

    # Action: Remove the second (currently selected) ROI
    phasor_window._on_remove_roi()
    qtbot.wait(250)  # let preview/filter timers from rebind drain

    # session.active_mask unchanged
    assert session_with_dataset.active_mask == "nucleus"

    # First ROI still present
    assert len(phasor_window._roi_widgets) == 1
    assert phasor_window._roi_widgets[0].phasor_roi.name == surviving_name

    # Status bar shows the surviving ROI's stats — not the standard format.
    # _update_preview writes "<name>: <count> (<pct>%)" via the bincount path.
    status_text = phasor_window._status.currentMessage()
    assert not _STD_STATUS_RE.fullmatch(status_text), (
        f"with a surviving ROI, status must show per-ROI stats, "
        f"not the standard format; got: {status_text!r}"
    )
    assert surviving_name in status_text, (
        f"surviving ROI's name must appear in status; got: {status_text!r}"
    )
