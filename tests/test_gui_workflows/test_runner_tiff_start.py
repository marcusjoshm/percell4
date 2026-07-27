"""Runner-level coverage for the TIFF-start path (Phase 0 into later phases).

Before this file the ``tiff_pending`` source was tested only at the
plan-serialization level — ``_build_compress_plan`` shape, ``compress_one``
kwargs, config round-tripping. Nothing drove Phase 0 into the phases that
consume its output, so a compressed dataset that was structurally broken
(no ``/intensity``, empty ``channel_names``) sailed through Phase 0 and failed
minutes later somewhere unrelated. That is exactly how the dropped
``token_config`` defect stayed invisible.

These tests cover the seam in both directions: the cheap generator drain for
phase ordering, and a real end-to-end run that compresses TIFFs on disk and
carries the result through measure and export.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import tifffile

from percell4.gui.workflows.base_runner import WorkflowEventKind
from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner
from percell4.store import DatasetStore
from percell4.workflows.artifacts import create_run_folder
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)

from .conftest import TIFF_FIXTURE_SIZE as SIZE


def _write_tiffs(src: Path, stem: str) -> None:
    """Two tokenless named channels — the configuration that was broken.

    Single-word suffixes: tokenless discovery splits on the last underscore.
    """
    src.mkdir(parents=True, exist_ok=True)
    gfp = np.zeros((SIZE, SIZE), dtype=np.uint16)
    rfp = np.zeros((SIZE, SIZE), dtype=np.uint16)
    for i in range(4):
        row = 5 + (i // 2) * 25
        col = 5 + (i % 2) * 25
        gfp[row : row + 10, col : col + 10] = 100 + 40 * i
        rfp[row : row + 10, col : col + 10] = 60
    tifffile.imwrite(str(src / f"{stem}_GFP.tif"), gfp)
    tifffile.imwrite(str(src / f"{stem}_RFP.tif"), rfp)


def _tiff_entry(tmp_path: Path, name: str) -> WorkflowDatasetEntry:
    """A TIFF_PENDING entry whose plan mirrors what the config dialog builds."""
    from percell4.domain.io.discovery import discover_tokenless
    from percell4.gui.workflows.single_cell.config_dialog import (
        _build_compress_plan,
    )

    src = tmp_path / f"raw_{name}"
    _write_tiffs(src, name)
    datasets, token_config = discover_tokenless(src)
    ds = datasets[0]
    out_h5 = tmp_path / f"{name}.h5"
    channels = sorted(ds.scan_result.channels)

    plan = _build_compress_plan(
        ds=type(
            "Spec",
            (),
            {
                "name": name,
                "source_dir": src,
                "files": list(ds.files),
                "output_path": out_h5,
            },
        )(),
        gui_state=None,
        cfg=type(
            "Cfg",
            (),
            {
                "z_project_method": "mip",
                "token_config": token_config,
                "tile_config": None,
                "flim_params": None,
                "creation_bin": 1,
            },
        )(),
        selected_token_ids=channels,
        layer_assignments_payload={},
    )
    return WorkflowDatasetEntry(
        name=name,
        source=DatasetSource.TIFF_PENDING,
        h5_path=out_h5,
        channel_names=list(channels),
        compress_plan=plan,
    )


def _h5_entry(path: Path, name: str) -> WorkflowDatasetEntry:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP", "RFP"]})
    store.write_array(
        "intensity",
        np.zeros((2, SIZE, SIZE), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    return WorkflowDatasetEntry(
        name=name,
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["GFP", "RFP"],
    )


def _config(entries, tmp_path, **overrides) -> WorkflowConfig:
    base = dict(
        datasets=entries,
        cellpose=CellposeSettings(diameter=10.0, gpu=False, min_size=5),
        thresholding_rounds=[
            ThresholdingRound(
                name="GFP_split",
                channel="GFP",
                metric="mean_intensity",
                algorithm=ThresholdAlgorithm.KMEANS,
                kmeans_n_clusters=2,
                gaussian_sigma=0.0,
            ),
        ],
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
        seg_channel_name="GFP",
    )
    base.update(overrides)
    return WorkflowConfig(**base)


def _runner(cfg, run_folder: Path | None = None):
    meta = RunMetadata(
        run_id="r",
        run_folder=run_folder or Path("/tmp/r"),
        started_at=datetime.now(UTC),
        intersected_channels=["GFP", "RFP"],
    )
    return (
        SingleCellThresholdingRunner(
            config=cfg, metadata=meta, interactive_qc=False
        ),
        meta,
    )


def _phase_names(runner) -> list[str]:
    return [req.phase_name for req in runner._phase_generator()]


def _phases_by_dataset(runner) -> dict[str, list[str]]:
    by_ds: dict[str, list[str]] = {}
    for req in runner._phase_generator():
        by_ds.setdefault(req.dataset_name, []).append(req.phase_name)
    return by_ds


# ── Phase ordering (generator drain, no handlers executed) ──────────────


def test_tiff_pending_yields_compress_before_segment(qtbot, tmp_path):
    """A pending dataset must be compressed before anything reads its .h5."""
    entry = _tiff_entry(tmp_path, "DS1")
    runner, _ = _runner(_config([entry], tmp_path))

    phases = _phase_names(runner)

    assert "compress" in phases, f"no compress phase emitted: {phases}"
    assert phases.index("compress") < phases.index("segment"), (
        f"compress must precede segment: {phases}"
    )


def test_h5_existing_yields_no_compress_phase(qtbot, tmp_path):
    """An already-compressed dataset must not be re-imported."""
    entry = _h5_entry(tmp_path / "DS2.h5", "DS2")
    runner, _ = _runner(_config([entry], tmp_path))

    assert "compress" not in _phase_names(runner)


def test_mixed_run_compresses_only_the_pending_dataset(qtbot, tmp_path):
    """One pending + one existing: exactly one compress, both segment."""
    pending = _tiff_entry(tmp_path, "DS1")
    existing = _h5_entry(tmp_path / "DS2.h5", "DS2")
    runner, _ = _runner(_config([pending, existing], tmp_path))

    by_ds = _phases_by_dataset(runner)

    assert by_ds["DS1"].count("compress") == 1
    assert "compress" not in by_ds["DS2"]
    assert "segment" in by_ds["DS1"]
    assert "segment" in by_ds["DS2"]


# ── Real Phase 0 execution ──────────────────────────────────────────────


def test_compress_phase_produces_a_usable_h5(qtbot, tmp_path):
    """Phase 0 must leave a dataset later phases can actually read.

    The regression this pins: before the token_config fix the .h5 existed
    but carried no /intensity and no channel names, so the run continued
    against an empty dataset.
    """
    entry = _tiff_entry(tmp_path, "DS1")
    runner, _ = _runner(_config([entry], tmp_path))

    gen = runner._phase_generator()
    request = next(gen)
    assert request.phase_name == "compress"
    result = request.handler()

    assert result.success, result.message
    assert entry.h5_path.exists()

    store = DatasetStore(entry.h5_path)
    assert list(store.metadata["channel_names"]) == ["GFP", "RFP"]
    assert store.read_channel("intensity", 0).shape == (SIZE, SIZE)


def test_compress_swaps_entry_to_h5_existing(qtbot, tmp_path):
    """Later phases resolve paths off the swapped entry, not the plan."""
    entry = _tiff_entry(tmp_path, "DS1")
    runner, _ = _runner(_config([entry], tmp_path))

    gen = runner._phase_generator()
    next(gen).handler()

    swapped = runner._working_entries[0]
    assert swapped.source == DatasetSource.H5_EXISTING
    assert swapped.h5_path == entry.h5_path
    assert swapped.h5_path.exists()


def test_compress_of_an_unmatched_source_yields_an_empty_dataset(
    qtbot, tmp_path
):
    """Documents the gap U3 closes: an unmatched source compresses "fine".

    ``import_dataset`` does not raise when no file matches the token
    pattern — it writes an .h5 with ``channel_names == []``, ``n_channels
    == 0`` and no ``/intensity``, and ``compress_one`` reports success. That
    is exactly the state the dropped ``token_config`` produced, and it is
    why Phase 0 needs a post-compress validation gate rather than relying on
    ``import_dataset`` to fail loudly.

    The corresponding "and the dataset is then failed with a named message"
    assertions live in ``test_runner_post_compress_gate.py``.
    """
    entry = _tiff_entry(tmp_path, "DS1")
    entry.compress_plan["files"] = [str(tmp_path / "does_not_exist.tif")]
    entry.compress_plan["source_dir"] = str(tmp_path / "missing_dir")
    runner, _meta = _runner(_config([entry], tmp_path))

    gen = runner._phase_generator()
    request = next(gen)
    assert request.phase_name == "compress"
    request.handler()

    store = DatasetStore(entry.h5_path)
    assert list(store.metadata["channel_names"]) == []
    assert store.metadata["n_channels"] == 0


# ── Full run ────────────────────────────────────────────────────────────


def test_full_run_from_tiff_reaches_export(
    qtbot, fake_host, tmp_path, fake_segment_one
):
    """The whole point: a TIFF-start run completes and produces artifacts.

    This is the end-to-end assertion the plan called load-bearing — it fails
    on the pre-fix code because Phase 0 produces an .h5 with no channels.
    """
    entry = _tiff_entry(tmp_path, "DS1")
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _config([entry], tmp_path)
    runner, meta = _runner(cfg, run_folder)

    events = []
    runner.workflow_event.connect(lambda e: events.append(e))
    runner.start(cfg, fake_host, meta)

    finished = [e for e in events if e.kind is WorkflowEventKind.RUN_FINISHED]
    assert len(finished) == 1
    assert finished[0].success is True, finished[0].message
    assert meta.failures == [], [
        (f.dataset_name, f.phase_name, f.message) for f in meta.failures
    ]

    assert (run_folder / "measurements.parquet").is_file()
    assert (run_folder / "combined.csv").is_file()
    assert (run_folder / "per_dataset" / "DS1.csv").is_file()


def test_full_run_measures_cells_from_the_compressed_dataset(
    qtbot, fake_host, tmp_path, fake_segment_one
):
    """Measurements must reflect the TIFF pixel data, not an empty dataset."""
    import pandas as pd

    entry = _tiff_entry(tmp_path, "DS1")
    run_folder = create_run_folder(tmp_path / "runs")
    cfg = _config([entry], tmp_path)
    runner, meta = _runner(cfg, run_folder)
    runner.start(cfg, fake_host, meta)

    df = pd.read_parquet(run_folder / "measurements.parquet")
    assert len(df) == 4, f"expected 4 cells, got {len(df)}"
    assert set(df["dataset"].unique()) == {"DS1"}
    assert "GFP_mean_intensity" in df.columns
    # Non-zero intensities prove /intensity survived Phase 0.
    assert df["GFP_mean_intensity"].max() > 0
