"""Tests for src/percell4/gui/opengl_platform.py."""

from __future__ import annotations

import os
import sys

import pytest

from percell4.gui.opengl_platform import (
    configure_pyopengl_platform,
    pyopengl_platform_for,
)


class _FakeApp:
    """Stands in for QApplication -- only platformName() is consulted."""

    def __init__(self, name: str):
        self._name = name

    def platformName(self) -> str:  # noqa: N802 -- mirrors the Qt API
        return self._name


# ── pyopengl_platform_for ──────────────────────────────────────────


def test_xcb_under_wayland_session_forces_glx():
    """The reported bug: GNOME runs Qt5 on XWayland (a GLX context) while a
    live WAYLAND_DISPLAY pushes PyOpenGL to EGL."""
    assert pyopengl_platform_for("xcb", wayland_display="wayland-0") == "glx"


def test_xcb_on_plain_x11_needs_no_override():
    """Without WAYLAND_DISPLAY, PyOpenGL already defaults to GLX."""
    assert pyopengl_platform_for("xcb", wayland_display=None) is None
    assert pyopengl_platform_for("xcb", wayland_display="") is None


def test_native_wayland_needs_no_override():
    assert pyopengl_platform_for("wayland", wayland_display="wayland-0") is None
    assert (
        pyopengl_platform_for("wayland-egl", wayland_display="wayland-0") is None
    )


def test_native_wayland_without_display_var_forces_egl():
    assert pyopengl_platform_for("wayland", wayland_display=None) == "egl"


@pytest.mark.parametrize("name", ["offscreen", "minimal", "eglfs", "vnc"])
def test_non_windowing_platforms_are_left_alone(name):
    assert pyopengl_platform_for(name, wayland_display="wayland-0") is None


def test_platform_name_is_case_insensitive():
    assert pyopengl_platform_for("XCB", wayland_display="wayland-0") == "glx"


# ── configure_pyopengl_platform ────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_pyopengl_platform_env():
    """Undo any write to ``PYOPENGL_PLATFORM``.

    ``configure_pyopengl_platform`` writes straight to ``os.environ``, and
    monkeypatch registers no undo for a ``delenv`` of an already-absent
    variable. Without this, the value leaks into the rest of the session and
    switches PyOpenGL's backend under the napari GUI tests -- which segfaults
    the interpreter at teardown.
    """
    missing = object()
    original = os.environ.get("PYOPENGL_PLATFORM", missing)
    yield
    if original is missing:
        os.environ.pop("PYOPENGL_PLATFORM", None)
    else:
        os.environ["PYOPENGL_PLATFORM"] = original


@pytest.fixture
def linux_wayland_env(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("PYOPENGL_PLATFORM", raising=False)
    # The guard against a too-late call keys off this module being loaded.
    monkeypatch.delitem(sys.modules, "OpenGL.platform", raising=False)


def test_sets_environment_variable_on_gnome_wayland(linux_wayland_env):
    assert configure_pyopengl_platform(_FakeApp("xcb")) == "glx"
    assert os.environ["PYOPENGL_PLATFORM"] == "glx"


def test_explicit_user_setting_is_respected(linux_wayland_env, monkeypatch):
    monkeypatch.setenv("PYOPENGL_PLATFORM", "egl")
    assert configure_pyopengl_platform(_FakeApp("xcb")) is None
    assert os.environ["PYOPENGL_PLATFORM"] == "egl"


def test_no_op_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("PYOPENGL_PLATFORM", raising=False)
    assert configure_pyopengl_platform(_FakeApp("xcb")) is None
    assert "PYOPENGL_PLATFORM" not in os.environ


def test_warns_and_skips_if_opengl_already_imported(linux_wayland_env, monkeypatch):
    monkeypatch.setitem(sys.modules, "OpenGL.platform", object())
    with pytest.warns(RuntimeWarning, match="PYOPENGL_PLATFORM"):
        assert configure_pyopengl_platform(_FakeApp("xcb")) is None
    assert "PYOPENGL_PLATFORM" not in os.environ
