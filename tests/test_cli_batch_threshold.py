"""Tests for the percell4-batch-threshold CLI (headless grouped thresholding)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from percell4.interfaces.cli import batch_threshold as cli
from percell4.store import DatasetStore


def _make_dataset(path: Path, *, with_labels: bool = True, size: int = 100,
                  n_cells: int = 12) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})
    intensity = np.zeros((2, size, size), dtype=np.float32)
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        # Two intensity populations within each cell so grouping/threshold
        # has something to split.
        intensity[0, row : row + 6, col : col + 6] = 40
        intensity[0, row + 1 : row + 4, col + 1 : col + 4] = 200
        intensity[1, row : row + 6, col : col + 6] = 80
        labels[row : row + 6, col : col + 6] = i + 1
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    if with_labels:
        store.write_labels("cellpose", labels)


def test_batch_threshold_writes_binary_mask(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--algorithm", "kmeans",
                   "--kmeans-n-clusters", "2"])
    assert rc == 0
    store = DatasetStore(p)
    assert "GFP_hi" in store.list_masks()
    mask = store.read_mask("GFP_hi")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    # /groups table written too.
    assert "GFP_hi" in store.list_groups("groups")
    # Hand-off line points the user at the measure CLI.
    assert "percell4-batch-measure" in capsys.readouterr().out


def test_batch_threshold_missing_labels_skips(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p, with_labels=False)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "r1"])
    assert rc == 1
    assert "no usable segmentation" in capsys.readouterr().err


def test_batch_threshold_overwrite_guard(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    # First run writes the mask.
    assert cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                     "--segmentation", "cellpose", "--kmeans-n-clusters", "2"]) == 0
    before = DatasetStore(p).read_mask("GFP_hi").copy()

    # Second run without --overwrite must refuse and leave the mask intact.
    rc = cli.main([str(p), "--channel", "RFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--kmeans-n-clusters", "2"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().err
    after = DatasetStore(p).read_mask("GFP_hi")
    assert np.array_equal(before, after)  # untouched

    # With --overwrite it is replaced (run succeeds).
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--kmeans-n-clusters", "2",
                   "--overwrite"])
    assert rc == 0


def test_batch_threshold_invalid_round_name(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "1bad",
                   "--segmentation", "cellpose"])
    assert rc == 1
    assert "invalid round configuration" in capsys.readouterr().err


def test_batch_threshold_partial_success_exit_zero(tmp_path, capsys):
    good = tmp_path / "good.h5"
    bad = tmp_path / "bad.h5"
    _make_dataset(good)
    _make_dataset(bad, with_labels=False)
    rc = cli.main([str(good), str(bad), "--channel", "GFP",
                   "--round-name", "GFP_hi", "--segmentation", "cellpose",
                   "--kmeans-n-clusters", "2"])
    assert rc == 0  # one succeeded
    assert "GFP_hi" in DatasetStore(good).list_masks()
    assert "GFP_hi" not in DatasetStore(bad).list_masks()


def test_batch_threshold_iterative_otsu_writes_binary_mask(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_iter",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--iterative-scope", "per-cell", "--gaussian-sigma", "0",
        "--stop-param", "bg-floor.k=2.0",
    ])
    assert rc == 0
    store = DatasetStore(p)
    mask = store.read_mask("SG_iter")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert mask.sum() > 0
    out = capsys.readouterr().out
    assert "iterative-otsu" in out  # [ok] line names the strategy


def test_batch_threshold_iterative_fixed_iterations_writes_mask(tmp_path, capsys):
    # --iterations runs a fixed count and blocks the stop criteria.
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_fixed",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--iterative-scope", "per-cell", "--gaussian-sigma", "0",
        "--iterations", "1",
    ])
    assert rc == 0
    mask = DatasetStore(p).read_mask("SG_fixed")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}


def test_batch_threshold_iterative_unknown_stop_criterion_exits_1(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_iter",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--stop-criteria", "not-a-real-criterion",
    ])
    assert rc == 1
    assert "invalid round configuration" in capsys.readouterr().err


# ── adaptive-clip strategy (U8) ──────────────────────────────────────────


def _make_adaptive_dataset(path: Path, *, pixel_size_um: float | None = 0.12) -> None:
    """One large cell with a non-constant background (so per-cell MAD > 0) and a
    bright blob the per-cell adaptive detector can find."""
    store = DatasetStore(path)
    meta = {"channel_names": ["GFP", "RFP"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)
    img = np.zeros((2, 100, 100), dtype=np.float32)
    rows = np.arange(100).reshape(-1, 1)
    img[0, 20:60, 20:60] = 10 + (rows[20:60] % 3)  # structured background
    img[0, 35:45, 35:45] = 200.0  # bright blob
    store.write_array("intensity", img, attrs={"dims": ["C", "H", "W"]})
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[20:60, 20:60] = 1
    store.write_labels("cellpose", labels)


def test_batch_threshold_adaptive_clip_writes_mask(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ac",
        "--segmentation", "cellpose", "--strategy", "adaptive-clip",
        "--d-min-um", "0.12", "--gaussian-sigma", "1",
    ])
    assert rc == 0
    store = DatasetStore(p)
    mask = store.read_mask("ac")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert mask.sum() > 0
    assert "adaptive-clip" in capsys.readouterr().out  # [ok] line names the strategy


def test_batch_threshold_adaptive_clip_requires_d_min(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ac",
        "--segmentation", "cellpose", "--strategy", "adaptive-clip",
    ])
    assert rc == 1
    assert "--d-min-um" in capsys.readouterr().err


def test_batch_threshold_adaptive_clip_missing_pixel_size_fails(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p, pixel_size_um=None)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ac",
        "--segmentation", "cellpose", "--strategy", "adaptive-clip",
        "--d-min-um", "0.12",
    ])
    assert rc == 1  # the only dataset fails → no successes
    assert "pixel size" in capsys.readouterr().err
    assert "ac" not in DatasetStore(p).list_masks()


# ── auto-extract strategy + guided CNR (U6) ──────────────────────────────


def test_batch_threshold_auto_extract_writes_mask(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
        "--smallest-particle-um", "0.36",
    ])
    assert rc == 0
    store = DatasetStore(p)
    mask = store.read_mask("ae")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask).tolist()) <= {0, 1}
    assert mask.sum() > 0
    out = capsys.readouterr().out
    assert "auto-extract" in out
    assert "smallest=0.36" in out  # the override summary fragment


def test_batch_threshold_auto_extract_autodetect_summary(tmp_path, capsys):
    """The autodetect path (no --smallest-particle-um) prints 'smallest=auto'."""
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
    ])
    assert rc == 0
    assert "smallest=auto" in capsys.readouterr().out


def test_batch_threshold_auto_extract_gaussian_sigma_sets_presmooth(tmp_path, monkeypatch):
    """--gaussian-sigma maps to AutoExtractSettings.presmooth_sigma_px; --smallest-
    particle-um is carried through as the µm override."""
    import percell4.workflows.phases as phases_mod

    captured = {}
    real_compute = phases_mod.threshold_compute_one

    def _capture(store, round_spec, seg_name="cellpose_qc"):
        captured["round"] = round_spec
        return real_compute(store, round_spec, seg_name=seg_name)

    monkeypatch.setattr(phases_mod, "threshold_compute_one", _capture)
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
        "--smallest-particle-um", "0.36", "--gaussian-sigma", "0.5",
    ])
    r = captured["round"]
    assert r.auto_extract is not None
    assert r.auto_extract.presmooth_sigma_px == 0.5
    assert r.auto_extract.smallest_particle_um == 0.36


def test_batch_threshold_auto_extract_autodetect_constructs(tmp_path, monkeypatch):
    """--strategy auto-extract without --smallest-particle-um builds a None override."""
    import percell4.workflows.phases as phases_mod

    captured = {}
    real_compute = phases_mod.threshold_compute_one

    def _capture(store, round_spec, seg_name="cellpose_qc"):
        captured["round"] = round_spec
        return real_compute(store, round_spec, seg_name=seg_name)

    monkeypatch.setattr(phases_mod, "threshold_compute_one", _capture)
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
    ])
    assert captured["round"].auto_extract.smallest_particle_um is None


def test_batch_threshold_cnr_classify_requires_threshold(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
        "--smallest-particle-um", "0.36", "--cnr-classify",
    ])
    assert rc == 1
    assert "--cnr-threshold" in capsys.readouterr().err


def test_batch_threshold_cnr_classify_requires_alc_strategy(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "r1",
        "--segmentation", "cellpose", "--strategy", "grouped-otsu",
        "--cnr-classify", "--cnr-threshold", "5",
    ])
    assert rc == 1
    assert "adaptive-clip or" in capsys.readouterr().err


def _make_adaptive_timelapse_dataset(path: Path, *, n_t: int = 2, pixel_size_um=0.12) -> None:
    """A time-lapse single-channel dataset: a cell with structured background + a blob
    per frame (writing a T-axis intensity sets n_timepoints)."""
    store = DatasetStore(path)
    meta = {"channel_names": ["GFP"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)
    frame = np.zeros((100, 100), dtype=np.float32)
    rows = np.arange(100).reshape(-1, 1)
    frame[20:60, 20:60] = 10 + (rows[20:60] % 3)
    frame[35:45, 35:45] = 200.0
    store.write_array("intensity", np.stack([frame] * n_t, 0), attrs={"dims": ["T", "H", "W"]})
    lab = np.zeros((100, 100), dtype=np.int32)
    lab[20:60, 20:60] = 1
    store.write_labels("cellpose", np.stack([lab] * n_t, 0).astype(np.int32))


def test_batch_threshold_cnr_classify_timelapse_runs_per_frame(tmp_path, capsys):
    """A time-lapse auto-extract + guided CNR run completes (no single-timepoint abort)
    and writes a (T,H,W) base mask + a timepoint-columned /classification table."""
    p = tmp_path / "TL.h5"
    _make_adaptive_timelapse_dataset(p, n_t=2)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
        "--smallest-particle-um", "0.36", "--cnr-classify", "--cnr-threshold", "5",
    ])
    assert rc == 0
    store = DatasetStore(p)
    assert store.read_mask("ae").shape == (2, 100, 100)  # (T,H,W) base mask
    table = store.read_dataframe("/classification/ae")
    assert "timepoint" in table.columns
    assert set(table["timepoint"].unique()) <= {0, 1}


def test_batch_threshold_cnr_classify_writes_classification_table(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
        "--smallest-particle-um", "0.36", "--cnr-classify", "--cnr-threshold", "5",
    ])
    assert rc == 0
    store = DatasetStore(p)
    assert "ae" in store.list_masks()
    table = store.read_dataframe("/classification/ae")  # per-focus table written
    assert len(table) >= 1
    assert "CNR split" in capsys.readouterr().out
    assert "SG_iter" not in DatasetStore(p).list_masks()


# ── px/µm size unit (U11) ────────────────────────────────────────────────


def test_batch_threshold_adaptive_clip_px_unit_runs_without_pixel_size(tmp_path, capsys):
    """--d-min-unit px lets an adaptive round run on a dataset with no pixel size."""
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p, pixel_size_um=None)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ac",
        "--segmentation", "cellpose", "--strategy", "adaptive-clip",
        "--d-min-um", "3", "--d-min-unit", "px",
    ])
    assert rc == 0
    assert DatasetStore(p).read_mask("ac").sum() > 0
    assert "d_min=3 px" in capsys.readouterr().out


def test_batch_threshold_auto_extract_px_unit_runs_without_pixel_size(tmp_path, capsys):
    """--smallest-particle-unit px lets an auto-extract override run with no pixel size."""
    p = tmp_path / "DS1.h5"
    _make_adaptive_dataset(p, pixel_size_um=None)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "ae",
        "--segmentation", "cellpose", "--strategy", "auto-extract",
        "--smallest-particle-um", "3", "--smallest-particle-unit", "px",
    ])
    assert rc == 0
    assert DatasetStore(p).read_mask("ae").sum() > 0
    assert "smallest=3 px" in capsys.readouterr().out


def test_batch_threshold_iterative_malformed_stop_param_exits_1(tmp_path, capsys):
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([
        str(p), "--channel", "GFP", "--round-name", "SG_iter",
        "--segmentation", "cellpose", "--strategy", "iterative-otsu",
        "--stop-param", "bg-floor-k-no-equals",
    ])
    assert rc == 1
    assert "invalid round configuration" in capsys.readouterr().err


def test_batch_threshold_grouped_otsu_default_unchanged(tmp_path):
    # Default strategy still produces the legacy grouped-otsu round (no iterative).
    p = tmp_path / "DS1.h5"
    _make_dataset(p)
    rc = cli.main([str(p), "--channel", "GFP", "--round-name", "GFP_hi",
                   "--segmentation", "cellpose", "--kmeans-n-clusters", "2"])
    assert rc == 0
    assert "GFP_hi" in DatasetStore(p).list_masks()


def test_batch_threshold_import_is_qt_free():
    qt_before = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    from percell4.interfaces.cli import batch_threshold  # noqa: F401
    qt_after = {m for m in sys.modules if "PyQt" in m or "qtpy" in m or "napari" in m}
    assert not (qt_after - qt_before)
