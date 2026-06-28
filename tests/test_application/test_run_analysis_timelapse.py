"""Time-aware analysis framework: per-frame loader + aggregation (U14)."""

from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

from percell4.application.analysis.loader import load_layers
from percell4.application.use_cases.run_analysis import _aggregate_timepoints
from percell4.domain.analysis import ImageOutput, ImageRole, TableOutput
from percell4.store import DatasetStore


def _timelapse_store(path):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "DAPI"]})
    intensity = np.zeros((2, 2, 8, 8), dtype=np.float32)
    for t in range(2):
        for c in range(2):
            intensity[t, c] = t * 10 + c
    store.write_array("intensity", intensity, attrs={"dims": ["T", "C", "H", "W"]})
    labels = np.zeros((2, 8, 8), dtype=np.int32)
    labels[1, 2, 2] = 7
    store.write_labels("cp", labels)
    gate = np.zeros((8, 8), dtype=np.uint8)
    gate[4, 4] = 1
    store.write_mask("thr", gate)  # 2D time-invariant
    return store


def test_load_layers_per_frame_returns_2d(tmp_h5):
    _timelapse_store(tmp_h5)
    roles = {
        "chan": ImageRole(kind="intensity", dtype="float", ndim=(2,)),
        "seg": ImageRole(kind="label", dtype="int", ndim=(2,)),
        "msk": ImageRole(kind="mask", dtype="bool", ndim=(2,)),
    }
    layer_map = {"chan": "GFP", "seg": "cp", "msk": "thr"}

    arrays = load_layers(tmp_h5, layer_map, roles, timepoint=1)
    assert arrays["chan"].shape == (8, 8)
    assert np.all(arrays["chan"] == 10)  # t=1, channel GFP (c=0)
    assert arrays["seg"].shape == (8, 8)
    assert arrays["seg"][2, 2] == 7      # frame 1's label
    assert arrays["msk"].shape == (8, 8)
    assert arrays["msk"][4, 4]            # 2D gate broadcast to frame 1


def test_load_layers_frame0_differs(tmp_h5):
    _timelapse_store(tmp_h5)
    roles = {"chan": ImageRole(kind="intensity", dtype="float", ndim=(2,))}
    a0 = load_layers(tmp_h5, {"chan": "GFP"}, roles, timepoint=0)
    a1 = load_layers(tmp_h5, {"chan": "GFP"}, roles, timepoint=1)
    assert np.all(a0["chan"] == 0) and np.all(a1["chan"] == 10)


def test_aggregate_timepoints_tables_concat_with_timepoint():
    cls = types.SimpleNamespace(
        outputs={"counts": TableOutput(), "mask": ImageOutput(dtype="binary")}
    )
    df0 = pd.DataFrame({"label": [1, 2], "n": [3, 4]})
    df1 = pd.DataFrame({"label": [1, 2], "n": [5, 6]})
    img0 = np.zeros((8, 8), dtype=np.uint8)
    img1 = np.ones((8, 8), dtype=np.uint8)
    per_t = [
        (0, {"counts": df0, "mask": img0}),
        (1, {"counts": df1, "mask": img1}),
    ]

    out = _aggregate_timepoints(per_t, cls)

    # Table: concatenated with a timepoint column (4 rows).
    assert list(out["counts"]["timepoint"]) == [0, 0, 1, 1]
    assert len(out["counts"]) == 4
    # Image: stacked on a leading T axis.
    assert out["mask"].shape == (2, 8, 8)
    assert out["mask"][0].sum() == 0 and out["mask"][1].sum() == 64


def test_aggregate_tolerates_output_absent_on_some_frames():
    cls = types.SimpleNamespace(outputs={"counts": TableOutput()})
    df1 = pd.DataFrame({"label": [1], "n": [9]})
    per_t = [(0, {}), (1, {"counts": df1})]  # frame 0 produced nothing
    out = _aggregate_timepoints(per_t, cls)
    assert list(out["counts"]["timepoint"]) == [1]


# ── ImageOutput is exact-T: never silently shortened (U1) ─────────


def test_aggregate_imageoutput_all_frames_present_is_exact_T():
    """An ImageOutput present on every frame (the realistic per-particle
    donut path, which emits an all-zero plane on an empty frame) stacks to
    exactly T planes on a leading T axis."""
    cls = types.SimpleNamespace(outputs={"donut": ImageOutput(dtype="binary")})
    img0 = np.ones((4, 4), dtype=np.uint8)
    img1 = np.zeros((4, 4), dtype=np.uint8)  # an empty frame's all-zero plane
    img2 = np.ones((4, 4), dtype=np.uint8)
    per_t = [(0, {"donut": img0}), (1, {"donut": img1}), (2, {"donut": img2})]
    out = _aggregate_timepoints(per_t, cls)
    assert out["donut"].shape == (3, 4, 4)
    assert out["donut"][1].sum() == 0
    assert out["donut"][0].sum() == 16 and out["donut"][2].sum() == 16


def test_aggregate_imageoutput_missing_frame_not_silently_shortened():
    """Exact-T contract: an ImageOutput absent on one frame must NOT shorten
    the stack to < T planes — a short stack would later raise
    LayerSizeMismatchError at write. The missing frame becomes an all-zero
    plane shaped like the frames that did emit it, so the stack stays exactly
    T. (Contrast the TableOutput tolerance above, which is intentional: tables
    concatenate variable rows, images must stay an exact-T stack.)

    Belt-and-suspenders for the producer guarantee: the per-particle donut
    producer already emits an all-zero plane on a no-particle frame, so in
    practice the key is present on every frame — but the aggregator must not
    silently drop a frame even if a producer ever omits it.
    """
    cls = types.SimpleNamespace(outputs={"donut": ImageOutput(dtype="binary")})
    img0 = np.ones((4, 4), dtype=np.uint8)
    img2 = np.ones((4, 4), dtype=np.uint8)
    per_t = [(0, {"donut": img0}), (1, {}), (2, {"donut": img2})]  # f1 omitted
    out = _aggregate_timepoints(per_t, cls)
    assert out["donut"].shape == (3, 4, 4)  # exact-T, not (2, 4, 4)
    assert out["donut"][1].sum() == 0       # the omitted frame: all-zero plane
    assert out["donut"][0].sum() == 16 and out["donut"][2].sum() == 16
