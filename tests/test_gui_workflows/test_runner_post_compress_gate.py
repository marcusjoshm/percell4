"""Runner wiring for the post-compress validation gate (U3).

``compress_one`` succeeding is not the same as the dataset being usable:
``import_dataset`` writes an ``.h5`` with no ``/intensity`` and empty
``channel_names`` when nothing matches the channel token pattern, and reports
success. These tests cover the runner-side consequences of gating on that —
the dataset is failed with a named message, it is dropped from later phases,
and the rest of the batch still runs.

Unit coverage for the predicate itself lives in
``tests/test_workflows/test_validate_compressed_dataset.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from percell4.store import DatasetStore
from percell4.workflows.artifacts import create_run_folder
from percell4.workflows.models import (
    AdaptiveClipSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)

from .test_runner_tiff_start import (
    _config,
    _h5_entry,
    _runner,
    _tiff_entry,
)


def _break_source(entry, tmp_path: Path) -> None:
    """Point the plan at files that match no channel token."""
    entry.compress_plan["files"] = [str(tmp_path / "does_not_exist.tif")]
    entry.compress_plan["source_dir"] = str(tmp_path / "missing_dir")


def _drain_running_compress(runner) -> dict[str, list[str]]:
    """Drain the generator, executing only the compress handlers."""
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
        if req.phase_name == "compress":
            req.handler()
    return by_ds


# ── The gate fires ──────────────────────────────────────────────────────


def test_unmatched_source_is_failed_at_compress(qtbot, tmp_path):
    """The silent-empty-.h5 state becomes a named per-dataset failure."""
    entry = _tiff_entry(tmp_path, "DS1")
    _break_source(entry, tmp_path)
    runner, meta = _runner(_config([entry], tmp_path))

    result = next(runner._phase_generator()).handler()

    assert result.success is False
    assert [r.dataset_name for r in meta.failures] == ["DS1"]
    assert meta.failures[0].phase_name == "compress"
    assert "no channels" in meta.failures[0].message


def test_failed_dataset_is_dropped_from_later_phases(qtbot, tmp_path):
    """A gated dataset must not reach segment, threshold, or measure."""
    bad = _tiff_entry(tmp_path, "DS1")
    _break_source(bad, tmp_path)
    good = _h5_entry(tmp_path / "DS2.h5", "DS2")
    runner, _meta = _runner(_config([bad, good], tmp_path))

    by_ds = _drain_running_compress(runner)

    assert by_ds["DS1"] == ["compress"], (
        f"failed dataset continued into later phases: {by_ds['DS1']}"
    )
    assert "segment" in by_ds["DS2"], "the healthy dataset must still run"


def test_gate_does_not_swap_the_entry_on_failure(qtbot, tmp_path):
    """A gated dataset keeps its TIFF_PENDING entry — nothing downstream
    should treat the broken .h5 as a usable dataset."""
    from percell4.workflows.models import DatasetSource

    entry = _tiff_entry(tmp_path, "DS1")
    _break_source(entry, tmp_path)
    runner, _meta = _runner(_config([entry], tmp_path))

    next(runner._phase_generator()).handler()

    assert runner._working_entries[0].source == DatasetSource.TIFF_PENDING


def test_missing_segmentation_channel_is_failed_at_compress(qtbot, tmp_path):
    """A dataset that compresses fine but lacks the configured seg channel."""
    entry = _tiff_entry(tmp_path, "DS1")
    cfg = _config([entry], tmp_path, seg_channel_name="NOT_A_CHANNEL")
    runner, meta = _runner(cfg)

    result = next(runner._phase_generator()).handler()

    assert result.success is False
    assert "'NOT_A_CHANNEL'" in meta.failures[0].message


def test_missing_round_channel_is_failed_at_compress(qtbot, tmp_path):
    entry = _tiff_entry(tmp_path, "DS1")
    cfg = _config(
        [entry],
        tmp_path,
        thresholding_rounds=[
            ThresholdingRound(
                name="R1",
                channel="ABSENT",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
            )
        ],
    )
    runner, meta = _runner(cfg)

    result = next(runner._phase_generator()).handler()

    assert result.success is False
    assert "round channel 'ABSENT'" in meta.failures[0].message


def test_micron_round_without_pixel_size_is_failed_at_compress(
    qtbot, tmp_path
):
    """µm rounds are pre-flighted for .h5 datasets but not tiff_pending ones.

    The synthetic TIFFs carry no resolution metadata, so no pixel size is
    stored. Previously this surfaced only inside apply_threshold_headless,
    after segmentation had already run.
    """
    entry = _tiff_entry(tmp_path, "DS1")
    cfg = _config(
        [entry],
        tmp_path,
        thresholding_rounds=[
            ThresholdingRound(
                name="R1",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
                adaptive_clip=AdaptiveClipSettings(
                    d_min_um=0.8, d_min_unit="um"
                ),
            )
        ],
    )
    runner, meta = _runner(cfg)

    result = next(runner._phase_generator()).handler()

    assert result.success is False
    assert "µm" in meta.failures[0].message


# ── The gate stays out of the way ───────────────────────────────────────


def test_healthy_dataset_passes_the_gate(qtbot, tmp_path):
    entry = _tiff_entry(tmp_path, "DS1")
    runner, meta = _runner(_config([entry], tmp_path))

    result = next(runner._phase_generator()).handler()

    assert result.success is True, result.message
    assert meta.failures == []


def test_px_unit_round_without_pixel_size_still_passes(qtbot, tmp_path):
    """px-native rounds need no pixel size — the check must be conditional."""
    entry = _tiff_entry(tmp_path, "DS1")
    cfg = _config(
        [entry],
        tmp_path,
        thresholding_rounds=[
            ThresholdingRound(
                name="R1",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
                adaptive_clip=AdaptiveClipSettings(
                    d_min_um=3.0, d_min_unit="px"
                ),
            )
        ],
    )
    runner, meta = _runner(cfg)

    result = next(runner._phase_generator()).handler()

    assert result.success is True, result.message
    assert meta.failures == []


def test_gate_does_not_leak_an_hdf5_handle(qtbot, fake_host, tmp_path, fake_segment_one):
    """A full run must still write masks after the gate has read the file.

    HDF5 locking is non-blocking and exclusive, so a handle left open by the
    validation read would resurface as a BlockingIOError (errno 35) from an
    unrelated write later in the run.
    """
    entry = _tiff_entry(tmp_path, "DS1")
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _config([entry], tmp_path)
    runner, meta = _runner(cfg, run_folder)

    runner.start(cfg, fake_host, meta)

    assert meta.failures == [], [
        (f.dataset_name, f.phase_name, f.message) for f in meta.failures
    ]
    # The threshold round's mask write is the operation that would have hit
    # errno 35 had the gate leaked its read handle.
    assert "GFP_split" in DatasetStore(entry.h5_path).list_masks()


@pytest.mark.parametrize("n_good", [1, 2])
def test_batch_survives_one_gated_dataset(
    qtbot, fake_host, tmp_path, fake_segment_one, n_good
):
    """One bad dataset must never abort the batch."""
    bad = _tiff_entry(tmp_path, "BAD")
    _break_source(bad, tmp_path)
    goods = [_tiff_entry(tmp_path, f"OK{i}") for i in range(n_good)]
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _config([bad, *goods], tmp_path)
    runner, meta = _runner(cfg, run_folder)

    runner.start(cfg, fake_host, meta)

    assert [r.dataset_name for r in meta.failures] == ["BAD"]
    assert (run_folder / "measurements.parquet").is_file()

    import pandas as pd

    df = pd.read_parquet(run_folder / "measurements.parquet")
    assert set(df["dataset"].unique()) == {f"OK{i}" for i in range(n_good)}
