"""Headless phasor-plot PNG renderer.

Pure rendering helper: turns ``(g, s, intensity)`` arrays into a PNG that
mirrors what the GUI phasor window
(:class:`percell4.interfaces.gui.peer_views.phasor_plot.PhasorPlotWindow`)
shows — an intensity-weighted 2D histogram with the universal
semicircle overlay and labeled G/S axes.

GUI-parity is *specified*, not merely "looks similar":

* **Constants** mirror ``_refresh_histogram``: ``bins=300``,
  ``range=[(-0.005, 1.005), (0.0, 0.7)]``, ``np.log1p`` of the counts,
  ``nipy_spectral`` colormap.
* **Orientation:** ``np.histogram2d(g, s)`` returns shape
  ``(n_g, n_s)``. The GUI does *not* transpose — pyqtgraph's
  ``ImageItem`` defaults to col-major, so G already maps to x there.
  matplotlib ``imshow`` is row-major, so this renderer must transpose:
  ``imshow(hist.T, origin="lower", extent=[g…, s…])``. We mirror the
  GUI's *result* (G on x, S on y), not its code path.
* **Aspect:** ``aspect="auto"`` is set explicitly. The GUI's pyqtgraph
  plot scales the G axis (range ≈ 1.01) and S axis (range = 0.7)
  independently; matplotlib ``imshow`` defaults to ``aspect="equal"``,
  which would squash the semicircle into an ellipse.

No Qt, no napari, **no global backend mutation**: this module renders
through :class:`matplotlib.figure.Figure` +
:class:`~matplotlib.backends.backend_agg.FigureCanvasAgg` directly and
never imports ``pyplot`` or calls ``matplotlib.use()``. That keeps it
safe to import from any process — including a live GUI one — since it
cannot switch a host's backend or tear down its figure stack as a
side effect of import.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from numpy.typing import NDArray

from percell4.domain.flim.phasor_display import compute_valid_phasor_pixels

# GUI parity constants — keep in lockstep with phasor_plot._refresh_histogram.
_G_RANGE: tuple[float, float] = (-0.005, 1.005)
_S_RANGE: tuple[float, float] = (0.0, 0.7)
_BINS: int = 300
_CMAP: str = "nipy_spectral"


class RenderOutcome(Enum):
    """Outcome of a single :func:`render_phasor_png` call.

    ``RENDERED_EMPTY`` means the PNG was written but the histogram
    carried no signal — either no valid (finite, non-zero) pixels, or
    all weights were zero (e.g. an all-zero decay channel). The caller
    is expected to consume this — it is not an advisory the caller may
    ignore (see ``batch_export_phasor``).
    """

    RENDERED_WITH_DATA = "rendered_with_data"
    RENDERED_EMPTY = "rendered_empty"


def render_phasor_png(
    g: NDArray[np.floating],
    s: NDArray[np.floating],
    *,
    out_path: Path,
    intensity: NDArray[np.floating] | None = None,
    title: str | None = None,
) -> RenderOutcome:
    """Render a phasor histogram PNG to ``out_path``.

    Args:
        g: G-coordinate map (any shape; flattened internally).
        s: S-coordinate map. Must have the same shape as ``g``.
        out_path: Destination ``.png`` path. Parent directories are
            created if missing. Any existing file is overwritten.
        intensity: Optional per-pixel intensity used as histogram
            weights (the GUI uses ``decay.sum(-1)``). Used only when it
            is not None and ``intensity.size == g.size``; otherwise the
            histogram is unweighted. NOTE: in the batch path, a
            ``decay``/``g`` *shape mismatch* is a stale-cache signal the
            caller treats as a per-channel failure *before* calling
            this renderer — this size guard is the standalone-call
            defensive contract only, not a place to paper over
            misalignment.
        title: Optional plot title.

    Returns:
        :class:`RenderOutcome` — ``RENDERED_WITH_DATA`` when the
        weighted histogram carries any signal, ``RENDERED_EMPTY`` when
        it does not (no valid pixels, or all weights zero). The PNG is
        written in both cases.

    Raises:
        ValueError: if ``g`` and ``s`` have mismatched shapes.
    """
    if g.shape != s.shape:
        raise ValueError(
            f"g and s must have the same shape; got {g.shape} and "
            f"{s.shape}"
        )

    g_flat = g.ravel()
    s_flat = s.ravel()

    # Always-on validity filter — reuse the domain helper the GUI uses
    # (finite, non-zero g). All optional filters are None: the headless
    # export is the full dataset phasor, no interactive GUI filters.
    valid = compute_valid_phasor_pixels(g_flat, s_flat, None, None, None)

    g_valid = g_flat[valid]
    s_valid = s_flat[valid]

    if intensity is not None and intensity.size == g.size:
        weights = intensity.ravel()[valid]
    else:
        weights = np.ones(g_valid.size)

    hist, _g_edges, _s_edges = np.histogram2d(
        g_valid,
        s_valid,
        bins=_BINS,
        range=[_G_RANGE, _S_RANGE],
        weights=weights,
    )

    # Outcome is decided from the *post-weight* histogram, not the valid
    # pixel count: an all-zero-weight render (e.g. a zeroed decay
    # channel) has valid pixels but no signal and must report empty so
    # the caller is not silently handed a blank plot.
    outcome = (
        RenderOutcome.RENDERED_WITH_DATA
        if hist.sum() > 0
        else RenderOutcome.RENDERED_EMPTY
    )

    hist_display = np.log1p(hist)

    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.subplots()

    # hist is (n_g, n_s); transpose so axis0 -> y (S), axis1 -> x (G),
    # matching the GUI's col-major ImageItem result.
    ax.imshow(
        hist_display.T,
        origin="lower",
        extent=[_G_RANGE[0], _G_RANGE[1], _S_RANGE[0], _S_RANGE[1]],
        cmap=_CMAP,
        aspect="auto",  # GUI scales G/S independently — not "equal"
    )

    # Universal semicircle (parametrization mirrors the GUI).
    theta = np.linspace(0, np.pi, 200)
    ax.plot(
        0.5 + 0.5 * np.cos(theta),
        0.5 * np.sin(theta),
        color="white",
        linewidth=1.5,
    )

    ax.set_xlim(*_G_RANGE)
    ax.set_ylim(*_S_RANGE)
    ax.set_xlabel("G")
    ax.set_ylabel("S")
    if title is not None:
        ax.set_title(title)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    # No pyplot global state to release: the Figure and its Agg canvas
    # are local and garbage-collected with this frame.

    return outcome
