"""Unit tests for the pure phase helpers.

These exercise each helper against small synthetic fixtures built on
top of real :class:`DatasetStore` h5 files. The runner's end-to-end
test lives under ``tests/test_gui_workflows/`` because it pulls in Qt.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.domain.measure.grouper import GroupingResult
from percell4.store import DatasetStore
from percell4.workflows.failures import DatasetFailure
from percell4.workflows.models import (
    AdaptiveClipSettings,
    AutoExtractSettings,
    CellposeSettings,
    CnrClassifySettings,
    DatasetSource,
    IterativeOtsuSettings,
    PunctaDetectorSettings,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)
from percell4.workflows.phases import (
    apply_threshold_headless,
    datasets_without_failures,
    export_run,
    measure_one,
    measure_particles_one,
    record_failure,
    segment_one,
    threshold_compute_one,
    write_staging_parquet,
    write_staging_particles_parquet,
)

# ── Synthetic fixtures ──────────────────────────────────────────────────


def _make_fixture_h5(
    path: Path,
    channel_names: list[str] = None,
    n_cells: int = 12,
    size: int = 100,
) -> DatasetStore:
    """Create a small h5 with a multi-channel intensity cube and diverse cells.

    Layout:
      - (C, size, size) intensity where each channel has distinct values
      - Cells are placed as small squares with known intensities so the
        per-cell metrics are predictable.

    The grouper has a min-cells threshold of 10 before it will produce
    more than one group, so fixtures default to 12 cells on a 100×100
    grid (4 rows × 3 cols).
    """
    channel_names = channel_names or ["GFP", "RFP"]
    store = DatasetStore(path)
    store.create(metadata={"channel_names": channel_names})

    n_ch = len(channel_names)
    intensity = np.zeros((n_ch, size, size), dtype=np.float32)

    # Place `n_cells` 6×6 squares on a grid, each with increasing
    # intensity on channel 0 and random on channel 1.
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        # Channel 0: increasing intensity per cell (for grouping)
        intensity[0, row : row + 6, col : col + 6] = 50 + 30 * i
        if n_ch > 1:
            # Channel 1: positive everywhere, values mix "bright" and "dim"
            # within each cell so Otsu has something to find.
            base = 30 + 10 * (i % 2)
            # Add a few extra-bright pixels so Otsu actually separates them.
            intensity[1, row : row + 6, col : col + 6] = base
            intensity[1, row + 2 : row + 4, col + 2 : col + 4] = base + 80

    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    return store


def _write_synthetic_labels(store: DatasetStore, n_cells: int = 12) -> np.ndarray:
    """Write cellpose_qc labels matching the cell layout in _make_fixture_h5."""
    size = 100
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(n_cells):
        row = 5 + (i // 3) * 22
        col = 5 + (i % 3) * 22
        labels[row : row + 6, col : col + 6] = i + 1
    store.write_labels("cellpose_qc", labels)
    return labels


@pytest.fixture
def fixture_store(tmp_path: Path) -> DatasetStore:
    return _make_fixture_h5(tmp_path / "DS1.h5")


@pytest.fixture
def fixture_store_with_labels(tmp_path: Path) -> DatasetStore:
    store = _make_fixture_h5(tmp_path / "DS1.h5")
    _write_synthetic_labels(store)
    return store


# ── segment_one ─────────────────────────────────────────────────────────


@pytest.mark.slow
def test_segment_one_writes_cellpose_qc(fixture_store):
    """Real Cellpose run — marked slow so CI can skip it if needed."""
    from percell4.adapters.cellpose import build_cellpose_model

    cfg = CellposeSettings(diameter=8.0, gpu=True, min_size=5)
    model = build_cellpose_model(gpu=True)
    labels, failure, msg = segment_one(fixture_store, cfg, cellpose_model=model, channel_idx=0)
    # With tiny synthetic squares and tiny diameter, Cellpose may or may
    # not find them. Either way the dataset is no longer marked failed;
    # an empty result is now a recoverable case that writes empty
    # labels for downstream interactive QC drawing.
    assert failure is None
    assert "cellpose_qc" in fixture_store.list_labels()


def test_segment_one_empty_cellpose_writes_empty_labels_and_succeeds(
    fixture_store_50px, monkeypatch
):
    """Cellpose finding 0 cells is no longer a failure.

    Previously this returned ``DatasetFailure.SEGMENTATION_EMPTY``,
    which routed the dataset around every downstream phase including
    interactive seg QC — leaving the user with no way to draw cells
    manually. Now segment_one writes an all-zeros ``/labels/<seg_name>``
    and returns no failure so the QC step can pick it up.
    """
    from percell4.workflows import phases

    monkeypatch.setattr(phases, "run_cellpose", lambda *a, **kw: np.zeros((50, 50), dtype=np.int32))
    cfg = CellposeSettings(min_size=5)

    labels, failure, msg = segment_one(fixture_store_50px, cfg)

    assert failure is None, f"empty cellpose should not record a failure (got {failure})"
    assert labels.shape == (50, 50)
    assert int(labels.max()) == 0
    # Empty layer was persisted so the QC phase has something to load.
    assert "cellpose_qc" in fixture_store_50px.list_labels()
    persisted = fixture_store_50px.read_labels("cellpose_qc")
    assert persisted.shape == (50, 50)
    assert int(persisted.max()) == 0
    # Message mentions manual drawing so the run-log line is actionable.
    assert "0 cells" in msg
    assert "manual" in msg.lower() or "draw" in msg.lower()


def test_segment_one_applies_saturation_lut_when_pct_positive(fixture_store_50px, monkeypatch):
    """segment_one preprocesses the seg channel with the saturation LUT
    when CellposeSettings.saturation_pct > 0.

    The captured array passed to run_cellpose is the apply_saturation_lut
    output, not the raw store channel.
    """
    from percell4.domain.segmentation.preprocess import apply_saturation_lut
    from percell4.workflows import phases

    captured: dict = {}

    def fake_run(plane, *a, **kw):  # noqa: ARG001
        captured["plane"] = np.array(plane, copy=True)
        return _fake_cellpose_labels_with_edges()

    monkeypatch.setattr(phases, "run_cellpose", fake_run)
    cfg = CellposeSettings(min_size=5, saturation_pct=1.0)

    segment_one(fixture_store_50px, cfg)

    raw = fixture_store_50px.read_channel("intensity", 0)
    expected = apply_saturation_lut(raw, 1.0)
    assert np.array_equal(captured["plane"], expected)


def test_segment_one_skips_lut_when_saturation_pct_zero(fixture_store_50px, monkeypatch):
    """saturation_pct == 0 → segment_one passes the raw channel."""
    from percell4.workflows import phases

    captured: dict = {}

    def fake_run(plane, *a, **kw):  # noqa: ARG001
        captured["plane"] = np.array(plane, copy=True)
        return _fake_cellpose_labels_with_edges()

    monkeypatch.setattr(phases, "run_cellpose", fake_run)
    cfg = CellposeSettings(min_size=5, saturation_pct=0.0)

    segment_one(fixture_store_50px, cfg)

    raw = fixture_store_50px.read_channel("intensity", 0)
    assert np.array_equal(captured["plane"], raw)


def test_segment_one_applies_gaussian_blur_after_saturation(
    fixture_store_50px, monkeypatch
):
    """blur_sigma > 0 → segment_one feeds run_cellpose the
    saturation-LUT-then-Gaussian-blur output, in that order."""
    from percell4.domain.segmentation.preprocess import (
        apply_gaussian_blur,
        apply_saturation_lut,
    )
    from percell4.workflows import phases

    captured: dict = {}

    def fake_run(plane, *a, **kw):  # noqa: ARG001
        captured["plane"] = np.array(plane, copy=True)
        return _fake_cellpose_labels_with_edges()

    monkeypatch.setattr(phases, "run_cellpose", fake_run)
    cfg = CellposeSettings(min_size=5, saturation_pct=1.0, blur_sigma=1.5)

    segment_one(fixture_store_50px, cfg)

    raw = fixture_store_50px.read_channel("intensity", 0)
    expected = apply_gaussian_blur(apply_saturation_lut(raw, 1.0), 1.5)
    assert np.array_equal(captured["plane"], expected)


def test_segment_one_skips_blur_when_sigma_zero(
    fixture_store_50px, monkeypatch
):
    """blur_sigma == 0 → no blur; the saturated (here raw) channel passes
    through unchanged."""
    from percell4.workflows import phases

    captured: dict = {}

    def fake_run(plane, *a, **kw):  # noqa: ARG001
        captured["plane"] = np.array(plane, copy=True)
        return _fake_cellpose_labels_with_edges()

    monkeypatch.setattr(phases, "run_cellpose", fake_run)
    cfg = CellposeSettings(min_size=5, saturation_pct=0.0, blur_sigma=0.0)

    segment_one(fixture_store_50px, cfg)

    raw = fixture_store_50px.read_channel("intensity", 0)
    assert np.array_equal(captured["plane"], raw)


def test_segment_one_handles_read_error(tmp_path):
    """An empty h5 (no /intensity) should return SEGMENTATION_ERROR."""
    store = DatasetStore(tmp_path / "empty.h5")
    store.create(metadata={})
    cfg = CellposeSettings()

    labels, failure, msg = segment_one(store, cfg)

    assert failure is DatasetFailure.SEGMENTATION_ERROR
    assert "read /intensity failed" in msg


# ── segment_one × edge_mode ─────────────────────────────────────────────


def _fake_cellpose_labels_with_edges() -> np.ndarray:
    """A 50×50 label array with 4 cells, 2 touching borders.

    Cells:
      - label 1: top-left corner (0:8, 0:8) — touches top + left edges
      - label 2: interior (20:30, 20:30) — area=100
      - label 3: interior (15:18, 15:18) — area=9
      - label 4: bottom-right corner (42:50, 42:50) — touches bottom + right
    """
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[0:8, 0:8] = 1
    labels[20:30, 20:30] = 2
    labels[15:18, 15:18] = 3
    labels[42:50, 42:50] = 4
    return labels


@pytest.fixture
def fixture_store_50px(tmp_path: Path) -> DatasetStore:
    """A 50×50 h5 store sized for the edge-mode fixture labels."""
    return _make_fixture_h5(tmp_path / "edge_ds.h5", n_cells=4, size=50)


def test_segment_one_exclude_mode_removes_edge_cells(fixture_store_50px, monkeypatch):
    """Default EXCLUDE mode preserves today's filter-edge invariant."""
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    monkeypatch.setattr(phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges())
    cfg = CellposeSettings(min_size=5)

    labels, failure, _msg = segment_one(fixture_store_50px, cfg, edge_mode=EdgeMode.EXCLUDE)

    assert failure is None
    # Cells 1 and 4 (edge-touching) were removed; cells 2 and 3 survive
    # and get sequential relabeling (1, 2).
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 2  # exactly 2 cells remain


def test_segment_one_include_normal_keeps_edge_cells(fixture_store_50px, monkeypatch):
    """INCLUDE_AS_NORMAL mode keeps edge cells in labels."""
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    monkeypatch.setattr(phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges())
    cfg = CellposeSettings(min_size=5)

    labels, failure, _msg = segment_one(
        fixture_store_50px, cfg, edge_mode=EdgeMode.INCLUDE_AS_NORMAL
    )

    assert failure is None
    # All 4 cells survive (cell 3 area=9 > min_size=5; no edge filter).
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 4


def test_segment_one_size_normalized_cohort_keeps_edge_cells(fixture_store_50px, monkeypatch):
    """INCLUDE_AS_SIZE_NORMALIZED_COHORT mode also keeps edge cells in Phase 1.

    The cohort treatment is a measure-time concern (U4), not a Phase 1
    concern — Phase 1 just preserves the edge cells in labels.
    """
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    monkeypatch.setattr(phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges())
    cfg = CellposeSettings(min_size=5)

    labels, failure, _msg = segment_one(
        fixture_store_50px,
        cfg,
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )

    assert failure is None
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 4


def test_segment_one_default_edge_mode_is_exclude(fixture_store_50px, monkeypatch):
    """Calling segment_one without edge_mode preserves the today's-behavior default."""
    from percell4.workflows import phases

    monkeypatch.setattr(phases, "run_cellpose", lambda *a, **kw: _fake_cellpose_labels_with_edges())
    cfg = CellposeSettings(min_size=5)

    # No edge_mode kwarg — default EXCLUDE
    labels, failure, _msg = segment_one(fixture_store_50px, cfg)

    assert failure is None
    unique = set(np.unique(labels).tolist()) - {0}
    assert len(unique) == 2  # edge cells removed


def test_segment_one_include_normal_with_no_edge_cells_matches_exclude(
    fixture_store_50px, monkeypatch
):
    """When labels have no edge-touching cells, all three modes produce the same output."""
    from percell4.workflows import phases
    from percell4.workflows.models import EdgeMode

    # All cells are well inside the image
    interior_labels = np.zeros((50, 50), dtype=np.int32)
    interior_labels[10:20, 10:20] = 1
    interior_labels[30:40, 30:40] = 2

    monkeypatch.setattr(phases, "run_cellpose", lambda *a, **kw: interior_labels.copy())
    cfg = CellposeSettings(min_size=5)

    out_exclude, _, _ = segment_one(fixture_store_50px, cfg, edge_mode=EdgeMode.EXCLUDE)
    out_normal, _, _ = segment_one(fixture_store_50px, cfg, edge_mode=EdgeMode.INCLUDE_AS_NORMAL)

    # Both produce the same sequential labels [1, 2]
    np.testing.assert_array_equal(out_exclude, out_normal)
    assert set(np.unique(out_exclude).tolist()) - {0} == {1, 2}


# ── threshold_compute_one ───────────────────────────────────────────────


def test_threshold_compute_kmeans_happy_path(fixture_store_with_labels):
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    result, failure, msg = threshold_compute_one(fixture_store_with_labels, round_spec)
    assert failure is None
    assert isinstance(result, GroupingResult)
    # With 6 cells of varying intensity, k-means should produce 2 groups.
    assert result.n_groups == 2


def test_threshold_compute_unknown_channel(fixture_store_with_labels):
    round_spec = ThresholdingRound(
        name="bogus",
        channel="NotAChannel",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    result, failure, msg = threshold_compute_one(fixture_store_with_labels, round_spec)
    assert result is None
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "NotAChannel" in msg


def test_threshold_compute_empty_labels(tmp_path):
    store = _make_fixture_h5(tmp_path / "empty_labels.h5")
    store.write_labels("cellpose_qc", np.zeros((60, 60), dtype=np.int32))
    round_spec = ThresholdingRound(
        name="r",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    result, failure, msg = threshold_compute_one(store, round_spec)
    assert result is None
    assert failure is DatasetFailure.THRESHOLD_EMPTY


# ── threshold_compute_one: adaptive-clip trivial grouping (U7) ───────────


def _adaptive_round(**overrides) -> ThresholdingRound:
    defaults = dict(
        name="ac",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.40),
    )
    defaults.update(overrides)
    return ThresholdingRound(**defaults)


def test_threshold_compute_adaptive_trivial_grouping(fixture_store_with_labels):
    """An adaptive round returns a single-group result over all cells."""
    result, failure, msg = threshold_compute_one(fixture_store_with_labels, _adaptive_round())
    assert failure is None
    assert isinstance(result, GroupingResult)
    assert result.n_groups == 1
    # Every cell is assigned to group 1.
    assert set(result.group_assignments.unique()) == {1}
    assert len(result.group_assignments) == 12


def test_threshold_compute_adaptive_bypasses_cluster_gate(tmp_path):
    """Adaptive rounds are not gated by clustering — a single-cell dataset that
    grouped-Otsu would drop as THRESHOLD_EMPTY still yields a trivial grouping."""
    store = _make_fixture_h5(tmp_path / "one_cell.h5", n_cells=1)
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[5:11, 5:11] = 1
    store.write_labels("cellpose_qc", labels)
    result, failure, msg = threshold_compute_one(store, _adaptive_round())
    assert failure is None
    assert result.n_groups == 1
    assert set(result.group_assignments.unique()) == {1}


def test_threshold_compute_adaptive_empty_labels(tmp_path):
    """No cells is still THRESHOLD_EMPTY even for an adaptive round."""
    store = _make_fixture_h5(tmp_path / "empty.h5")
    store.write_labels("cellpose_qc", np.zeros((60, 60), dtype=np.int32))
    result, failure, msg = threshold_compute_one(store, _adaptive_round())
    assert result is None
    assert failure is DatasetFailure.THRESHOLD_EMPTY


# ── apply_threshold_headless: adaptive-clip (U2) ─────────────────────────


def _make_adaptive_store(path: Path, pixel_size_um: float | None = 0.12) -> DatasetStore:
    """One large cell with a non-constant background (so per-cell MAD > 0) and a
    bright blob that the per-cell adaptive detector should pick up."""
    store = DatasetStore(path)
    meta = {"channel_names": ["GFP"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)

    img = np.zeros((1, 100, 100), dtype=np.float32)
    rows = np.arange(100).reshape(-1, 1)
    # Low-level structured background (10/11/12) so MAD is non-zero inside cells.
    img[0, 20:60, 20:60] = 10 + (rows[20:60] % 3)
    img[0, 35:45, 35:45] = 200.0  # bright blob well above k*sigma
    store.write_array("intensity", img, attrs={"dims": ["C", "H", "W"]})

    labels = np.zeros((100, 100), dtype=np.int32)
    labels[20:60, 20:60] = 1
    store.write_labels("cellpose_qc", labels)
    return store


def _adaptive_apply_round(**overrides) -> ThresholdingRound:
    defaults = dict(
        name="ac",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=1.0,
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.12),
    )
    defaults.update(overrides)
    return ThresholdingRound(**defaults)


def test_apply_adaptive_clip_writes_binary_mask_and_degenerate_groups(tmp_path):
    store = _make_adaptive_store(tmp_path / "ac.h5")
    round_spec = _adaptive_apply_round()
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg

    mask = store.read_mask("ac")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    assert mask.sum() > 0  # the bright blob is detected
    # Foreground only inside the cell.
    assert mask[:20].sum() == 0 and mask[60:].sum() == 0
    # /groups is a single degenerate group.
    groups = store.read_dataframe("/groups/ac")
    assert set(groups["group_GFP_mean_intensity"].unique()) == {1}


def test_apply_adaptive_clip_missing_pixel_size_fails_dataset(tmp_path):
    store = _make_adaptive_store(tmp_path / "no_ps.h5", pixel_size_um=None)
    round_spec = _adaptive_apply_round()
    grouping, _, _ = threshold_compute_one(store, round_spec)
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "pixel size" in msg
    assert "ac" not in store.list_masks()


def test_adaptive_clip_px_unit_runs_without_pixel_size(tmp_path):
    """U9: in px mode an adaptive round needs no dataset pixel size — the d_min
    value is taken as pixels directly."""
    store = _make_adaptive_store(tmp_path / "px.h5", pixel_size_um=None)
    round_spec = _adaptive_apply_round(
        adaptive_clip=AdaptiveClipSettings(d_min_um=3.0, d_min_unit="px")
    )
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg
    assert store.read_mask("ac").sum() > 0


def test_adaptive_clip_px_unit_oversized_window_fails_cleanly(tmp_path):
    """U9: px mode still guards the window (via eff_pixel=1.0) — a huge px d_min
    whose derived window exceeds the frame fails cleanly, no pixel size involved."""
    from percell4.workflows.phases import _apply_adaptive_clip_cells

    store = _make_adaptive_store(tmp_path / "px_big.h5", pixel_size_um=None)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    settings = AdaptiveClipSettings(d_min_um=500.0, d_min_unit="px")  # window ≫ 100px frame
    combined = np.zeros(labels.shape, dtype=np.uint8)
    err = _apply_adaptive_clip_cells(image, labels, settings, combined, None, "ac")
    assert "exceeds the image" in err
    assert combined.sum() == 0


def test_apply_adaptive_clip_zero_pixel_size_fails_dataset(tmp_path):
    store = _make_adaptive_store(tmp_path / "zero_ps.h5", pixel_size_um=0.0)
    round_spec = _adaptive_apply_round()
    grouping, _, _ = threshold_compute_one(store, round_spec)
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "pixel size" in msg  # pins the adaptive guard, not some unrelated failure


def test_apply_adaptive_clip_absurd_pixel_size_fails_dataset(tmp_path):
    """An absurd-but-positive pixel size blows the window past the frame; fail
    the dataset rather than silently writing an empty mask."""
    store = _make_adaptive_store(tmp_path / "absurd.h5", pixel_size_um=1e-3)
    round_spec = _adaptive_apply_round()
    grouping, _, _ = threshold_compute_one(store, round_spec)
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "window" in msg
    assert "ac" not in store.list_masks()


def test_apply_adaptive_clip_presmooth_defaults_to_one_not_round_sigma(tmp_path):
    """Regression: the adaptive presmooth comes from AdaptiveClipSettings
    (default 1.0), NOT the round's grouped-Otsu gaussian_sigma (default 0). A
    round with gaussian_sigma=0 must still presmooth at 1 px."""
    from percell4.domain.measure.adaptive_clip import detect_adaptive_by_particle_size
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    store = _make_adaptive_store(tmp_path / "presmooth.h5")
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    ps = float(store.metadata["pixel_size_um"])

    # gaussian_sigma=0 (the grouped default) but adaptive presmooth defaults to 1.
    round_spec = _adaptive_apply_round(gaussian_sigma=0.0)
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))
    mask, _gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, ps)
    assert err == ""
    expected = detect_adaptive_by_particle_size(
        image, labels, ps, 0.12, k=1.0, presmooth_sigma_px=1.0
    )
    assert np.array_equal(mask, expected)
    # And NOT the sigma=0 (no-presmooth) result, which differs on noisy data.
    no_presmooth = detect_adaptive_by_particle_size(
        image, labels, ps, 0.12, k=1.0, presmooth_sigma_px=0.0
    )
    assert not np.array_equal(expected, no_presmooth)


# ── apply_threshold_headless: auto-extraction (U3) ───────────────────────


def _make_auto_extract_store(path: Path, pixel_size_um: float | None = 0.12) -> DatasetStore:
    """One large cell with structured background (per-cell MAD > 0) and a bright
    blob the two-pass auto-extractor should pick up."""
    store = DatasetStore(path)
    meta = {"channel_names": ["GFP"]}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)

    img = np.zeros((1, 100, 100), dtype=np.float32)
    rows = np.arange(100).reshape(-1, 1)
    img[0, 20:60, 20:60] = 10 + (rows[20:60] % 3)
    img[0, 35:45, 35:45] = 200.0  # bright blob well above k*sigma
    store.write_array("intensity", img, attrs={"dims": ["C", "H", "W"]})

    labels = np.zeros((100, 100), dtype=np.int32)
    labels[20:60, 20:60] = 1
    store.write_labels("cellpose_qc", labels)
    return store


def _auto_extract_round(**overrides) -> ThresholdingRound:
    defaults = dict(
        name="ae",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=1.0,
        auto_extract=AutoExtractSettings(smallest_particle_um=0.36),
    )
    defaults.update(overrides)
    return ThresholdingRound(**defaults)


def test_auto_extract_writes_binary_mask_and_degenerate_groups(tmp_path):
    store = _make_auto_extract_store(tmp_path / "ae.h5")
    round_spec = _auto_extract_round()
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg

    mask = store.read_mask("ae")
    assert mask.dtype == np.uint8
    assert set(np.unique(mask)).issubset({0, 1})
    assert mask.sum() > 0  # the bright blob is detected
    assert mask[:20].sum() == 0 and mask[60:].sum() == 0  # inside the cell only
    groups = store.read_dataframe("/groups/ae")
    assert set(groups["group_GFP_mean_intensity"].unique()) == {1}


def test_auto_extract_trivial_grouping_bypasses_cluster_gate(tmp_path):
    """An auto-extract round short-circuits grouping — a single-cell dataset that
    grouped-Otsu would drop still yields a trivial grouping so apply runs."""
    store = _make_fixture_h5(tmp_path / "one_cell.h5", n_cells=1)
    labels = np.zeros((100, 100), dtype=np.int32)
    labels[5:11, 5:11] = 1
    store.write_labels("cellpose_qc", labels)
    result, failure, _ = threshold_compute_one(store, _auto_extract_round())
    assert failure is None
    assert result.n_groups == 1
    assert set(result.group_assignments.unique()) == {1}


def test_auto_extract_um_override_converts_to_px(tmp_path):
    """A µm smallest override reaches auto_extract as (µm / pixel_size_um) px."""
    from unittest.mock import patch

    import percell4.domain.measure.auto_extraction as ae_mod
    from percell4.workflows import phases as phases_mod

    store = _make_auto_extract_store(tmp_path / "px.h5", pixel_size_um=0.12)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    settings = AutoExtractSettings(smallest_particle_um=0.36)

    captured = {}
    real = ae_mod.auto_extract

    def _spy(img, lab, *, smallest_particle_px=None, **kw):
        captured["px"] = smallest_particle_px
        return real(img, lab, smallest_particle_px=smallest_particle_px, **kw)

    combined = np.zeros(labels.shape, dtype=np.uint8)
    with patch.object(ae_mod, "auto_extract", _spy):
        err = phases_mod._apply_auto_extract_cells(image, labels, settings, combined, 0.12, "ae")
    assert err == ""
    assert captured["px"] == pytest.approx(0.36 / 0.12)  # 3.0 px


def test_auto_extract_um_override_missing_pixel_size_fails(tmp_path):
    """A µm override with no pixel size fails cleanly (never defaults to 1 µm/px)."""
    store = _make_auto_extract_store(tmp_path / "no_ps.h5", pixel_size_um=None)
    round_spec = _auto_extract_round(auto_extract=AutoExtractSettings(smallest_particle_um=0.36))
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR
    assert "pixel size" in msg
    assert "ae" not in store.list_masks()


def test_auto_extract_px_unit_runs_without_pixel_size(tmp_path):
    """U9: a px smallest-particle override needs no dataset pixel size."""
    store = _make_auto_extract_store(tmp_path / "px.h5", pixel_size_um=None)
    round_spec = _auto_extract_round(
        auto_extract=AutoExtractSettings(smallest_particle_um=3.0, smallest_particle_unit="px")
    )
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg
    assert store.read_mask("ae").sum() > 0


def test_auto_extract_px_unit_oversized_window_fails_cleanly(tmp_path):
    """U9: px mode auto-extract still guards the fine window, with no pixel size."""
    from percell4.workflows.phases import _apply_auto_extract_cells

    store = _make_auto_extract_store(tmp_path / "px_big.h5", pixel_size_um=None)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    # px=40 → fine window ≈ 3×40 = 120 > 100px frame → guard fires; no pixel size.
    settings = AutoExtractSettings(smallest_particle_um=40.0, smallest_particle_unit="px")
    combined = np.zeros(labels.shape, dtype=np.uint8)
    err = _apply_auto_extract_cells(image, labels, settings, combined, None, "ae")
    assert "fine window" in err
    assert "exceeds the image" in err
    assert combined.sum() == 0


def test_auto_extract_autodetect_not_blocked_by_missing_pixel_size(tmp_path):
    """An auto-detect round (no µm override) is NOT failed by the pixel-size guard
    even with no pixel size — auto-detect is px-native and needs none."""
    store = _make_auto_extract_store(tmp_path / "autodetect.h5", pixel_size_um=None)
    round_spec = _auto_extract_round(auto_extract=AutoExtractSettings())  # None override
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    _failure, msg = apply_threshold_headless(store, round_spec, grouping)
    # It must NOT be blocked for lacking a pixel size; success or a detection-side
    # failure is fine, but never the pixel-size guard.
    assert "pixel size" not in msg


def test_auto_extract_presmooth_is_settings_default_not_round_sigma(tmp_path):
    """R10 regression: presmooth comes from AutoExtractSettings (1.0), NOT the
    round's grouped-Otsu gaussian_sigma (0) — proven by spying the auto_extract call,
    and the result is not a silent empty mask."""
    from unittest.mock import patch

    import percell4.domain.measure.auto_extraction as ae_mod
    from percell4.workflows import phases as phases_mod

    store = _make_auto_extract_store(tmp_path / "presmooth.h5")
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    ps = float(store.metadata["pixel_size_um"])
    settings = AutoExtractSettings(smallest_particle_um=0.36)  # presmooth defaults to 1.0

    captured = {}
    real = ae_mod.auto_extract

    def _spy(img, lab, *, smallest_particle_px=None, presmooth_sigma_px=1.0, **kw):
        captured["presmooth"] = presmooth_sigma_px
        return real(
            img, lab, smallest_particle_px=smallest_particle_px,
            presmooth_sigma_px=presmooth_sigma_px, **kw,
        )

    combined = np.zeros(labels.shape, dtype=np.uint8)
    with patch.object(ae_mod, "auto_extract", _spy):
        err = phases_mod._apply_auto_extract_cells(image, labels, settings, combined, ps, "ae")
    assert err == ""
    assert captured["presmooth"] == 1.0  # NOT 0.0 (the round's gaussian_sigma default)
    assert combined.sum() > 0  # catches the silent-empty-mask trap


def test_apply_auto_extract_is_bit_identical_to_bare_detector(tmp_path):
    """R10 parity: the apply branch equals a direct auto_extract call with the
    settings' presmooth_sigma_px — driven through _apply_threshold_frame with the
    round's grouped-Otsu gaussian_sigma=0 and a NON-default presmooth (2.0). Catches
    the dispatcher feeding `smoothed` instead of the raw image, or a wrong-sigma wire."""
    from percell4.domain.measure.auto_extraction import auto_extract
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    store = _make_auto_extract_store(tmp_path / "parity.h5")
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    ps = float(store.metadata["pixel_size_um"])
    round_spec = _auto_extract_round(
        gaussian_sigma=0.0,
        auto_extract=AutoExtractSettings(smallest_particle_um=0.36, presmooth_sigma_px=2.0),
    )
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))
    mask, _gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, ps)
    assert err == ""
    expected, _ = auto_extract(
        image, labels, smallest_particle_px=0.36 / ps, presmooth_sigma_px=2.0
    )
    assert np.array_equal(mask, expected)


def test_apply_auto_extract_threads_round_min_size_to_min_spot(tmp_path):
    """GUI parity: a workflow auto-extract round threads its Min size into
    auto_extract's ``min_spot_px`` — the GUI 'Adaptive Local Clipping' panel passes
    its Min-particle-size the same way — instead of the hardcoded default 2."""
    from unittest.mock import patch

    import percell4.domain.measure.auto_extraction as ae_mod
    from percell4.workflows import phases as phases_mod
    from percell4.workflows.phases import _trivial_grouping

    store = _make_auto_extract_store(tmp_path / "minspot.h5", pixel_size_um=None)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    round_spec = _auto_extract_round(
        gaussian_sigma=0.0,
        auto_extract=AutoExtractSettings(smallest_particle_um=3.0, smallest_particle_unit="px"),
        min_particle_size=7.0,
        min_particle_size_unit="px",
    )
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))

    captured = {}
    real = ae_mod.auto_extract

    def _spy(img, lab, *, min_spot_px=2, **kw):
        captured["min_spot_px"] = min_spot_px
        return real(img, lab, min_spot_px=min_spot_px, **kw)

    with patch.object(ae_mod, "auto_extract", _spy):
        mask, _gdf, err = phases_mod._apply_threshold_frame(
            image, labels, grouping, round_spec, None
        )
    assert err == ""
    assert captured["min_spot_px"] == 7  # the round's Min size, not the default 2


def test_apply_auto_extract_min_size_unset_keeps_detector_default(tmp_path):
    """Backward-compat: an auto-extract round with no Min size (0) leaves
    auto_extract's own default min_spot_px untouched (not forced to 0)."""
    from unittest.mock import patch

    import percell4.domain.measure.auto_extraction as ae_mod
    from percell4.workflows import phases as phases_mod
    from percell4.workflows.phases import _trivial_grouping

    store = _make_auto_extract_store(tmp_path / "default.h5", pixel_size_um=None)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    round_spec = _auto_extract_round(
        gaussian_sigma=0.0,
        auto_extract=AutoExtractSettings(smallest_particle_um=3.0, smallest_particle_unit="px"),
    )  # min_particle_size defaults to 0
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))

    captured = {}
    real = ae_mod.auto_extract

    def _spy(img, lab, *, min_spot_px=2, **kw):
        captured["min_spot_px"] = min_spot_px
        return real(img, lab, min_spot_px=min_spot_px, **kw)

    with patch.object(ae_mod, "auto_extract", _spy):
        _mask, _gdf, err = phases_mod._apply_threshold_frame(
            image, labels, grouping, round_spec, None
        )
    assert err == ""
    assert captured["min_spot_px"] == 2  # detector default, unchanged


def test_apply_auto_extract_with_min_size_is_bit_identical_to_bare(tmp_path):
    """The apply branch with a Min size equals a direct auto_extract call passing
    that value as min_spot_px — full GUI parity, no extra post-filter divergence."""
    from percell4.domain.measure.auto_extraction import auto_extract
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    store = _make_auto_extract_store(tmp_path / "id.h5", pixel_size_um=None)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    round_spec = _auto_extract_round(
        gaussian_sigma=0.0,
        auto_extract=AutoExtractSettings(
            smallest_particle_um=3.0, smallest_particle_unit="px", presmooth_sigma_px=1.0
        ),
        min_particle_size=5.0,
        min_particle_size_unit="px",
    )
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))
    mask, _gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, None)
    assert err == ""
    expected, _ = auto_extract(
        image, labels, smallest_particle_px=3.0, presmooth_sigma_px=1.0, min_spot_px=5
    )
    assert np.array_equal(mask, expected)


def test_auto_extract_oversized_window_fails_cleanly(tmp_path):
    """The plausibility guard bounds the FINE WINDOW (≈3× smallest), not the diameter:
    a smallest-particle whose px diameter < frame but whose ≈3× window > frame must
    fail cleanly rather than silently produce a degenerate global-clip mask."""
    from percell4.workflows.phases import _apply_auto_extract_cells

    store = _make_auto_extract_store(tmp_path / "oversized.h5", pixel_size_um=0.12)
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    # px = 4.8 / 0.12 = 40 on a 100px frame → diameter 40 < 100 (a diameter guard
    # would miss it) but window ≈ 3×40 = 120 > 100 (the window guard fires).
    settings = AutoExtractSettings(smallest_particle_um=4.8)
    combined = np.zeros(labels.shape, dtype=np.uint8)
    err = _apply_auto_extract_cells(image, labels, settings, combined, 0.12, "ae")
    assert "fine window" in err
    assert "exceeds the image" in err
    assert combined.sum() == 0


# ── apply_threshold_headless: guided CNR post-step (U4) ──────────────────


def _cnr_round(**overrides) -> ThresholdingRound:
    defaults = dict(
        name="ae",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.12),
        cnr_classify=CnrClassifySettings(threshold=5.0),
    )
    defaults.update(overrides)
    return ThresholdingRound(**defaults)


def _fake_two_pop_result():
    from percell4.domain.measure.cnr_classification import ClassificationResult

    labels_image = np.zeros((100, 100), dtype=np.int32)
    labels_image[36:40, 36:40] = 1  # low-CNR population
    labels_image[44:48, 44:48] = 2  # high-CNR population
    components = [
        {"label": 1, "cell": 1, "cnr": 3.0, "subpopulation": 1},
        {"label": 2, "cell": 1, "cnr": 8.0, "subpopulation": 2},
    ]
    return ClassificationResult(
        n_subpopulations=2, labels_image=labels_image, components=components,
        split_axis="cnr", threshold=5.0, report={},
    )


def _fake_one_pop_result():
    from percell4.domain.measure.cnr_classification import ClassificationResult

    labels_image = np.zeros((100, 100), dtype=np.int32)
    labels_image[36:40, 36:40] = 1
    components = [{"label": 1, "cell": 1, "cnr": 3.0, "subpopulation": 1}]
    return ClassificationResult(
        n_subpopulations=1, labels_image=labels_image, components=components,
        split_axis=None, threshold=None, report={},
    )


def _patch_classify(monkeypatch, result):
    """Patch the domain classify_by_cnr so the PHASES wiring (split / naming /
    stale-delete / table) is tested deterministically; the split logic itself is
    covered in tests/test_measure/test_cnr_classification.py."""
    import percell4.domain.measure.cnr_classification as cnr_mod

    def _fake(image, feature_mask, cell_labels, *, threshold=None, presmooth_sigma_px=1.0):
        return result

    monkeypatch.setattr(cnr_mod, "classify_by_cnr", _fake)


def test_cnr_two_population_writes_low_high_and_table(tmp_path, monkeypatch):
    store = _make_auto_extract_store(tmp_path / "cnr2.h5")
    round_spec = _cnr_round()
    _patch_classify(monkeypatch, _fake_two_pop_result())
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg
    masks = store.list_masks()
    assert "ae" in masks  # base feature mask still written
    assert "ae_low" in masks and "ae_high" in masks
    assert set(np.unique(store.read_mask("ae_low"))).issubset({0, 1})
    table = store.read_dataframe("/classification/ae")
    assert len(table) == 2
    assert "split into" in msg
    assert "CNR cutoff 5.00" in msg  # guided cutoff (5.0 from the fake) is logged


def test_cnr_single_population_writes_no_extra_masks(tmp_path, monkeypatch):
    store = _make_auto_extract_store(tmp_path / "cnr1.h5")
    round_spec = _cnr_round()
    _patch_classify(monkeypatch, _fake_one_pop_result())
    grouping, _, _ = threshold_compute_one(store, round_spec)
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg
    masks = store.list_masks()
    assert "ae" in masks
    assert "ae_low" not in masks and "ae_high" not in masks
    assert len(store.read_dataframe("/classification/ae")) == 1  # table still written
    assert "single population" in msg


def test_cnr_stale_population_masks_deleted_on_reclassify(tmp_path, monkeypatch):
    """A 2→1 population re-run leaves no stale _low/_high masks."""
    store = _make_auto_extract_store(tmp_path / "stale.h5")
    round_spec = _cnr_round()
    _patch_classify(monkeypatch, _fake_two_pop_result())
    grouping, _, _ = threshold_compute_one(store, round_spec)
    apply_threshold_headless(store, round_spec, grouping)
    assert "ae_low" in store.list_masks() and "ae_high" in store.list_masks()

    _patch_classify(monkeypatch, _fake_one_pop_result())
    grouping, _, _ = threshold_compute_one(store, round_spec)
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg
    assert "ae_low" not in store.list_masks()
    assert "ae_high" not in store.list_masks()


def test_cnr_table_write_failure_does_not_lose_masks(tmp_path, monkeypatch):
    store = _make_auto_extract_store(tmp_path / "tablefail.h5")
    round_spec = _cnr_round()
    _patch_classify(monkeypatch, _fake_two_pop_result())
    grouping, _, _ = threshold_compute_one(store, round_spec)

    real_write_df = store.write_dataframe

    def _selective_write(path, df):
        if path.startswith("/classification/"):
            raise RuntimeError("disk full")
        return real_write_df(path, df)

    monkeypatch.setattr(store, "write_dataframe", _selective_write)
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None  # masks survive a table failure
    assert "ae_low" in store.list_masks() and "ae_high" in store.list_masks()
    assert "table write FAILED" in msg


def test_cnr_forced_mode_overrides_threshold_with_n_populations_2(tmp_path, monkeypatch):
    """The GMM 2-pop (forced) checkbox routes to classify_by_cnr(n_populations=2)
    and never passes the guided threshold — the two-group split overrides it."""
    import percell4.domain.measure.cnr_classification as cnr_mod

    calls: list[dict] = []

    def _fake(image, feature_mask, cell_labels, *, threshold=None,
              n_populations="auto", presmooth_sigma_px=1.0):
        calls.append({"threshold": threshold, "n_populations": n_populations})
        return _fake_two_pop_result()

    monkeypatch.setattr(cnr_mod, "classify_by_cnr", _fake)

    store = _make_auto_extract_store(tmp_path / "forced.h5")
    # threshold value is present but must be ignored in forced mode.
    round_spec = _cnr_round(cnr_classify=CnrClassifySettings(threshold=5.0, forced=True))
    grouping, failure, _ = threshold_compute_one(store, round_spec)
    assert failure is None
    failure, msg = apply_threshold_headless(store, round_spec, grouping)
    assert failure is None, msg
    assert len(calls) == 1
    assert calls[0]["threshold"] is None  # threshold overridden
    assert calls[0]["n_populations"] == 2
    assert "ae_low" in store.list_masks() and "ae_high" in store.list_masks()
    # The GMM-found CNR cutoff between the two populations is logged (5.0 from the fake).
    assert "GMM CNR cutoff 5.00" in msg


def test_apply_adaptive_clip_is_bit_identical_to_bare_detector(tmp_path):
    """Guards parity: the apply branch must equal a direct detector call with the
    settings' presmooth_sigma_px — including a presmooth != 1 case."""
    from percell4.domain.measure.adaptive_clip import detect_adaptive_by_particle_size
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    store = _make_adaptive_store(tmp_path / "parity.h5")
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    ps = float(store.metadata["pixel_size_um"])

    for presmooth in (1.0, 2.0):  # presmooth != 1 would diverge if it were fixed
        round_spec = _adaptive_apply_round(
            adaptive_clip=AdaptiveClipSettings(d_min_um=0.12, presmooth_sigma_px=presmooth)
        )
        grouping = _trivial_grouping(np.array([1], dtype=np.int32))
        mask, _gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, ps)
        assert err == ""
        expected = detect_adaptive_by_particle_size(
            image, labels, ps, 0.12, k=1.0, presmooth_sigma_px=presmooth
        )
        assert np.array_equal(mask, expected)


def test_apply_adaptive_clip_global_sigma_threads_to_detector(tmp_path):
    """A global-σ round applies the pooled-σ detector — bit-identical to a direct
    detector call with global_sigma=True."""
    from percell4.domain.measure.adaptive_clip import detect_adaptive_by_particle_size
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    store = _make_adaptive_store(tmp_path / "global.h5")
    image = store.read_channel("intensity", 0)
    labels = store.read_labels("cellpose_qc")
    ps = float(store.metadata["pixel_size_um"])

    round_spec = _adaptive_apply_round(
        adaptive_clip=AdaptiveClipSettings(d_min_um=0.12, global_sigma=True)
    )
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))
    mask, _gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, ps)
    assert err == ""
    expected = detect_adaptive_by_particle_size(
        image, labels, ps, 0.12, k=1.0, global_sigma=True
    )
    assert np.array_equal(mask, expected)


def _min_size_inputs():
    """One whole-field cell with two bright blobs: 9 px (3×3) and 100 px (10×10)."""
    labels = np.ones((40, 40), dtype=np.int32)
    image = np.zeros((40, 40), dtype=np.float32)
    image[2:5, 2:5] = 100.0  # 9 px small blob
    image[10:20, 10:20] = 100.0  # 100 px large blob
    return image, labels


def test_apply_threshold_frame_min_particle_size_drops_small_components():
    """A round's px min particle size drops connected components below that area
    from the produced mask (applies to every method, here grouped Otsu)."""
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    image, labels = _min_size_inputs()
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))

    unfiltered = ThresholdingRound(
        name="r",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=0.0,  # no presmoothing so blob areas stay exact
    )
    mask0, _g0, err0 = _apply_threshold_frame(image, labels, grouping, unfiltered)
    assert err0 == ""
    assert int(mask0.sum()) == 109  # both blobs

    filtered = ThresholdingRound(
        name="r",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=0.0,
        min_particle_size=50,
        min_particle_size_unit="px",
    )
    mask1, _g1, err1 = _apply_threshold_frame(image, labels, grouping, filtered)
    assert err1 == ""
    assert int(mask1.sum()) == 100  # only the 100 px blob survives


def test_apply_threshold_frame_min_size_um2_needs_pixel_size():
    """A µm² min particle size on a dataset with no pixel size fails the frame
    cleanly rather than silently defaulting to 1 µm/px."""
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    image, labels = _min_size_inputs()
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))
    round_spec = ThresholdingRound(
        name="r",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=0.0,
        min_particle_size=5.0,
        min_particle_size_unit="um2",
    )
    mask, gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, None)
    assert mask is None
    assert gdf is None
    assert "pixel size" in err


def test_apply_threshold_frame_min_size_um2_resolves_with_pixel_size():
    """With a pixel size, a µm² min particle size converts to a pixel threshold
    and filters accordingly (0.12 µm/px → 5 µm² ≈ 347 px keeps neither blob)."""
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    image, labels = _min_size_inputs()
    grouping = _trivial_grouping(np.array([1], dtype=np.int32))
    round_spec = ThresholdingRound(
        name="r",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        gaussian_sigma=0.0,
        min_particle_size=1.0,  # 1 µm² / (0.12²) ≈ 69 px → drops the 9 px, keeps the 100 px
        min_particle_size_unit="um2",
    )
    mask, _g, err = _apply_threshold_frame(image, labels, grouping, round_spec, 0.12)
    assert err == ""
    assert int(mask.sum()) == 100


def test_apply_adaptive_clip_empty_labels_yields_zero_mask(tmp_path):
    """An all-zero label frame yields an all-zero mask, no error (apply level)."""
    from percell4.workflows.phases import _apply_threshold_frame, _trivial_grouping

    store = _make_adaptive_store(tmp_path / "empty.h5")
    image = store.read_channel("intensity", 0)
    labels = np.zeros((100, 100), dtype=np.int32)
    round_spec = _adaptive_apply_round()
    grouping = _trivial_grouping(np.array([], dtype=np.int32))
    mask, _gdf, err = _apply_threshold_frame(image, labels, grouping, round_spec, 0.12)
    assert err == ""
    assert mask.sum() == 0


# ── apply_threshold_headless ────────────────────────────────────────────


def test_apply_threshold_headless_writes_mask_and_groups(
    fixture_store_with_labels,
):
    # First compute the grouping; then apply it headlessly.
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,  # no smoothing — makes the test deterministic
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    assert grouping is not None

    failure, msg = apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    assert failure is None, msg

    # Verify the mask and groups DF were written.
    assert "GFP_split" in fixture_store_with_labels.list_masks()
    groups_df = fixture_store_with_labels.read_dataframe("/groups/GFP_split")
    assert "label" in groups_df.columns
    assert any(c.startswith("group_GFP_") for c in groups_df.columns)

    # The combined mask should have some positive pixels.
    combined = fixture_store_with_labels.read_mask("GFP_split")
    assert combined.sum() > 0


def test_apply_threshold_headless_puncta_mode_writes_binary_mask(
    fixture_store_with_labels,
):
    # A puncta round routes through detect_two_pass and must write a {0,1}
    # uint8 mask plus a 2-column /groups table (the downstream contract).
    round_spec = ThresholdingRound(
        name="SG_puncta",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=1.0,
        puncta=PunctaDetectorSettings(
            detector_name="log",
            seed_detector_name="bg-k-sigma",
            background_estimator_name="gaussian-peak",
            detector_params={"threshold_rel": 0.05},
            min_spot_px=2,
        ),
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    assert grouping is not None

    failure, msg = apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    assert failure is None, msg

    combined = fixture_store_with_labels.read_mask("SG_puncta")
    # {0,1}-only invariant (read back from /masks, not assumed of the store).
    assert combined.dtype == np.uint8
    assert set(np.unique(combined).tolist()) <= {0, 1}

    # /groups keeps exactly ["label", "group_<channel>_<metric>"] so the
    # _merge_group_dfs 2-column guard does not silently drop the group column.
    groups_df = fixture_store_with_labels.read_dataframe("/groups/SG_puncta")
    assert list(groups_df.columns) == ["label", "group_GFP_mean_intensity"]


def _iterative_round(scope: str, name: str) -> ThresholdingRound:
    # Channel RFP has intra-cell structure (a bright center over a dim base), so
    # there is something to peel within each cell / group. A min-positive stop
    # criterion keeps the integration deterministic across all three scopes — the
    # aggressive default positive-fraction-high (max_frac=0.5) would prematurely
    # stop a whole-field unit on this small synthetic fixture (its stopping
    # behaviour is exercised directly in tests/test_measure/test_iterative_otsu.py).
    return ThresholdingRound(
        name=name,
        channel="RFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=1.0,
        iterative_otsu=IterativeOtsuSettings(scope=scope, stop_criteria=("min-positive",)),
    )


@pytest.mark.parametrize("scope", ["per-cell", "whole-field", "groups"])
def test_apply_threshold_headless_iterative_otsu_writes_binary_mask(
    fixture_store_with_labels, scope
):
    round_spec = _iterative_round(scope, f"SG_iter_{scope.replace('-', '_')}")
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    assert grouping is not None

    failure, msg = apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    assert failure is None, msg

    combined = fixture_store_with_labels.read_mask(round_spec.name)
    assert combined.dtype == np.uint8
    assert set(np.unique(combined).tolist()) <= {0, 1}
    assert combined.sum() > 0  # the bright RFP centers are captured

    # /groups keeps the 2-column contract so _merge_group_dfs doesn't drop it.
    groups_df = fixture_store_with_labels.read_dataframe(f"/groups/{round_spec.name}")
    assert list(groups_df.columns) == ["label", "group_RFP_mean_intensity"]


def test_iterative_otsu_group_table_honest_per_scope(fixture_store_with_labels):
    col = "group_RFP_mean_intensity"

    # whole-field / per-cell: grouping did not drive the mask -> degenerate table.
    wf = _iterative_round("whole-field", "SG_wf")
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, wf)
    apply_threshold_headless(fixture_store_with_labels, wf, grouping)
    wf_groups = fixture_store_with_labels.read_dataframe("/groups/SG_wf")
    assert set(wf_groups[col].unique().tolist()) == {1}

    # groups scope: the real GMM/k-means assignments survive (>1 group on 12 cells).
    gr = _iterative_round("groups", "SG_gr")
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, gr)
    apply_threshold_headless(fixture_store_with_labels, gr, grouping)
    gr_groups = fixture_store_with_labels.read_dataframe("/groups/SG_gr")
    assert gr_groups[col].nunique() >= 2


def test_iterative_otsu_mask_consumable_by_analyze_particles(fixture_store_with_labels):
    from percell4.domain.measure.particle import analyze_particles

    labels = fixture_store_with_labels.read_labels("cellpose_qc")
    rfp = fixture_store_with_labels.read_channel("intensity", 1)
    round_spec = _iterative_round("per-cell", "SG_particles")
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    mask = fixture_store_with_labels.read_mask("SG_particles")
    df = analyze_particles({"RFP": rfp}, labels, mask, min_area=1)
    assert len(df) > 0
    assert df["particle_count"].sum() > 0


def test_apply_threshold_headless_handles_unknown_channel(
    fixture_store_with_labels,
):
    round_spec = ThresholdingRound(
        name="bogus",
        channel="NotAChannel",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    # Fake a grouping result (we never get this far in practice if
    # threshold_compute_one already failed).
    grouping = GroupingResult(
        group_assignments=pd.Series([1, 1, 2, 2, 2, 2], index=[1, 2, 3, 4, 5, 6], name="group"),
        n_groups=2,
        group_means=[1.0, 2.0],
    )
    failure, msg = apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    assert failure is DatasetFailure.THRESHOLD_ERROR


# ── measure_one ─────────────────────────────────────────────────────────


def test_measure_one_adds_area_um2_sibling_when_pixel_size_metadata_present(
    tmp_path,
):
    """When pixel_size_um is in /metadata, measure_one emits an
    `area_um2` sibling column alongside every area column."""
    store = _make_fixture_h5(tmp_path / "DSpx.h5")
    # Patch in the pixel size that import_dataset would normally write.
    store.set_metadata({"pixel_size_um": 0.5})
    _write_synthetic_labels(store)

    df, failure, _ = measure_one(store, round_specs=[])
    assert failure is None

    # area column has an area_um2 sibling
    assert "area" in df.columns
    assert "area_um2" in df.columns
    # 0.5 µm/px ⇒ pixel_size² = 0.25 ⇒ area_um2 = area * 0.25
    assert (df["area_um2"] == df["area"] * 0.25).all()


def test_measure_one_no_area_um2_when_pixel_size_missing(
    fixture_store_with_labels,
):
    """When pixel_size_um is absent, no _um2 sibling columns are added."""
    df, failure, _ = measure_one(fixture_store_with_labels, round_specs=[])
    assert failure is None
    # Fixture h5 has no pixel_size_um in metadata
    assert "area" in df.columns
    assert "area_um2" not in df.columns
    assert not any(c.endswith("_um2") for c in df.columns)


def test_measure_particles_one_includes_area_um2_when_pixel_size_present(
    tmp_path,
):
    """Per-particle detail rows get an `area_um2` sibling when pixel_size_um
    is known."""
    from percell4.workflows.models import ParticleSettings

    store = _make_fixture_h5(tmp_path / "DSpx_part.h5")
    store.set_metadata({"pixel_size_um": 0.25})
    _write_synthetic_labels(store)

    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(store, round_spec)
    apply_threshold_headless(store, round_spec, grouping)

    particles, failure, _ = measure_particles_one(
        store,
        round_specs=[round_spec],
        particle_settings=ParticleSettings(min_area=0),
    )
    assert failure is None
    if not particles.empty:
        assert "area" in particles.columns
        assert "area_um2" in particles.columns
        # 0.25 µm/px ⇒ pixel² = 0.0625
        assert (particles["area_um2"] == particles["area"] * 0.0625).all()


def test_measure_one_with_no_masks(fixture_store_with_labels):
    """Measuring without any round masks produces the base per-channel table."""
    df, failure, msg = measure_one(fixture_store_with_labels, round_specs=[])
    assert failure is None, msg
    assert len(df) == 12  # n_cells
    # Should have per-channel metric columns for GFP and RFP
    assert "GFP_mean_intensity" in df.columns
    assert "RFP_mean_intensity" in df.columns


def test_measure_one_with_round_masks(fixture_store_with_labels):
    """Full measure path: compute → apply → measure with the resulting mask."""
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    df, failure, msg = measure_one(fixture_store_with_labels, round_specs=[round_spec])
    assert failure is None, msg
    assert len(df) == 12

    # Per-round inside columns should exist. The "_out_<round>" columns
    # are intentionally dropped by measure_one in this workflow
    # (iteration-3 user feedback: only inside-mask stats are kept).
    assert "GFP_mean_intensity_in_GFP_split" in df.columns
    assert "RFP_mean_intensity_in_GFP_split" in df.columns
    assert "GFP_mean_intensity_out_GFP_split" not in df.columns
    assert not any("_out_" in c for c in df.columns)

    # The group_<round> column should be merged in
    assert "group_GFP_split" in df.columns
    # Every cell should have a non-null group assignment
    assert df["group_GFP_split"].notna().all()


def test_measure_one_missing_mask_still_succeeds(fixture_store_with_labels):
    """A round without a mask (threshold failed earlier) is skipped silently."""
    round_spec = ThresholdingRound(
        name="nonexistent",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    df, failure, msg = measure_one(fixture_store_with_labels, round_specs=[round_spec])
    assert failure is None
    assert len(df) == 12
    # No _in_nonexistent columns because the mask was missing
    assert "GFP_mean_intensity_in_nonexistent" not in df.columns


# ── measure_one × identity / cohort columns (U4) ────────────────────────


def test_measure_one_populates_cell_id_column(fixture_store_with_labels):
    """U4: every real row carries cell_id = label."""
    df, failure, _ = measure_one(fixture_store_with_labels, round_specs=[])
    assert failure is None
    assert "cell_id" in df.columns
    # cell_id mirrors label for real cells.
    pd.testing.assert_series_equal(df["cell_id"].rename("label"), df["label"], check_dtype=False)


def test_measure_one_populates_is_edge_columns_default_exclude(
    fixture_store_with_labels,
):
    """U4: is_edge / is_edge_synthetic are always present, uniformly False
    in EXCLUDE mode (the default) on a fixture with no edge-touching cells."""
    df, failure, _ = measure_one(fixture_store_with_labels, round_specs=[])
    assert failure is None
    assert "is_edge" in df.columns
    assert "is_edge_synthetic" in df.columns
    assert not df["is_edge"].any()
    assert not df["is_edge_synthetic"].any()


def _write_labels_with_edge_cells(store: DatasetStore) -> np.ndarray:
    """Write a labels array with 4 edge-touching cells + 8 whole cells.

    Built on top of the standard 12-cell fixture (rows at 5, 27, 49, 71;
    cols at 5, 27, 49). The cells in row index 0 (rows 5-10) get extended
    to touch the top edge (rows 0-10). Same on row index 3 (rows 71-76)
    extended to touch the bottom edge (rows 71-99). Middle rows (27, 49)
    stay interior.

    Result: cells 1,2,3 touch top edge; cells 10,11,12 touch bottom edge;
    cells 4-9 are interior. 6 edge cells, 6 interior cells.
    """
    size = 100
    labels = np.zeros((size, size), dtype=np.int32)
    for i in range(12):
        grid_row = i // 3  # 0..3
        grid_col = i % 3  # 0..2
        if grid_row == 0:
            # Stretch to touch the top edge.
            row_lo, row_hi = 0, 11
        elif grid_row == 3:
            # Stretch to touch the bottom edge.
            row_lo, row_hi = 71, 100
        else:
            row_lo = 5 + grid_row * 22
            row_hi = row_lo + 6
        col_lo = 5 + grid_col * 22
        col_hi = col_lo + 6
        labels[row_lo:row_hi, col_lo:col_hi] = i + 1
    store.write_labels("cellpose_qc", labels)
    return labels


@pytest.fixture
def fixture_store_with_edge_cells(tmp_path: Path) -> DatasetStore:
    """Fixture: 100×100 image with 6 edge-touching + 6 interior labels."""
    store = _make_fixture_h5(tmp_path / "edge_DS.h5")
    _write_labels_with_edge_cells(store)
    return store


def test_measure_one_flags_edge_cells_in_include_normal_mode(
    fixture_store_with_edge_cells,
):
    """U4: INCLUDE_AS_NORMAL keeps edge cells with is_edge=True."""
    from percell4.workflows.models import EdgeMode

    df, failure, _ = measure_one(
        fixture_store_with_edge_cells,
        round_specs=[],
        edge_mode=EdgeMode.INCLUDE_AS_NORMAL,
    )
    assert failure is None
    # 6 edge + 6 whole = 12 real cells, no synthetic row
    assert len(df) == 12
    assert df["is_edge"].sum() == 6
    assert (~df["is_edge"]).sum() == 6
    # No synthetic row in this mode
    assert not df["is_edge_synthetic"].any()


def test_measure_one_appends_synthetic_row_in_size_normalized_mode(
    fixture_store_with_edge_cells,
):
    """U4 / AE1: INCLUDE_AS_SIZE_NORMALIZED_COHORT appends one synthetic row."""
    from percell4.workflows.models import EdgeMode

    df, failure, _ = measure_one(
        fixture_store_with_edge_cells,
        round_specs=[],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    assert failure is None
    # 12 real + 1 synthetic = 13 rows
    assert len(df) == 13

    synthetic = df[df["is_edge_synthetic"]]
    assert len(synthetic) == 1
    s = synthetic.iloc[0]
    assert s["cell_id"] == -1
    assert s["label"] == -1
    assert bool(s["is_edge"]) is False
    # 6 real edge cells + 6 real whole cells (no synthetic counted)
    assert df["is_edge"].sum() == 6
    assert (~df["is_edge"] & ~df["is_edge_synthetic"]).sum() == 6


def test_measure_one_synthetic_row_area_equals_whole_mean_area(
    fixture_store_with_edge_cells,
):
    """U4: synthetic row's area = sum(edge_area) / N_theoretical
    = sum(edge_area) / (sum(edge_area) / mean(whole_area))
    = mean(whole_area).
    """
    from percell4.workflows.models import EdgeMode

    df, _, _ = measure_one(
        fixture_store_with_edge_cells,
        round_specs=[],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    real = df[~df["is_edge_synthetic"]]
    synthetic = df[df["is_edge_synthetic"]].iloc[0]

    whole_mean_area = real.loc[~real["is_edge"], "area"].mean()
    assert synthetic["area"] == pytest.approx(whole_mean_area, rel=1e-6)


def test_measure_one_no_edge_cells_emits_no_synthetic_row(
    fixture_store_with_labels,
):
    """U4 / R10a: zero edge cells → no synthetic row, no failure."""
    from percell4.workflows.models import EdgeMode

    df, failure, _ = measure_one(
        fixture_store_with_labels,
        round_specs=[],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    assert failure is None
    # 12 real cells, all interior, no synthetic row
    assert len(df) == 12
    assert not df["is_edge"].any()
    assert not df["is_edge_synthetic"].any()


def test_measure_one_zero_whole_cells_records_failure_preserves_df(tmp_path):
    """U4 / AE2 / R10b: all cells touch the border → DatasetFailure recorded,
    no synthetic row, but per-cell rows still returned for staging."""
    from percell4.workflows.models import EdgeMode

    # Build a fixture where every cell touches the border.
    store = _make_fixture_h5(tmp_path / "all_edge.h5", n_cells=4, size=50)
    labels = np.zeros((50, 50), dtype=np.int32)
    labels[0:8, 0:8] = 1  # top-left corner
    labels[0:8, 42:50] = 2  # top-right corner
    labels[42:50, 0:8] = 3  # bottom-left corner
    labels[42:50, 42:50] = 4  # bottom-right corner
    store.write_labels("cellpose_qc", labels)

    df, failure, msg = measure_one(
        store,
        round_specs=[],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    # Soft failure: DatasetFailure recorded for the synthetic-row math,
    # but per-cell rows preserved so the runner can stage them.
    assert failure is DatasetFailure.MEASUREMENT_ERROR
    assert "no whole cells" in msg.lower()
    assert len(df) == 4  # all 4 edge cells preserved
    assert df["is_edge"].all()
    assert not df["is_edge_synthetic"].any()


def test_measure_one_synthetic_row_has_nan_group_columns(
    fixture_store_with_edge_cells,
):
    """U4: synthetic row's group_<round> columns are NaN after the left-merge.

    The synthetic row has label=-1 which is absent from /groups/<round>;
    the existing df.merge(on='label', how='left') leaves the group
    column NaN naturally — no special-casing in the merge code.
    """
    from percell4.workflows.models import EdgeMode

    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_edge_cells, round_spec)
    apply_threshold_headless(fixture_store_with_edge_cells, round_spec, grouping)

    df, failure, _ = measure_one(
        fixture_store_with_edge_cells,
        round_specs=[round_spec],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    assert failure is None
    synthetic = df[df["is_edge_synthetic"]]
    assert len(synthetic) == 1
    assert pd.isna(synthetic.iloc[0]["group_GFP_split"])


def test_measure_one_default_edge_mode_exclude_emits_no_synthetic_row(
    fixture_store_with_edge_cells,
):
    """U4: calling measure_one without edge_mode uses EXCLUDE default —
    edge cells appear with is_edge=True (Phase 1 didn't filter here since
    we wrote labels directly) but no synthetic row is emitted."""
    df, failure, _ = measure_one(fixture_store_with_edge_cells, round_specs=[])
    assert failure is None
    # Edge cells are tagged (measure-time recompute), but no synthetic row
    # because edge_mode defaults to EXCLUDE.
    assert df["is_edge"].any()
    assert not df["is_edge_synthetic"].any()


def test_measure_one_zero_edge_cells_in_size_normalized_mode_no_failure(
    fixture_store_with_labels,
):
    """U4 / R10a: in size-normalized mode, zero edge cells → no failure, no synthetic."""
    from percell4.workflows.models import EdgeMode

    df, failure, _ = measure_one(
        fixture_store_with_labels,
        round_specs=[],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    assert failure is None
    assert not df["is_edge_synthetic"].any()


# ── export_run ──────────────────────────────────────────────────────────


def _sample_run_metadata(run_folder: Path) -> RunMetadata:
    from datetime import UTC, datetime

    return RunMetadata(
        run_id="test",
        run_folder=run_folder,
        started_at=datetime.now(UTC),
        intersected_channels=["GFP", "RFP"],
    )


def _sample_workflow_config(
    selected_cols: list[str],
) -> WorkflowConfig:
    return WorkflowConfig(
        datasets=[
            WorkflowDatasetEntry(
                name="DS1",
                source=DatasetSource.H5_EXISTING,
                h5_path=Path("/tmp/DS1.h5"),
                channel_names=["GFP", "RFP"],
            ),
        ],
        cellpose=CellposeSettings(),
        thresholding_rounds=[
            ThresholdingRound(
                name="R",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
            ),
        ],
        selected_csv_columns=selected_cols,
        output_parent=Path("/tmp/runs"),
    )


def test_export_run_writes_parquet_and_csvs(tmp_path, fixture_store_with_labels):
    run_folder = tmp_path / "run_01"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    # Build one staging parquet from a real measure_one call
    round_spec = ThresholdingRound(
        name="R",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    df, failure, _ = measure_one(fixture_store_with_labels, round_specs=[round_spec])
    assert failure is None

    write_staging_parquet(run_folder, "DS1", df)
    assert (run_folder / "staging" / "DS1.parquet").exists()

    cfg = _sample_workflow_config(selected_cols=["GFP_mean_intensity", "group_R"])
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is None, msg

    # Final artifacts
    parquet_path = run_folder / "measurements.parquet"
    combined_csv = run_folder / "combined.csv"
    per_ds_csv = run_folder / "per_dataset" / "DS1.csv"

    assert parquet_path.exists()
    assert combined_csv.exists()
    assert per_ds_csv.exists()

    # Parquet round-trips with the expected columns
    loaded = pd.read_parquet(parquet_path)
    assert "dataset" in loaded.columns
    assert "GFP_mean_intensity" in loaded.columns
    assert len(loaded) == 12

    # combined.csv has identity + selected columns only
    combined = pd.read_csv(combined_csv)
    assert "dataset" in combined.columns
    assert "label" in combined.columns
    assert "GFP_mean_intensity" in combined.columns
    # Unselected column should NOT be in the CSV
    assert "RFP_mean_intensity" not in combined.columns

    # per-dataset CSV has no dataset column
    per_ds = pd.read_csv(per_ds_csv)
    assert "dataset" not in per_ds.columns
    assert "label" in per_ds.columns

    # staging/ was cleaned up
    assert not (run_folder / "staging").exists()


def test_export_run_ignores_appledouble_staging_sidecars(
    tmp_path, fixture_store_with_labels
):
    """A run folder on an exFAT drive gets a ``._<name>.parquet`` xattr blob
    beside every staging file. It matches ``*.parquet`` but is not one, so an
    unfiltered scan killed the whole export with "Parquet magic bytes not
    found in footer" after every dataset had already been measured."""
    run_folder = tmp_path / "run_exfat"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    round_spec = ThresholdingRound(
        name="R",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    df, failure, _ = measure_one(fixture_store_with_labels, round_specs=[round_spec])
    assert failure is None

    write_staging_parquet(run_folder, "DS1", df)
    (run_folder / "staging" / "._DS1.parquet").write_bytes(b"\x00\x05\x16\x07" * 16)

    cfg = _sample_workflow_config(selected_cols=["GFP_mean_intensity"])
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is None, msg
    assert (run_folder / "measurements.parquet").exists()
    assert len(pd.read_parquet(run_folder / "measurements.parquet")) == 12


def test_export_run_keeps_timepoint_for_multitimepoint(tmp_path, fixture_store_with_labels):
    """combined.csv + per_dataset CSVs must carry `timepoint` for time-lapse data — it's
    in the staged per-cell df (like complete_tracks.csv), but the export column selection
    dropped it because it is neither an identity nor a user-selected column. particles.csv
    already carries it (no column selection); this also locks that behavior."""
    run_folder = tmp_path / "run_tl"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    # A real measure_one df (so the summary-CSV builders find their identity/edge columns)
    # with an injected `timepoint` column — exactly what the time-lapse measure path emits.
    round_spec = ThresholdingRound(
        name="R", channel="GFP", metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS, kmeans_n_clusters=2, gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    df, failure, _ = measure_one(fixture_store_with_labels, round_specs=[round_spec])
    assert failure is None
    df = df.reset_index(drop=True)
    df["timepoint"] = (df.index >= len(df) // 2).astype(int)  # first half t=0, rest t=1
    write_staging_parquet(run_folder, "DS1", df)

    # Particle staging carrying `timepoint` (the time-lapse particle path tags it).
    pdf = pd.DataFrame(
        {
            "round_name": ["R", "R"],
            "cell_id": [1, 2],
            "particle_id": [1, 1],
            "area": [5, 6],
            "timepoint": [0, 1],
            "GFP_mean_intensity": [9.0, 8.0],
        }
    )
    write_staging_particles_parquet(run_folder, "DS1", pdf)

    cfg = _sample_workflow_config(selected_cols=["GFP_mean_intensity"])
    meta = _sample_run_metadata(run_folder)
    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is None, msg

    combined = pd.read_csv(run_folder / "combined.csv")
    assert "timepoint" in combined.columns
    assert sorted(combined["timepoint"].unique()) == [0, 1]
    # timepoint is kept as identity even though it is NOT a user-selected metric
    assert "RFP_mean_intensity" not in combined.columns

    per_ds = pd.read_csv(run_folder / "per_dataset" / "DS1.csv")
    assert "timepoint" in per_ds.columns

    particles = pd.read_csv(run_folder / "particles.csv")
    assert "timepoint" in particles.columns
    assert sorted(particles["timepoint"].unique()) == [0, 1]


def test_export_run_no_timepoint_for_single_timepoint(tmp_path):
    """Single-timepoint exports have no `timepoint` column — the added identity is
    self-gating (kept only when the column is present in the staged df)."""
    run_folder = tmp_path / "run_st"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)
    df = pd.DataFrame(
        {"cell_id": [1, 2], "label": [1, 2], "GFP_mean_intensity": [10.0, 20.0]}
    )
    write_staging_parquet(run_folder, "DS1", df)

    cfg = _sample_workflow_config(selected_cols=["GFP_mean_intensity"])
    meta = _sample_run_metadata(run_folder)
    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is None, msg

    combined = pd.read_csv(run_folder / "combined.csv")
    assert "timepoint" not in combined.columns
    per_ds = pd.read_csv(run_folder / "per_dataset" / "DS1.csv")
    assert "timepoint" not in per_ds.columns


def test_export_run_fails_if_staging_missing(tmp_path):
    run_folder = tmp_path / "run_02"
    run_folder.mkdir()
    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is DatasetFailure.MEASUREMENT_ERROR
    assert "staging" in msg


def test_export_run_fails_if_no_staging_parquets(tmp_path):
    run_folder = tmp_path / "run_03"
    (run_folder / "staging").mkdir(parents=True)
    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)

    failure, msg = export_run(run_folder, cfg, meta)
    assert failure is DatasetFailure.MEASUREMENT_ERROR


# ── export_run summary CSVs (U6) ────────────────────────────────────────


def test_export_run_writes_summary_groups_csv(tmp_path, fixture_store_with_labels):
    """U6: summary_groups.csv has one row per (dataset, round_name, group_label)."""
    run_folder = tmp_path / "run_summary"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    round_spec = ThresholdingRound(
        name="R",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)
    df, _, _ = measure_one(fixture_store_with_labels, round_specs=[round_spec])
    write_staging_parquet(run_folder, "DS1", df)

    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)
    failure, _ = export_run(run_folder, cfg, meta)
    assert failure is None

    summary_path = run_folder / "summary_groups.csv"
    assert summary_path.exists()
    summary = pd.read_csv(summary_path)

    # Columns we promised in origin R18.
    for col in ("dataset", "round_name", "group_label", "n_cells", "fraction_of_dataset_cells"):
        assert col in summary.columns

    # Per-metric stats for the GFP_mean_intensity column at minimum.
    assert any(c.endswith("_mean") for c in summary.columns)
    assert any(c.endswith("_median") for c in summary.columns)
    assert any(c.endswith("_std") for c in summary.columns)

    # fraction_of_dataset_cells sums to ~1.0 within each (dataset, round).
    grouped = summary.groupby(["dataset", "round_name"], observed=True)[
        "fraction_of_dataset_cells"
    ].sum()
    for val in grouped:
        assert val == pytest.approx(1.0, abs=1e-6)

    # n_cells across groups in the round equals total real cells.
    total_in_round = summary[summary["round_name"] == "R"]["n_cells"].sum()
    assert total_in_round == 12


def test_export_run_writes_summary_datasets_csv(tmp_path, fixture_store_with_labels):
    """U6: summary_datasets.csv has one row per dataset with origin R19 columns."""
    run_folder = tmp_path / "run_summary_ds"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    df, _, _ = measure_one(fixture_store_with_labels, round_specs=[])
    write_staging_parquet(run_folder, "DS1", df)

    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)
    failure, _ = export_run(run_folder, cfg, meta)
    assert failure is None

    summary_path = run_folder / "summary_datasets.csv"
    assert summary_path.exists()
    summary = pd.read_csv(summary_path)

    # Origin R19 columns
    for col in (
        "dataset",
        "source",
        "n_cells_total",
        "n_cells_whole",
        "n_cells_edge",
        "n_rounds_thresholding",
        "n_rounds_dilute",
        "dilute_enabled",
        "edge_mode",
        "failure_reason",
    ):
        assert col in summary.columns

    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["dataset"] == "DS1"
    assert row["source"] in ("h5_existing", "compressed_from_tiff")
    assert row["n_cells_total"] == 12
    assert row["n_cells_whole"] == 12  # No edge cells in this fixture
    assert row["n_cells_edge"] == 0
    assert int(row["n_rounds_thresholding"]) == len(cfg.thresholding_rounds)
    # Dilute is disabled in _sample_workflow_config — n_rounds_dilute NaN
    assert pd.isna(row["n_rounds_dilute"])
    assert bool(row["dilute_enabled"]) is False
    assert row["edge_mode"] == "exclude"  # default


def test_summary_datasets_csv_records_failure_reason(tmp_path, fixture_store_with_labels):
    """U6: failure_reason column populated when a dataset has metadata failures."""
    run_folder = tmp_path / "run_summary_fail"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    df, _, _ = measure_one(fixture_store_with_labels, round_specs=[])
    write_staging_parquet(run_folder, "DS1", df)

    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)
    # Simulate a recorded failure on DS1.
    record_failure(
        meta,
        dataset_name="DS1",
        phase_name="threshold",
        failure=DatasetFailure.THRESHOLD_ERROR,
        message="all cells in one group",
    )

    failure, _ = export_run(run_folder, cfg, meta)
    assert failure is None

    summary = pd.read_csv(run_folder / "summary_datasets.csv")
    row = summary[summary["dataset"] == "DS1"].iloc[0]
    assert "threshold: all cells in one group" in str(row["failure_reason"])


def test_summary_groups_excludes_synthetic_rows(tmp_path, fixture_store_with_edge_cells):
    """U6: synthetic rows (is_edge_synthetic=True) excluded from group stats."""
    from percell4.workflows.models import EdgeMode

    run_folder = tmp_path / "run_summary_synth"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    round_spec = ThresholdingRound(
        name="R",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_edge_cells, round_spec)
    apply_threshold_headless(fixture_store_with_edge_cells, round_spec, grouping)
    df, _, _ = measure_one(
        fixture_store_with_edge_cells,
        round_specs=[round_spec],
        edge_mode=EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    )
    # The df includes 12 real + 1 synthetic = 13 rows
    assert df["is_edge_synthetic"].sum() == 1

    write_staging_parquet(run_folder, "DS1", df)
    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)
    failure, _ = export_run(run_folder, cfg, meta)
    assert failure is None

    summary = pd.read_csv(run_folder / "summary_groups.csv")
    # The synthetic row has NaN group, so it's already filtered, but
    # additionally we explicitly exclude is_edge_synthetic from the
    # n_cells count.
    total = summary[summary["round_name"] == "R"]["n_cells"].sum()
    assert total == 12  # not 13 — synthetic excluded


def test_measure_one_merges_particle_columns_when_particle_settings_set(
    fixture_store_with_labels,
):
    """U7: per-cell particle summary columns are prefixed with the round name
    and merged into the per-cell df."""
    from percell4.workflows.models import ParticleSettings

    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    df, failure, _ = measure_one(
        fixture_store_with_labels,
        round_specs=[round_spec],
        particle_settings=ParticleSettings(min_area=0),
    )
    assert failure is None
    # Per-cell particle columns prefixed with the round name.
    assert "GFP_split_particle_count" in df.columns
    assert "GFP_split_total_particle_area" in df.columns
    # Per-channel intensity aggregates from the particle helper.
    assert any(c.startswith("GFP_split_GFP_") for c in df.columns)


def test_measure_one_no_particle_columns_when_particle_settings_none(
    fixture_store_with_labels,
):
    """U7: with particle_settings=None, no particle columns are added."""
    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    df, failure, _ = measure_one(
        fixture_store_with_labels,
        round_specs=[round_spec],
        particle_settings=None,
    )
    assert failure is None
    assert "GFP_split_particle_count" not in df.columns


def test_measure_particles_one_returns_per_particle_rows(
    fixture_store_with_labels,
):
    """U7: measure_particles_one returns a combined per-particle df with
    a round_name column."""
    from percell4.workflows.models import ParticleSettings

    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    particles, failure, msg = measure_particles_one(
        fixture_store_with_labels,
        round_specs=[round_spec],
        particle_settings=ParticleSettings(min_area=0),
    )
    assert failure is None, msg
    if not particles.empty:
        assert "round_name" in particles.columns
        assert "cell_id" in particles.columns
        assert "particle_id" in particles.columns
        assert (particles["round_name"] == "GFP_split").all()


def test_measure_particles_one_no_rounds_returns_empty_df(
    fixture_store_with_labels,
):
    """U7: when no round masks are present (round_specs=[]), the helper
    returns an empty df with no failure."""
    from percell4.workflows.models import ParticleSettings

    particles, failure, _ = measure_particles_one(
        fixture_store_with_labels,
        round_specs=[],
        particle_settings=ParticleSettings(min_area=0),
    )
    assert failure is None
    assert particles.empty


def test_export_run_writes_particles_parquet_and_csv(tmp_path, fixture_store_with_labels):
    """U7: when staging_particles/ has parquet files, export_run produces
    particles.parquet and particles.csv in the run folder."""
    from percell4.workflows.models import ParticleSettings

    run_folder = tmp_path / "run_particles"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    round_spec = ThresholdingRound(
        name="GFP_split",
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
        gaussian_sigma=0.0,
    )
    grouping, _, _ = threshold_compute_one(fixture_store_with_labels, round_spec)
    apply_threshold_headless(fixture_store_with_labels, round_spec, grouping)

    df, _, _ = measure_one(
        fixture_store_with_labels,
        round_specs=[round_spec],
        particle_settings=ParticleSettings(min_area=0),
    )
    write_staging_parquet(run_folder, "DS1", df)

    particles_df, _, _ = measure_particles_one(
        fixture_store_with_labels,
        round_specs=[round_spec],
        particle_settings=ParticleSettings(min_area=0),
    )
    if not particles_df.empty:
        write_staging_particles_parquet(run_folder, "DS1", particles_df)

    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)
    failure, _ = export_run(run_folder, cfg, meta)
    assert failure is None

    # Per-cell measurements still produced
    assert (run_folder / "measurements.parquet").is_file()
    # When particles were detected, particles.parquet + csv produced
    if not particles_df.empty:
        assert (run_folder / "particles.parquet").is_file()
        assert (run_folder / "particles.csv").is_file()
        # Staging cleaned up on success
        assert not (run_folder / "staging_particles").exists()


def test_summary_csvs_written_atomically_no_tmp_residue(tmp_path, fixture_store_with_labels):
    """U6: write_atomic is used for both summary CSVs — no .tmp leftovers."""
    run_folder = tmp_path / "run_atomic"
    (run_folder / "per_dataset").mkdir(parents=True)
    (run_folder / "staging").mkdir(parents=True)

    df, _, _ = measure_one(fixture_store_with_labels, round_specs=[])
    write_staging_parquet(run_folder, "DS1", df)

    cfg = _sample_workflow_config(selected_cols=[])
    meta = _sample_run_metadata(run_folder)
    export_run(run_folder, cfg, meta)

    assert not (run_folder / "summary_groups.csv.tmp").exists()
    assert not (run_folder / "summary_datasets.csv.tmp").exists()


# ── Failure tracking helpers ────────────────────────────────────────────


def test_record_failure_appends_to_metadata(tmp_path):
    meta = _sample_run_metadata(tmp_path)
    assert meta.failures == []

    record_failure(
        meta,
        dataset_name="DS_bad",
        phase_name="segment",
        failure=DatasetFailure.SEGMENTATION_EMPTY,
        message="no cells",
    )
    assert len(meta.failures) == 1
    assert meta.failures[0].dataset_name == "DS_bad"
    assert meta.failures[0].failure is DatasetFailure.SEGMENTATION_EMPTY


def test_datasets_without_failures_excludes_failed(tmp_path):
    meta = _sample_run_metadata(tmp_path)
    entries = [
        WorkflowDatasetEntry(
            name=f"DS{i}",
            source=DatasetSource.H5_EXISTING,
            h5_path=tmp_path / f"DS{i}.h5",
        )
        for i in range(3)
    ]

    # No failures yet — all datasets pass through.
    assert len(datasets_without_failures(entries, meta)) == 3

    record_failure(
        meta,
        dataset_name="DS1",
        phase_name="segment",
        failure=DatasetFailure.SEGMENTATION_ERROR,
        message="synthetic",
    )

    remaining = datasets_without_failures(entries, meta)
    assert [e.name for e in remaining] == ["DS0", "DS2"]
