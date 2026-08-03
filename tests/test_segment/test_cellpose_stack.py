"""run_cellpose_stack loops per timepoint (no real Cellpose needed)."""

from __future__ import annotations

import numpy as np


def test_run_cellpose_stack_loops_frames_and_reports_progress(monkeypatch):
    import percell4.adapters.cellpose as cp

    # Avoid building a real Cellpose model.
    monkeypatch.setattr(cp, "build_cellpose_model", lambda **kw: object())

    seen_shapes = []

    def fake_run(image, diameter=None, model=None, **kw):
        seen_shapes.append(image.shape)
        return (image > 0).astype(np.int32)

    monkeypatch.setattr(cp, "run_cellpose", fake_run)

    stack = np.ones((3, 8, 8), dtype=np.float32)
    progress = []
    out = cp.run_cellpose_stack(
        stack, progress_callback=lambda d, n: progress.append((d, n))
    )

    assert out.shape == (3, 8, 8)
    assert out.dtype == np.int32
    # Each frame handed to run_cellpose was 2D — never the whole 3D stack
    # (which run_cellpose would misread as (H, W, C) multichannel).
    assert seen_shapes == [(8, 8), (8, 8), (8, 8)]
    assert progress == [(1, 3), (2, 3), (3, 3)]
