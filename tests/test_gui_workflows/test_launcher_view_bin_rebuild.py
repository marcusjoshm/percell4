"""Tests for ViewerWindow rebuild on session bin toggle (U11).

The launcher owns ``_populate_viewer_from_store`` and the
``_on_state_changed`` branch that rebuilds the viewer when
``session.active_bin`` toggles. Use a stub ViewerWindow so the test
runs without a real napari Viewer; LauncherWindow construction needs a
real CellDataModel + Session.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


class _StubViewer:
    """Minimal ViewerWindow stub that captures add_* calls."""

    def __init__(self) -> None:
        self._alive = True
        self._is_originator = False
        # Track calls during rebuild so tests can assert ordering and
        # reentry-guard discipline.
        self.image_calls: list[tuple[np.ndarray, str]] = []
        self.labels_calls: list[tuple[np.ndarray, str]] = []
        self.mask_calls: list[tuple[np.ndarray, str]] = []
        self.cleared = 0
        # Originator snapshot at each add_* call -- True throughout rebuild,
        # False during the initial populate before any toggle.
        self.originator_during_calls: list[bool] = []
        self.push_active_calls: list[tuple[str, str]] = []
        # Faux viewer so the data_panel's
        # refresh_management_combos check (`viewer_win.viewer is not None`)
        # passes; we also alias _viewer for consistency with the real API.
        self.viewer = None  # acts like "no napari viewer alive" for data_panel
        self._viewer = self.viewer

    def _is_alive(self) -> bool:
        return self._alive

    def clear(self) -> None:
        self.cleared += 1

    def add_image(self, data, name: str, **kwargs) -> None:
        self.image_calls.append((data, name))
        self.originator_during_calls.append(self._is_originator)

    def add_labels(self, data, name: str, **kwargs) -> None:
        self.labels_calls.append((data, name))
        self.originator_during_calls.append(self._is_originator)

    def add_mask(self, data, name: str, **kwargs) -> None:
        self.mask_calls.append((data, name))
        self.originator_during_calls.append(self._is_originator)

    def _push_active_layer_to_napari(self, name: str, percell_type: str) -> None:
        self.push_active_calls.append((name, percell_type))

    def close(self) -> None:
        self._alive = False


@pytest.fixture
def real_store(tmp_path):
    """A real DatasetStore with a 4x4 intensity, one label, one mask."""
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_path / "ds.h5")
    store.create(metadata={"channel_names": ["ch00"]})
    # Use a 4x4 intensity so view_bin=2 yields a clean 2x2.
    intensity = np.full((4, 4), 10.0, dtype=np.float32)
    store.write_array("intensity", intensity, attrs={"dims": ["H", "W"]})
    labels = np.zeros((4, 4), dtype=np.int32)
    labels[0:2, 0:2] = 1
    store.write_labels("seg1", labels)
    mask = np.zeros((4, 4), dtype=np.uint8)
    mask[2:, 2:] = 1
    store.write_mask("mask1", mask)
    return store


@pytest.fixture
def launcher_with_stub_viewer(qtbot, real_store):
    """Build a real LauncherWindow + a stub ViewerWindow."""
    from percell4.application.session import Session
    from percell4.domain.dataset import DatasetHandle
    from percell4.interfaces.gui.main_window import LauncherWindow
    from percell4.model import CellDataModel

    handle = DatasetHandle(
        path=real_store.path,
        metadata={
            "channel_names": ["ch00"],
            "segmentation_names": ["seg1"],
            "mask_names": ["mask1"],
            "native_shape": (4, 4),
            "creation_bin": 1,
        },
    )
    session = Session()
    session.set_dataset(handle)
    model = CellDataModel(session=session)
    win = LauncherWindow(model)
    qtbot.addWidget(win)
    stub = _StubViewer()
    win._windows["viewer"] = stub
    win._current_store = real_store
    win._current_h5_path = str(real_store.path)
    return win, stub, session


# ── view_bin threading through _populate_viewer_from_store ─────────


def test_populate_default_uses_session_active_bin(launcher_with_stub_viewer):
    """When view_bin is omitted, populate reads session.active_bin (=1)."""
    win, stub, _session = launcher_with_stub_viewer
    win._populate_viewer_from_store()
    assert stub.cleared == 1
    # Default k=1: intensity at native 4x4.
    assert stub.image_calls[0][0].shape == (4, 4)
    assert stub.labels_calls[0][0].shape == (4, 4)
    assert stub.mask_calls[0][0].shape == (4, 4)


def test_populate_with_view_bin_2_downsamples_every_layer(launcher_with_stub_viewer):
    """view_bin=2 on a 4x4 dataset reads every layer at 2x2."""
    win, stub, _session = launcher_with_stub_viewer
    win._populate_viewer_from_store(view_bin=2)
    assert stub.image_calls[0][0].shape == (2, 2)
    assert stub.labels_calls[0][0].shape == (2, 2)
    assert stub.mask_calls[0][0].shape == (2, 2)


def test_populate_intensity_uses_sum_bin_rule(launcher_with_stub_viewer):
    """Intensity is sum-binned (preserves photon counts)."""
    win, stub, _session = launcher_with_stub_viewer
    win._populate_viewer_from_store(view_bin=2)
    # Every 2x2 source block of 10s sums to 40.
    np.testing.assert_allclose(stub.image_calls[0][0], np.full((2, 2), 40.0))


# ── Rebuild on change.bin ──────────────────────────────────────────


def test_session_active_bin_toggle_triggers_rebuild(launcher_with_stub_viewer):
    """Setting session.active_bin = 2 fires the launcher's change.bin
    handler which calls _populate_viewer_from_store(view_bin=2)."""
    win, stub, session = launcher_with_stub_viewer
    # Initial populate sets baseline.
    win._populate_viewer_from_store()
    initial_image_count = len(stub.image_calls)

    session.set_active_bin(2)

    # Another populate fired with view_bin=2.
    assert len(stub.image_calls) == initial_image_count + 1
    assert stub.image_calls[-1][0].shape == (2, 2)


def test_rebuild_sets_originator_during_add_calls(launcher_with_stub_viewer):
    """All add_* calls during a bin-triggered rebuild see _is_originator=True
    so napari layer-list callbacks can't write back to Session."""
    win, stub, session = launcher_with_stub_viewer
    win._populate_viewer_from_store()  # initial, originator=False
    initial_count = len(stub.originator_during_calls)

    session.set_active_bin(2)

    # Every add_* call after the toggle saw _is_originator=True.
    rebuild_calls = stub.originator_during_calls[initial_count:]
    assert rebuild_calls  # at least one call happened
    assert all(rebuild_calls)


def test_rebuild_restores_originator_to_false_afterwards(launcher_with_stub_viewer):
    """The _is_originator flag is reset to False after the rebuild
    completes, regardless of internal failures."""
    win, stub, session = launcher_with_stub_viewer
    session.set_active_bin(2)
    assert stub._is_originator is False


def test_rebuild_restores_active_mask_via_napari_push(launcher_with_stub_viewer):
    """After the rebuild, the active mask is pushed back into napari
    selection -- NOT via session.set_active_mask (which would cascade)."""
    win, stub, session = launcher_with_stub_viewer
    session.set_active_mask("mask1")
    win._populate_viewer_from_store()  # ensure initial load
    stub.push_active_calls.clear()

    session.set_active_bin(2)

    # The bin-rebuild routine pushed the active mask back.
    names = [n for n, _ in stub.push_active_calls]
    assert "mask1" in names


def test_rebuild_restores_active_segmentation_via_napari_push(
    launcher_with_stub_viewer,
):
    """Active segmentation gets the same direct-push treatment as masks."""
    win, stub, session = launcher_with_stub_viewer
    session.set_active_segmentation("seg1")
    win._populate_viewer_from_store()
    stub.push_active_calls.clear()

    session.set_active_bin(2)

    names = [n for n, _ in stub.push_active_calls]
    assert "seg1" in names


def test_rebuild_does_not_cascade_session_writes(launcher_with_stub_viewer):
    """The reentry guard: rebuild MUST NOT call session.set_active_*,
    which would re-emit ACTIVE_*_CHANGED -> state_changed -> back into
    this handler. One bin toggle produces exactly ONE rebuild."""
    win, stub, session = launcher_with_stub_viewer
    session.set_active_mask("mask1")
    session.set_active_segmentation("seg1")
    win._populate_viewer_from_store()
    cleared_baseline = stub.cleared

    session.set_active_bin(3)

    # cleared incremented by exactly 1 (one rebuild = one clear).
    assert stub.cleared == cleared_baseline + 1


def test_rebuild_noop_when_viewer_not_alive(launcher_with_stub_viewer):
    """If the viewer was closed, the rebuild is a no-op."""
    win, stub, session = launcher_with_stub_viewer
    stub.close()  # _alive = False
    # Should not raise.
    session.set_active_bin(2)
    assert stub.cleared == 0  # no rebuild ran


def test_rebuild_via_method_returns_to_native_on_set_active_bin_one(
    launcher_with_stub_viewer,
):
    """Toggling back to k=1 rebuilds at native (4x4)."""
    win, stub, session = launcher_with_stub_viewer
    win._populate_viewer_from_store()
    session.set_active_bin(3)
    session.set_active_bin(1)

    # Last rebuild was at k=1 -> native shape.
    assert stub.image_calls[-1][0].shape == (4, 4)
