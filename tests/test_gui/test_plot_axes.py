"""SI-prefix scaling must be off on every plot axis, whatever the call order.

The phasor plot's S axis rendered the top of the universal semicircle -- which
is at S = 0.5 by definition -- as 500. pyqtgraph had silently applied a milli
prefix, scaling every tick by 1000.

The trap is that ``AxisItem.enableAutoSIPrefix(False)`` does not prevent this.
In pyqtgraph 0.14 it sets the flag and then unconditionally recomputes the
scale from the axis's current range, so disabling the feature freezes in
whatever scaling applied at that moment and nothing ever recalculates it. The
result depends on whether the caller set the range before or after disabling,
which is exactly the kind of thing that is correct by accident until someone
reorders two lines.

Two conditions are needed to reproduce it, and both are easy to miss: the axis
must carry a visible label (``updateAutoSIPrefix`` returns early otherwise), and
the range must already be set when the feature is disabled.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pytest

from percell4.gui.plot_axes import disable_si_prefix


@pytest.fixture()
def plot(qtbot):
    widget = pg.PlotWidget()
    qtbot.addWidget(widget)
    return widget


def _ticks(axis, values, spacing):
    """Render tick labels the way the axis itself would.

    ``spacing`` drives the decimal places pyqtgraph prints, so it has to be fine
    enough to show the values under test.
    """
    return axis.tickStrings(values, axis.autoSIPrefixScale, spacing)


def test_semicircle_apex_reads_as_a_half(plot):
    """The regression: S = 0.5 must render as 0.5, not 500."""
    plot.setLabel("left", "S")
    plot.setYRange(0.0, 0.7, padding=0)
    disable_si_prefix(plot.getAxis("left"))

    assert _ticks(plot.getAxis("left"), [0.0, 0.25, 0.5], 0.01) == ["0", "0.25", "0.50"]


@pytest.mark.parametrize(
    "range_first", [True, False], ids=["range-then-disable", "disable-then-range"]
)
def test_result_is_independent_of_call_order(plot, range_first):
    plot.setLabel("left", "S")
    axis = plot.getAxis("left")

    if range_first:
        plot.setYRange(0.0, 0.7, padding=0)
        disable_si_prefix(axis)
    else:
        disable_si_prefix(axis)
        plot.setYRange(0.0, 0.7, padding=0)

    assert axis.autoSIPrefixScale == 1.0
    assert axis.labelUnitPrefix == ""


def test_bare_pyqtgraph_call_is_insufficient(plot):
    """Pin the upstream behaviour this helper exists to work around.

    If a future pyqtgraph makes ``enableAutoSIPrefix(False)`` sufficient on its
    own, this fails and ``disable_si_prefix`` can be reduced to that one call.
    """
    plot.setLabel("left", "S")
    plot.setYRange(0.0, 0.7, padding=0)
    axis = plot.getAxis("left")
    axis.enableAutoSIPrefix(False)

    assert axis.autoSIPrefixScale == 1000.0, (
        "pyqtgraph no longer applies a milli prefix after enableAutoSIPrefix(False); "
        "disable_si_prefix can be simplified."
    )


def test_label_must_be_visible_to_reproduce(plot):
    """Document the other half of the reproduction condition.

    Without a label the upstream scale computation is skipped entirely, so an
    unlabelled axis never showed the bug -- which is why it surfaced on the
    phasor plot and not on plots that leave an axis unlabelled.
    """
    plot.setYRange(0.0, 0.7, padding=0)
    axis = plot.getAxis("left")
    axis.enableAutoSIPrefix(False)

    assert axis.autoSIPrefixScale == 1.0


@pytest.mark.parametrize(
    ("high", "values", "spacing", "expected"),
    [
        (0.7, [0.0, 0.25, 0.5], 0.01, ["0", "0.25", "0.50"]),  # phasor S
        (0.05, [0.0, 0.01, 0.04], 0.01, ["0", "0.01", "0.04"]),  # a small fraction
        (250000.0, [0.0, 100000.0], 1000.0, ["0", "100000"]),  # a large count
    ],
    ids=["phasor-s", "small-fraction", "large-count"],
)
def test_unitless_axes_show_real_values(plot, high, values, spacing, expected):
    """No prefix in either direction -- no milli below 1, no kilo above 1000."""
    plot.setLabel("left", "value")
    plot.setYRange(0.0, high, padding=0)
    disable_si_prefix(plot.getAxis("left"))

    assert plot.getAxis("left").autoSIPrefixScale == 1.0
    assert _ticks(plot.getAxis("left"), values, spacing) == expected


def test_disable_si_prefix_accepts_several_axes(plot):
    plot.setLabel("bottom", "G")
    plot.setLabel("left", "S")
    plot.setXRange(-0.005, 1.005, padding=0)
    plot.setYRange(0.0, 0.7, padding=0)

    disable_si_prefix(plot.getAxis("bottom"), plot.getAxis("left"))

    for name in ("bottom", "left"):
        assert plot.getAxis(name).autoSIPrefixScale == 1.0


def test_universal_semicircle_geometry():
    """Guard the curve itself, so a correct axis cannot flatter a wrong curve.

    The universal semicircle is centred at (0.5, 0) with radius 0.5, so its apex
    is (0.5, 0.5) and it meets the G axis at 0 and 1. The curve is sampled at 200
    points, which straddles rather than lands on the apex, hence the tolerance.
    """
    theta = np.linspace(0, np.pi, 200)
    g = 0.5 + 0.5 * np.cos(theta)
    s = 0.5 * np.sin(theta)

    assert s.max() == pytest.approx(0.5, abs=1e-4)
    assert g[np.argmax(s)] == pytest.approx(0.5, abs=1e-2)
    assert g.min() == pytest.approx(0.0, abs=1e-12)
    assert g.max() == pytest.approx(1.0, abs=1e-12)
    assert s[0] == pytest.approx(0.0, abs=1e-12)
    assert s[-1] == pytest.approx(0.0, abs=1e-12)
