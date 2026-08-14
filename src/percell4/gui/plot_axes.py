"""Axis helpers for pyqtgraph plots.

Every axis in this application is unitless — G, S, CNR, intensity, counts. None
of them should ever have an SI prefix applied, and pyqtgraph's own switch for
that does not reliably turn it off.
"""

from __future__ import annotations

from typing import Any


def disable_si_prefix(*axes: Any) -> None:
    """Turn off SI-prefix scaling on one or more pyqtgraph ``AxisItem``s.

    ``AxisItem.enableAutoSIPrefix(False)`` is not sufficient on its own. In
    pyqtgraph 0.14 it sets the flag and then *unconditionally* calls
    ``updateAutoSIPrefix()``, which computes a scale factor from the axis's
    current range and applies it. Disabling the feature therefore bakes in
    whatever scaling was appropriate at that instant, and because the flag is
    now off nothing ever recomputes it.

    That makes the result depend on call order, which is how this surfaced. An
    axis spanning 0 to 0.7:

    * ``setYRange(0, 0.7)`` then ``enableAutoSIPrefix(False)`` -> the range is
      already 0.7 when the scale is computed, ``siScale`` returns
      ``(1000.0, 'm')``, and ticks render as 0, 250, 500 instead of 0, 0.25, 0.5.
    * ``enableAutoSIPrefix(False)`` then ``setYRange(0, 0.7)`` -> the scale is
      computed against the default range of 0 to 1, stays 1.0, and the later
      range change never recomputes it because the flag is off.

    The second ordering is correct only by accident. This helper forces the
    scale and prefix to their identity values afterwards, so the outcome is the
    same whichever order the caller uses.

    pyqtgraph's docstring says the feature "is only available when a suffix
    (unit string) is provided", but 0.14's implementation no longer checks
    ``labelUnits`` — an axis with no units is scaled anyway. That is why this
    has to be corrected rather than avoided by leaving units unset.
    """
    for axis in axes:
        axis.enableAutoSIPrefix(False)
        axis.autoSIPrefixScale = 1.0
        axis.labelUnitPrefix = ""
        axis._updateLabel()
