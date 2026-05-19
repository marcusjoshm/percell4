"""Tests for the headless phasor PNG renderer.

Covers the happy paths, the empty/with-data outcome enum, the
shape-mismatch error path, and the two GUI-parity correctness gates:

* **Orientation** — a file-exists/bbox assertion cannot catch a G/S
  axis swap (scientifically wrong but visually plausible). The
  orientation test compares the colored-blob centroid of a high-G/low-S
  render against a low-G/high-S render and asserts it moves in the
  expected direction on *both* axes.
* **Aspect** — matplotlib ``imshow`` defaults to ``aspect="equal"``,
  which would distort the semicircle; the renderer must request
  ``aspect="auto"``.

Renders hit the real Agg backend and the real filesystem under
tmp_path; PNGs are decoded back with Pillow. No mocking of the
renderer itself.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import matplotlib.axes
import numpy as np
import pytest
from PIL import Image

from percell4.application.phasor_render import (
    RenderOutcome,
    render_phasor_png,
)

_SRC = str(Path(__file__).resolve().parents[2] / "src")


# ── Helpers ─────────────────────────────────────────────────────────────


def _cluster(g_center: float, s_center: float, n: int = 4000) -> tuple:
    """Build (g, s, intensity) maps tightly clustered at one point."""
    rng = np.random.default_rng(0)
    side = int(np.sqrt(n))
    g = (g_center + rng.normal(0, 0.01, side * side)).astype(np.float32)
    s = (s_center + rng.normal(0, 0.01, side * side)).astype(np.float32)
    g = g.reshape(side, side)
    s = s.reshape(side, side)
    intensity = np.ones_like(g, dtype=np.float32)
    return g, s, intensity


def _colored_centroid(png_path: Path) -> tuple[float, float]:
    """Centroid (x, y) of vivid (high-saturation) pixels in the PNG.

    Isolates the nipy_spectral histogram blob from the white figure
    background and the black empty-data region (both low saturation),
    so the centroid tracks the data cloud regardless of axis decoration
    placement. Image y grows downward.
    """
    arr = np.asarray(Image.open(png_path).convert("RGB")).astype(float)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1.0), 0.0)
    ys, xs = np.where(sat > 0.4)
    assert xs.size > 0, "no vivid pixels found — nothing rendered?"
    return float(xs.mean()), float(ys.mean())


# ── Happy paths ─────────────────────────────────────────────────────────


def test_happy_path_weighted_writes_decodable_png(tmp_path: Path) -> None:
    g, s, intensity = _cluster(0.5, 0.35)
    out = tmp_path / "phasor.png"

    outcome = render_phasor_png(g, s, out_path=out, intensity=intensity)

    assert outcome is RenderOutcome.RENDERED_WITH_DATA
    assert out.exists() and out.stat().st_size > 0
    # Decodes as a real PNG of non-trivial size.
    img = Image.open(out)
    assert img.format == "PNG"
    assert img.size[0] > 50 and img.size[1] > 50


def test_happy_path_unweighted_intensity_none(tmp_path: Path) -> None:
    g, s, _ = _cluster(0.5, 0.35)
    out = tmp_path / "phasor.png"

    outcome = render_phasor_png(g, s, out_path=out, intensity=None)

    assert outcome is RenderOutcome.RENDERED_WITH_DATA
    assert out.exists()


# ── Orientation guard (axis-swap correctness gate) ──────────────────────


def test_orientation_high_g_low_s_vs_low_g_high_s(tmp_path: Path) -> None:
    """The colored blob must move right+down for high-G/low-S relative
    to low-G/high-S. A G/S swap or a vertical flip fails this."""
    g_hi, s_lo, i1 = _cluster(0.9, 0.1)
    out_hi = tmp_path / "high_g_low_s.png"
    render_phasor_png(g_hi, s_lo, out_path=out_hi, intensity=i1)

    g_lo, s_hi, i2 = _cluster(0.1, 0.45)
    out_lo = tmp_path / "low_g_high_s.png"
    render_phasor_png(g_lo, s_hi, out_path=out_lo, intensity=i2)

    x_hi, y_hi = _colored_centroid(out_hi)
    x_lo, y_lo = _colored_centroid(out_lo)

    # Higher G -> further right in image x.
    assert x_hi > x_lo, f"G axis not mapped to x (x_hi={x_hi}, x_lo={x_lo})"
    # Higher S -> nearer the top -> smaller image y (y grows downward).
    assert y_hi > y_lo, (
        f"S axis not mapped to y / vertical flip "
        f"(y_hi={y_hi}, y_lo={y_lo})"
    )


# ── Aspect (semicircle-distortion correctness gate) ─────────────────────


def test_imshow_requested_with_aspect_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The renderer must call imshow with aspect='auto'; the imshow
    default 'equal' would squash the semicircle into an ellipse."""
    captured: dict[str, object] = {}
    real_imshow = matplotlib.axes.Axes.imshow

    def spy(self, *args, **kwargs):  # noqa: ANN001
        captured["aspect"] = kwargs.get("aspect")
        return real_imshow(self, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "imshow", spy)

    g, s, intensity = _cluster(0.5, 0.35)
    render_phasor_png(
        g, s, out_path=tmp_path / "p.png", intensity=intensity
    )

    assert captured["aspect"] == "auto"


# ── Edge / error paths ──────────────────────────────────────────────────


def test_all_nan_g_returns_empty_but_writes_png(tmp_path: Path) -> None:
    g = np.full((16, 16), np.nan, dtype=np.float32)
    s = np.full((16, 16), np.nan, dtype=np.float32)
    out = tmp_path / "empty.png"

    outcome = render_phasor_png(g, s, out_path=out)

    assert outcome is RenderOutcome.RENDERED_EMPTY
    assert out.exists() and out.stat().st_size > 0


def test_all_zero_g_returns_empty(tmp_path: Path) -> None:
    g = np.zeros((16, 16), dtype=np.float32)
    s = np.zeros((16, 16), dtype=np.float32)
    out = tmp_path / "empty.png"

    assert (
        render_phasor_png(g, s, out_path=out)
        is RenderOutcome.RENDERED_EMPTY
    )
    assert out.exists()


def test_valid_pixels_but_all_zero_intensity_returns_empty(
    tmp_path: Path,
) -> None:
    """A2/A1 regression: valid (finite, non-zero) g/s but all-zero
    weights -> blank histogram -> must report RENDERED_EMPTY, not
    RENDERED_WITH_DATA (the outcome is decided post-weighting)."""
    g, s, _ = _cluster(0.5, 0.35)
    zero_intensity = np.zeros(g.size, dtype=np.float32)
    out = tmp_path / "p.png"

    outcome = render_phasor_png(
        g, s, out_path=out, intensity=zero_intensity
    )

    assert outcome is RenderOutcome.RENDERED_EMPTY
    assert out.exists() and out.stat().st_size > 0


def test_intensity_shape_mismatch_falls_back_unweighted(
    tmp_path: Path,
) -> None:
    g, s, _ = _cluster(0.5, 0.35)
    bad_intensity = np.ones(g.size + 7, dtype=np.float32)  # wrong size
    out = tmp_path / "p.png"

    # No crash; renders unweighted.
    outcome = render_phasor_png(
        g, s, out_path=out, intensity=bad_intensity
    )
    assert outcome is RenderOutcome.RENDERED_WITH_DATA
    assert out.exists()


def test_nested_output_dir_is_created(tmp_path: Path) -> None:
    g, s, _ = _cluster(0.5, 0.35)
    out = tmp_path / "deep" / "nested" / "p.png"

    render_phasor_png(g, s, out_path=out)

    assert out.exists()


def test_g_s_shape_mismatch_raises_valueerror(tmp_path: Path) -> None:
    g = np.zeros((8, 8), dtype=np.float32)
    s = np.zeros((8, 9), dtype=np.float32)

    with pytest.raises(ValueError, match="same shape"):
        render_phasor_png(g, s, out_path=tmp_path / "p.png")


# ── Seam: importing the renderer pulls in no Qt / napari ────────────────


def test_import_does_not_load_qt_or_napari() -> None:
    """Stronger than test_cli_module_imports_without_qt (which only does
    importlib.reload + hasattr): a fresh interpreter must not have Qt or
    napari in sys.modules after importing the renderer."""
    code = (
        "import sys; import percell4.application.phasor_render; "
        "assert 'PyQt5' not in sys.modules, 'PyQt5 leaked'; "
        "assert 'qtpy' not in sys.modules, 'qtpy leaked'; "
        "assert 'napari' not in sys.modules, 'napari leaked'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": _SRC},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
