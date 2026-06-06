"""Inference-param forwarding through the segmentation seam (U1).

The full Cellpose controls — flow_threshold, cellprob_threshold, min_size —
must travel from SegmentCells.run_inference[_stack] through the Segmenter port
into run_cellpose. These tests assert the forwarding without invoking real
Cellpose (the model download is gated behind @pytest.mark.slow elsewhere).
"""

from __future__ import annotations

import numpy as np

from percell4.application.use_cases.segment_cells import SegmentCells


class RecordingSegmenter:
    """Segmenter that records the kwargs of each .run call and returns zeros."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, image, **kwargs):
        self.calls.append(kwargs)
        return np.zeros(np.asarray(image).shape, dtype=np.int32)


def _uc(segmenter):
    # run_inference / run_inference_stack never touch repo or session.
    return SegmentCells(repo=None, session=None, segmenter=segmenter)


def test_run_inference_forwards_full_params():
    seg = RecordingSegmenter()
    img = np.zeros((8, 8), dtype=np.float32)

    _uc(seg).run_inference(
        img,
        model_type="cpsam",
        diameter=42.0,
        gpu=True,
        flow_threshold=0.7,
        cellprob_threshold=-1.5,
        min_size=40,
    )

    assert len(seg.calls) == 1
    kw = seg.calls[0]
    assert kw["model_type"] == "cpsam"
    assert kw["diameter"] == 42.0
    assert kw["gpu"] is True
    assert kw["flow_threshold"] == 0.7
    assert kw["cellprob_threshold"] == -1.5
    assert kw["min_size"] == 40


def test_run_inference_defaults_match_run_cellpose():
    """Omitting the new args yields run_cellpose's defaults (backward compat)."""
    seg = RecordingSegmenter()
    _uc(seg).run_inference(np.zeros((8, 8), dtype=np.float32))

    kw = seg.calls[0]
    assert kw["flow_threshold"] == 0.4
    assert kw["cellprob_threshold"] == 0.0
    assert kw["min_size"] == 15


def test_run_inference_stack_forwards_params_every_frame():
    seg = RecordingSegmenter()
    stack = np.zeros((3, 8, 8), dtype=np.float32)

    _uc(seg).run_inference_stack(
        stack,
        flow_threshold=0.9,
        cellprob_threshold=2.0,
        min_size=33,
    )

    assert len(seg.calls) == 3
    for kw in seg.calls:
        assert kw["flow_threshold"] == 0.9
        assert kw["cellprob_threshold"] == 2.0
        assert kw["min_size"] == 33


def test_cellpose_segmenter_forwards_to_run_cellpose(monkeypatch):
    """CellposeSegmenter.run passes the inference controls to run_cellpose."""
    import percell4.adapters.cellpose as cp

    seen: dict = {}

    def _fake_run_cellpose(image, **kwargs):
        seen.update(kwargs)
        return np.zeros(np.asarray(image).shape, dtype=np.int32)

    monkeypatch.setattr(cp, "run_cellpose", _fake_run_cellpose)

    cp.CellposeSegmenter().run(
        np.zeros((8, 8), dtype=np.float32),
        model_type="cyto3",
        diameter=10.0,
        gpu=False,
        flow_threshold=0.6,
        cellprob_threshold=-2.0,
        min_size=21,
    )

    assert seen["model_type"] == "cyto3"
    assert seen["diameter"] == 10.0
    assert seen["gpu"] is False
    assert seen["flow_threshold"] == 0.6
    assert seen["cellprob_threshold"] == -2.0
    assert seen["min_size"] == 21
