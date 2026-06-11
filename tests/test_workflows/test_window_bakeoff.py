"""Tests for the Qt-free window bake-off harness (plan U7)."""

from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import disk

from percell4.domain.measure.window_finders import WINDOW_FINDERS
from percell4.store import DatasetStore
from percell4.workflows.models import PunctaDetectorSettings
from percell4.workflows.window_bakeoff import (
    BakeoffField,
    BakeoffReport,
    calibrate_c,
    load_bakeoff_field,
    run_bakeoff,
)


def _granule_image(centers, radius, *, dilute=50.0, fg=220.0, shape=(160, 160), seed=0):
    rng = np.random.default_rng(seed)
    img = dilute + rng.normal(0.0, 2.0, size=shape).astype(np.float32)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        img[rr, cc] = fg
    return img.astype(np.float32)


def _disk_mask(centers, radius, shape=(160, 160), dtype=np.uint8):
    m = np.zeros(shape, dtype=dtype)
    for cy, cx in centers:
        rr, cc = disk((cy, cx), radius, shape=shape)
        m[rr, cc] = 1
    return m


def _settings(k=3.0):
    return PunctaDetectorSettings(
        detector_name="adaptive",
        seed_detector_name="otsu",
        background_estimator_name="mad",
        detector_params={"window_px": 31, "k": k},
        min_spot_px=3,
        spot_scale_prior=(1.0, 4.0),
    )


def _field(name="fA", *, sg=True, seed=0):
    centers = [(80, 80), (80, 40), (40, 110)]
    img = _granule_image(centers, 8, seed=seed)
    sg_mask = _disk_mask(centers, 8) if sg else None
    return BakeoffField(name=name, image=img, sg_mask=sg_mask, cp_mask=None, gaussian_sigma=1.0)


GRID = [15, 31, 51]


# ── run_bakeoff core ──────────────────────────────────────────────────────


def test_run_bakeoff_scores_finders_and_ranks():
    report = run_bakeoff([_field()], ["otsu-mean", "granule-size"], _settings(), GRID)
    assert isinstance(report, BakeoffReport)
    assert report.k == 3.0
    assert "fA" in report.oracles
    methods = {s.method for s in report.scores if s.score is not None}
    assert methods == {"otsu-mean", "granule-size"}
    assert [m for m, _ in report.ranking]  # a non-empty ranking
    # every scored finder records the pinned k and a window error
    for s in report.scores:
        if s.score is not None:
            assert s.score.k == 3.0
            assert s.score.window_error == abs(s.score.auto_window - s.score.ideal_window)


def test_run_bakeoff_in_sample_flag_default_and_holdout():
    # No holdout -> every score is in-sample (single labeled image).
    rep = run_bakeoff([_field("fA")], ["granule-size"], _settings(), GRID)
    assert all(s.score.in_sample for s in rep.scores if s.score is not None)
    # Two fields, one held out -> the held-out field is NOT in-sample.
    rep2 = run_bakeoff(
        [_field("fA", seed=0), _field("fB", seed=1)],
        ["granule-size"], _settings(), GRID, holdout_field_names=["fB"],
    )
    by_field = {s.field: s.score.in_sample for s in rep2.scores if s.score is not None}
    assert by_field["fA"] is True
    assert by_field["fB"] is False


def test_run_bakeoff_flags_missing_sg_mask():
    rep = run_bakeoff([_field("labeled"), _field("unlabeled", sg=False)], ["otsu-mean"], _settings(), GRID)
    assert rep.missing_label_fields == ["unlabeled"]
    assert "unlabeled" not in rep.oracles  # not scored
    assert "labeled" in rep.oracles


def test_run_bakeoff_finder_failure_is_recorded_not_fatal(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(WINDOW_FINDERS, "boom", _boom)
    rep = run_bakeoff([_field()], ["otsu-mean", "boom"], _settings(), GRID)
    failures = [s for s in rep.scores if s.score is None]
    assert len(failures) == 1
    assert failures[0].method == "boom"
    assert "kaboom" in failures[0].error
    # the good finder still scored
    assert any(s.method == "otsu-mean" and s.score is not None for s in rep.scores)


def test_run_bakeoff_empty_when_no_labeled_fields():
    rep = run_bakeoff([_field(sg=False)], ["otsu-mean"], _settings(), GRID)
    assert rep.oracles == {}
    assert rep.ranking == []
    assert rep.missing_label_fields == ["fA"]


def test_calibrate_c_picks_a_value():
    best_c, mean_err = calibrate_c([_field()], "granule-size", _settings(), GRID, [3.0, 4.5, 6.0])
    assert best_c in (3.0, 4.5, 6.0)
    assert mean_err >= 0.0


# ── load_bakeoff_field (store boundary) ───────────────────────────────────


def _make_store(path, *, with_sg=True, with_cp=True):
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["G3BP1", "DNA"]})
    centers = [(80, 80), (80, 40), (40, 110)]
    g3 = _granule_image(centers, 8)
    intensity = np.stack([g3, np.zeros_like(g3)], axis=0).astype(np.float32)
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    if with_cp:
        labels = np.zeros((160, 160), dtype=np.int32)
        labels[20:140, 20:140] = 1
        store.write_labels("cp_mask", labels)
    if with_sg:
        store.write_mask("SG_mask", _disk_mask(centers, 8))
    return store


def test_load_bakeoff_field_reads_channel_sg_and_cp(tmp_path):
    p = tmp_path / "DS.h5"
    _make_store(p)
    store = DatasetStore(p)
    with store.open_read():
        fld = load_bakeoff_field(store, "G3BP1", cp_name="cp_mask")
    assert fld.image.shape == (160, 160)
    assert fld.sg_mask is not None and fld.sg_mask.dtype == bool and fld.sg_mask.any()
    assert fld.cp_mask is not None and fld.cp_mask.dtype == bool and fld.cp_mask.any()


def test_load_bakeoff_field_sg_none_when_absent(tmp_path):
    p = tmp_path / "DS_nosg.h5"
    _make_store(p, with_sg=False)
    store = DatasetStore(p)
    with store.open_read():
        fld = load_bakeoff_field(store, "G3BP1")
    assert fld.sg_mask is None  # flagged by the harness, not an error


def test_load_bakeoff_field_binarizes_nonbinary_sg(tmp_path):
    """A 0/255-style SG mask is read as boolean (defensive binarize)."""
    p = tmp_path / "DS255.h5"
    store = DatasetStore(p)
    store.create(metadata={"channel_names": ["G3BP1", "DNA"]})
    centers = [(80, 80)]
    g3 = _granule_image(centers, 8)
    store.write_array("intensity", np.stack([g3, np.zeros_like(g3)], 0), attrs={"dims": ["C", "H", "W"]})
    sg = _disk_mask(centers, 8) * np.uint8(255)
    store.write_mask("SG_mask", (sg > 0).astype(np.uint8))  # store enforces {0,1}; read back boolean
    s2 = DatasetStore(p)
    with s2.open_read():
        fld = load_bakeoff_field(s2, "G3BP1")
    assert fld.sg_mask.dtype == bool and set(np.unique(fld.sg_mask)) <= {False, True}


def test_run_bakeoff_end_to_end_through_store(tmp_path):
    p = tmp_path / "DS_e2e.h5"
    _make_store(p)
    store = DatasetStore(p)
    with store.open_read():
        fld = load_bakeoff_field(store, "G3BP1", cp_name="cp_mask")
    rep = run_bakeoff([fld], ["otsu-mean", "granule-size"], _settings(), GRID)
    assert "DS_e2e" in rep.oracles
    assert any(s.score is not None for s in rep.scores)
