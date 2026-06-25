"""Adaptive Local Clipping per-frame stacking (U8)."""

from __future__ import annotations

import numpy as np
import pytest

import percell4.gui.adaptive_clip_panel as acp


def _fake_report(image, presmooth_sigma_px, n_pos):
    """A minimal AutoExtractReport whose fine_window encodes the frame's mean."""
    from percell4.domain.measure.auto_extraction import AutoExtractReport

    return AutoExtractReport(
        passes=[(int(image.mean()), 1.0)],
        fine_window=int(image.mean()),
        largest_particle_px=float(image.max()),
        second_pass_used=False,
        presmooth_sigma_px=presmooth_sigma_px,
        n_cells=1,
        n_components=n_pos,
        area_px=n_pos,
        smallest_diameter_px=2.0,
        smallest_source="auto",
    )


def test_run_adaptive_detection_stack_loops_and_stacks(monkeypatch):
    """The stack worker runs detection per frame and stacks to (T,H,W); each
    frame's mask is that frame's own detection (not frame 0 broadcast), and the
    auto window is computed per frame (contract D3)."""

    def fake_detect(frame, sigma, settings, auto_window, window_method="otsu-mean"):
        # Encode the frame's mean in the window so per-frame computation shows.
        w = int(frame.mean())
        m = (frame > frame.mean()).astype(np.uint8)
        return m, w

    monkeypatch.setattr(acp, "run_adaptive_detection", fake_detect)

    image = np.stack(
        [
            np.full((4, 4), 1, dtype=np.float32),
            np.full((4, 4), 10, dtype=np.float32),
        ],
        axis=0,
    )
    image[1, 0, 0] = 100  # frame 1 has a bright pixel -> non-trivial mask

    mask, windows = acp.run_adaptive_detection_stack(image, 0.0, object(), True)

    assert mask.shape == (2, 4, 4)
    assert mask.dtype == np.uint8
    # Per-frame windows differ -> the window was computed per frame, not once.
    assert windows == [1, 15]
    assert windows[0] != windows[1]
    # Frame 1's mask flags only its bright pixel; frame 0 is uniform -> all zero.
    assert mask[0].sum() == 0
    assert mask[1, 0, 0] == 1


def test_run_adaptive_auto_extract_stack_loops_and_stacks(monkeypatch):
    """The auto-extract stack worker runs auto_extract per frame and stacks to (T,H,W);
    each frame is sized on its own data (not frame 0 broadcast)."""
    import percell4.domain.measure.auto_extraction as ae_mod

    def fake_auto_extract(image, labels, *, smallest_particle_px=None,
                          presmooth_sigma_px=1.0, min_spot_px=2):
        m = (image > image.mean()).astype(np.uint8)
        return m, _fake_report(image, presmooth_sigma_px, int(m.sum()))

    monkeypatch.setattr(ae_mod, "auto_extract", fake_auto_extract)

    image = np.stack(
        [np.full((4, 4), 1.0, np.float32), np.full((4, 4), 10.0, np.float32)], axis=0
    )
    image[1, 0, 0] = 100.0  # frame 1 has a bright pixel
    labels = np.ones((2, 4, 4), dtype=np.int32)

    mask, reports = acp.run_adaptive_auto_extract_stack(image, labels, 2.0, 1.0, 1)

    assert mask.shape == (2, 4, 4) and mask.dtype == np.uint8
    assert len(reports) == 2 and all(r is not None for r in reports)
    # Per-frame sizing: the two frames' reported fine_windows differ.
    assert reports[0].fine_window != reports[1].fine_window
    assert mask[0].sum() == 0          # uniform frame 0 -> empty
    assert mask[1, 0, 0] == 1          # frame 1's own bright pixel


def test_run_adaptive_auto_extract_stack_blank_frame_degrades(monkeypatch):
    """R9: in auto-detect mode a frame with no blobs becomes an empty plane, not an
    aborted run."""
    import percell4.domain.measure.auto_extraction as ae_mod

    def fake_auto_extract(image, labels, *, smallest_particle_px=None,
                          presmooth_sigma_px=1.0, min_spot_px=2):
        if image.max() < 50:  # a "blank" frame: auto-detect finds nothing
            raise ValueError("smallest-particle autodetection found no blobs; supply ...")
        m = (image > image.mean()).astype(np.uint8)
        return m, _fake_report(image, presmooth_sigma_px, int(m.sum()))

    monkeypatch.setattr(ae_mod, "auto_extract", fake_auto_extract)

    image = np.stack(
        [np.full((4, 4), 100.0, np.float32), np.full((4, 4), 1.0, np.float32)], axis=0
    )
    image[0, 0, 0] = 200.0
    labels = np.ones((2, 4, 4), dtype=np.int32)

    # auto-detect mode: smallest_particle_px=None
    mask, reports = acp.run_adaptive_auto_extract_stack(image, labels, None, 1.0, 1)

    assert mask.shape == (2, 4, 4)
    assert reports[0] is not None      # frame 0 detected
    assert reports[1] is None          # frame 1 degraded to empty
    assert mask[1].sum() == 0          # empty plane, no abort


def test_run_adaptive_auto_extract_stack_supplied_smallest_reraises(monkeypatch):
    """A raise with a SUPPLIED smallest is not the recoverable no-blobs case -> propagates."""
    import percell4.domain.measure.auto_extraction as ae_mod

    def boom(image, labels, **kwargs):
        raise ValueError("some other failure")

    monkeypatch.setattr(ae_mod, "auto_extract", boom)
    image = np.zeros((2, 4, 4), np.float32)
    labels = np.ones((2, 4, 4), np.int32)
    with pytest.raises(ValueError):
        acp.run_adaptive_auto_extract_stack(image, labels, 3.0, 1.0, 1)  # supplied smallest


def test_run_cnr_classification_stack_returns_THW_pop_masks(monkeypatch):
    """The CNR stack worker classifies each frame and returns (T,H,W) population masks
    + per-focus components carrying a timepoint, mirroring run_cnr_classification's
    contract."""
    import percell4.domain.measure.cnr_classification as cnr_mod
    from percell4.domain.measure.cnr_classification import ClassificationResult

    labels_image = np.zeros((6, 6), dtype=np.int32)
    labels_image[1, 1] = 1   # low-CNR focus
    labels_image[4, 4] = 2   # high-CNR focus
    result = ClassificationResult(
        n_subpopulations=2, labels_image=labels_image,
        components=[
            {"label": 1, "cnr": 3.0, "subpopulation": 1},
            {"label": 2, "cnr": 8.0, "subpopulation": 2},
        ],
        split_axis="cnr", threshold=5.0, report={"decision": "guided"},
    )
    monkeypatch.setattr(cnr_mod, "classify_by_cnr", lambda *a, **k: result)

    image = np.zeros((2, 6, 6), np.float32)
    mask = np.ones((2, 6, 6), np.uint8)
    labels = np.ones((2, 6, 6), np.int32)
    pop_masks, components, report = acp.run_cnr_classification_stack(
        image, mask, labels, mode="guided", threshold=5.0
    )

    assert [s for s, _ in pop_masks] == ["_low", "_high"]
    for _suffix, m in pop_masks:
        assert m.shape == (2, 6, 6) and m.dtype == np.uint8
    assert any("timepoint" in c for c in components)
    assert report["n_timepoints"] == 2


def test_run_cnr_classification_stack_single_population_one_mask(monkeypatch):
    """When no frame splits, the worker returns one base-name mask (all foci), matching
    the single-frame worker's single-population contract."""
    import percell4.domain.measure.cnr_classification as cnr_mod
    from percell4.domain.measure.cnr_classification import ClassificationResult

    labels_image = np.zeros((6, 6), dtype=np.int32)
    labels_image[1, 1] = 1
    result = ClassificationResult(
        n_subpopulations=1, labels_image=labels_image,
        components=[{"label": 1, "cnr": 3.0, "subpopulation": 1}],
        split_axis=None, threshold=None, report={"decision": "single population"},
    )
    monkeypatch.setattr(cnr_mod, "classify_by_cnr", lambda *a, **k: result)

    pop_masks, _components, _report = acp.run_cnr_classification_stack(
        np.zeros((2, 6, 6), np.float32), np.ones((2, 6, 6), np.uint8),
        np.ones((2, 6, 6), np.int32), mode="guided", threshold=5.0,
    )
    assert [s for s, _ in pop_masks] == [""]   # one mask under the base name
    assert pop_masks[0][1].shape == (2, 6, 6)


def test_accept_puncta_mask_persists_thw(tmp_h5):
    """AcceptPunctaMask is shape-transparent: a (T,H,W) mask round-trips (so the
    panel only needs to hand it a per-frame stack)."""
    from percell4.adapters.hdf5_store import Hdf5DatasetRepository
    from percell4.application.session import Session
    from percell4.application.use_cases.accept_puncta_mask import AcceptPunctaMask
    from percell4.store import DatasetStore

    store = DatasetStore(tmp_h5)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity", np.zeros((3, 8, 8), dtype=np.float32),
        attrs={"dims": ["T", "H", "W"]},
    )
    repo = Hdf5DatasetRepository()
    handle = repo.open(tmp_h5)
    session = Session()
    session.set_dataset(handle)

    mask = np.zeros((3, 8, 8), dtype=np.uint8)
    mask[1, 2, 2] = 1
    res = AcceptPunctaMask(repo, session).execute(mask, "adaptive")

    assert res.n_positive == 1
    assert store.read_mask("adaptive").shape == (3, 8, 8)
    assert store.read_mask("adaptive", timepoint=1)[2, 2] == 1
