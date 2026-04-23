"""End-to-end smoke test for the bench_wavelet CLI.

Runs the synthetic comparison on a small phantom (~256×256, 128 bins,
50 reference frames, flevel=5) so the whole pipeline executes in under
a second. Verifies the artefacts land on disk and the metrics JSON has
the expected structure. Numerical quality is covered by the algorithm-
level tests in ``test_wavelet_boe.py``; this test only guards the glue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("dtcwt")
pytest.importorskip("matplotlib")


@pytest.mark.slow
def test_bench_wavelet_synthetic_end_to_end(tmp_path: Path):
    from percell4.interfaces.cli.bench_wavelet import run_synthetic

    metrics = run_synthetic(
        tmp_path,
        seed=0,
        shape=(256, 256),
        n_bins=128,
        n_reference_frames=50,
        filter_level=5,
    )

    # Files produced.
    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "mse_curves.npz").exists()
    assert (tmp_path / "phasor_plots.png").exists()
    assert (tmp_path / "g_maps.png").exists()
    assert (tmp_path / "mse_per_frequency.png").exists()

    # metrics.json round-trips from file (not just the returned dict).
    loaded = json.loads((tmp_path / "metrics.json").read_text())
    assert loaded["seed"] == 0
    assert loaded["filter_level"] == 5
    whole = loaded["whole_image_gs_mse"]
    assert set(whole) >= {"unfiltered", "boe_2021", "jcb_2025"}

    # Both filters must cut the whole-image MSE substantially vs. unfiltered.
    assert whole["boe_2021"] < 0.5 * whole["unfiltered"]
    assert whole["jcb_2025"] < 0.5 * whole["unfiltered"]

    # The two filters produce measurably different results (regression
    # guard against collapsing them into the same code path).
    assert abs(whole["boe_2021"] - whole["jcb_2025"]) > 1e-5

    # Timings recorded for each phase.
    timings = loaded["timings_sec"]
    for phase in ("phantom_generation", "phasor_computation",
                  "filter_boe_2021", "filter_jcb_2025"):
        assert phase in timings
        assert timings[phase] >= 0.0

    # Version provenance.
    versions = loaded["versions"]
    assert "dtcwt" in versions
    assert "numpy" in versions
