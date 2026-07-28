"""Keep PyOpenGL's GL backend in sync with the one Qt actually chose.

PyOpenGL freezes its platform plugin the first time it is imported: on Linux
it uses GLX, *except* that it switches to EGL whenever ``WAYLAND_DISPLAY`` is
set in the environment. Qt makes its own, independent choice — and on GNOME,
Qt5 deliberately ignores a Wayland session and runs on XWayland (``xcb``),
which hands napari/vispy a GLX context::

    Warning: Ignoring XDG_SESSION_TYPE=wayland on Gnome.

When the two disagree, every PyOpenGL call that needs the current context asks
EGL for a context that only GLX knows about, and fails with::

    OpenGL.error.Error: Attempt to retrieve context when no valid context

napari surfaces that as a storm of "Error drawing visual" tracebacks and a
canvas that never paints — which is how this shows up when loading a dataset.

Pinning ``PYOPENGL_PLATFORM`` to match the platform Qt reports keeps the two in
agreement. It must be set before anything imports ``OpenGL``, so the entry
points call :func:`configure_pyopengl_platform` right after building the
``QApplication`` (which is what makes Qt commit to a platform) and before the
napari/vispy imports.
"""

from __future__ import annotations

import os
import sys
import warnings

__all__ = ["configure_pyopengl_platform", "pyopengl_platform_for"]


def pyopengl_platform_for(
    qt_platform_name: str,
    *,
    wayland_display: str | None,
) -> str | None:
    """Return the ``PYOPENGL_PLATFORM`` value needed to match Qt's backend.

    ``None`` means PyOpenGL's own autodetection already agrees with Qt and the
    environment should be left alone.

    Args:
        qt_platform_name: What ``QApplication.platformName()`` reports, e.g.
            ``"xcb"``, ``"wayland"``, ``"wayland-egl"``, ``"offscreen"``.
        wayland_display: The ``WAYLAND_DISPLAY`` environment variable, which is
            the sole trigger for PyOpenGL choosing EGL over GLX.
    """
    name = qt_platform_name.lower()
    autodetect_picks_egl = bool(wayland_display)

    if name == "xcb":
        # Qt is on X11/XWayland, so the context is GLX. A leftover
        # WAYLAND_DISPLAY would otherwise push PyOpenGL to EGL.
        return "glx" if autodetect_picks_egl else None

    if name.startswith("wayland"):
        # Qt is on a native Wayland surface, so the context is EGL.
        return None if autodetect_picks_egl else "egl"

    # offscreen, minimal, eglfs, vnc, … — no on-screen GL context to match, or
    # a backend whose pairing we can't infer. Leave PyOpenGL's default alone.
    return None


def configure_pyopengl_platform(app) -> str | None:
    """Pin ``PYOPENGL_PLATFORM`` to match the GL backend ``app`` is using.

    A no-op off Linux, when the user has set ``PYOPENGL_PLATFORM`` themselves,
    or when PyOpenGL's default is already correct.

    Args:
        app: The live ``QApplication``. Must already exist — Qt only commits to
            a platform when the application object is constructed.

    Returns:
        The value written to the environment, or ``None`` if nothing changed.
    """
    if not sys.platform.startswith("linux"):
        return None

    # An explicit setting is the user telling us they know better.
    if os.environ.get("PYOPENGL_PLATFORM"):
        return None

    wanted = pyopengl_platform_for(
        app.platformName(),
        wayland_display=os.environ.get("WAYLAND_DISPLAY"),
    )
    if wanted is None:
        return None

    if "OpenGL.platform" in sys.modules:
        # PyOpenGL has already locked in its plugin; setting the variable now
        # would silently do nothing, so say so rather than look like a fix.
        warnings.warn(
            "OpenGL was imported before the Qt platform was known, so "
            f"PYOPENGL_PLATFORM could not be set to {wanted!r}. If the napari "
            "canvas fails to draw with 'Attempt to retrieve context when no "
            f"valid context', run PerCell4 with PYOPENGL_PLATFORM={wanted}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None

    os.environ["PYOPENGL_PLATFORM"] = wanted
    return wanted
