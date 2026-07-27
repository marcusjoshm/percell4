"""CNR population masks reach the measure phase (U5).

A ``cnr_classify`` round mints ``<round>_low`` / ``<round>_high`` masks as a
post-step. They are deliberately not rounds, and ``_measure_round_specs_for``
returned ``config.thresholding_rounds`` verbatim, so they were written to the
``.h5`` and never measured — the researcher had to re-run the whole workflow
in existing-mask mode to get particle statistics for them. The
``percell4-batch-measure`` CLI already measures them; this closes the gap.

Specs are derived per dataset from what is actually on disk, because a
dataset whose classification found a single population writes no ``_high``
mask.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from percell4.gui.workflows.single_cell.runner import SingleCellThresholdingRunner
from percell4.store import DatasetStore
from percell4.workflows.models import (
    AdaptiveClipSettings,
    CellposeSettings,
    CnrClassifySettings,
    DatasetSource,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)

SIZE = 20


def _make_h5(path: Path, masks: dict[str, np.ndarray] | None = None) -> None:
    store = DatasetStore(path)
    store.create(metadata={"channel_names": ["GFP"]})
    store.write_array(
        "intensity",
        np.zeros((SIZE, SIZE), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    for name, arr in (masks or {}).items():
        store.write_mask(name, arr)


def _mask() -> np.ndarray:
    arr = np.zeros((SIZE, SIZE), dtype=np.uint8)
    arr[5:9, 5:9] = 1
    return arr


def _entry(path: Path, name: str) -> WorkflowDatasetEntry:
    return WorkflowDatasetEntry(
        name=name,
        source=DatasetSource.H5_EXISTING,
        h5_path=path,
        channel_names=["GFP"],
    )


def _cnr_round(name: str = "SG") -> ThresholdingRound:
    return ThresholdingRound(
        name=name,
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        adaptive_clip=AdaptiveClipSettings(d_min_um=3.0, d_min_unit="px"),
        cnr_classify=CnrClassifySettings(threshold=16.0),
    )


def _plain_round(name: str = "SG") -> ThresholdingRound:
    return ThresholdingRound(
        name=name,
        channel="GFP",
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
    )


def _runner(entries, rounds, tmp_path, **overrides):
    base = dict(
        datasets=entries,
        cellpose=CellposeSettings(diameter=8.0, gpu=False, min_size=5),
        thresholding_rounds=rounds,
        selected_csv_columns=["GFP_mean_intensity"],
        output_parent=tmp_path / "runs",
        seg_channel_name="GFP",
    )
    base.update(overrides)
    cfg = WorkflowConfig(**base)
    meta = RunMetadata(
        run_id="r",
        run_folder=Path("/tmp/r"),
        started_at=datetime.now(UTC),
        intersected_channels=["GFP"],
    )
    return SingleCellThresholdingRunner(
        config=cfg, metadata=meta, interactive_qc=False
    ), meta


def _spec_names(runner, entry) -> list[str]:
    return [s.name for s in runner._measure_round_specs_for(entry)]


# ── Happy path ──────────────────────────────────────────────────────────


def test_both_population_masks_are_measured(qtbot, tmp_path):
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_low": _mask(), "SG_high": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_cnr_round()], tmp_path)

    assert _spec_names(runner, entry) == ["SG", "SG_low", "SG_high"]


def test_population_specs_inherit_the_rounds_channel(qtbot, tmp_path):
    """measure_one reads only round.name, but __post_init__ validates the rest."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG_low": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_cnr_round()], tmp_path)

    low = [s for s in runner._measure_round_specs_for(entry) if s.name == "SG_low"]
    assert low[0].channel == "GFP"


# ── Derived from disk, not from config ──────────────────────────────────


def test_single_population_dataset_gets_only_the_mask_that_exists(
    qtbot, tmp_path
):
    """A frame that did not split writes no _high mask."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_low": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_cnr_round()], tmp_path)

    assert _spec_names(runner, entry) == ["SG", "SG_low"]


def test_high_without_low_is_handled(qtbot, tmp_path):
    """_classify_and_write_cnr skips an all-zero population either direction."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_high": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_cnr_round()], tmp_path)

    assert _spec_names(runner, entry) == ["SG", "SG_high"]


def test_no_population_masks_on_disk_yields_only_the_base_round(
    qtbot, tmp_path
):
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_cnr_round()], tmp_path)

    assert _spec_names(runner, entry) == ["SG"]


def test_two_datasets_get_independent_spec_lists(qtbot, tmp_path):
    """One split, one did not — neither inherits the other's masks."""
    split = tmp_path / "SPLIT.h5"
    flat = tmp_path / "FLAT.h5"
    _make_h5(split, masks={"SG": _mask(), "SG_low": _mask(), "SG_high": _mask()})
    _make_h5(flat, masks={"SG": _mask(), "SG_low": _mask()})
    e1, e2 = _entry(split, "SPLIT"), _entry(flat, "FLAT")
    runner, _ = _runner([e1, e2], [_cnr_round()], tmp_path)

    assert _spec_names(runner, e1) == ["SG", "SG_low", "SG_high"]
    assert _spec_names(runner, e2) == ["SG", "SG_low"]


# ── Stays out of the way ────────────────────────────────────────────────


def test_round_without_cnr_classify_probes_nothing(qtbot, tmp_path):
    """Suffixed masks left by an earlier run must not be picked up."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_low": _mask(), "SG_high": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_plain_round()], tmp_path)

    assert _spec_names(runner, entry) == ["SG"]


def test_existing_mask_mode_is_untouched(qtbot, tmp_path):
    """The user's explicit selections stay authoritative."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_low": _mask(), "SG_high": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner(
        [entry],
        [],
        tmp_path,
        use_existing_masks=True,
        existing_mask_selections={"DS1": ["SG"]},
    )

    assert _spec_names(runner, entry) == ["SG"]


# ── Never aborts the batch ──────────────────────────────────────────────


def test_unreadable_store_degrades_to_the_base_rounds(qtbot, tmp_path):
    """_measure_round_specs_for is called outside the handler's try.

    A raise here would reach BaseWorkflowRunner._run_loop and terminate the
    entire run, turning one unreadable dataset into a batch-wide abort.
    """
    entry = _entry(tmp_path / "missing.h5", "DS1")
    runner, meta = _runner([entry], [_cnr_round()], tmp_path)

    assert _spec_names(runner, entry) == ["SG"]
    assert meta.failures == []


def test_overlong_suffixed_name_is_skipped_not_failed(qtbot, tmp_path):
    """A 38-char round name overflows the 40-char limit once suffixed.

    The dataset did nothing wrong, so it must not collect a
    MEASUREMENT_ERROR — the base round still measures.
    """
    long_name = "R" * 38
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={long_name: _mask(), f"{long_name}_low": _mask()})
    entry = _entry(p, "DS1")
    runner, meta = _runner([entry], [_cnr_round(long_name)], tmp_path)
    logged: list[dict] = []
    runner._log = lambda **fields: logged.append(fields)

    assert _spec_names(runner, entry) == [long_name]
    assert meta.failures == []
    assert any(e.get("event") == "cnr_population_skipped" for e in logged)


def test_export_round_names_stay_pinned_to_configured_rounds(qtbot, tmp_path):
    """Population masks must not inflate n_rounds_thresholding.

    Passing them through the export round_names override would add zero
    summary_groups.csv rows -- _build_summary_groups selects by a
    group_<round_name> column that needs a /groups/<name> table, which the CNR
    post-step never writes -- while silently changing what
    n_rounds_thresholding means. Asserted here so a future change is
    deliberate rather than incidental.
    """
    from unittest.mock import patch

    import percell4.gui.workflows.single_cell.runner as runner_mod

    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_low": _mask(), "SG_high": _mask()})
    runner, _ = _runner([_entry(p, "DS1")], [_cnr_round()], tmp_path)

    with patch.object(
        runner_mod, "export_run", return_value=(None, "")
    ) as mock_export:
        runner._make_export_handler()()

    assert mock_export.call_args.args[3] is None, (
        "CNR population names must not reach export_run's round_names"
    )


def test_measured_populations_are_disclosed_in_the_run_log(qtbot, tmp_path):
    """The added measure cost should be visible, not mysterious."""
    p = tmp_path / "DS1.h5"
    _make_h5(p, masks={"SG": _mask(), "SG_low": _mask(), "SG_high": _mask()})
    entry = _entry(p, "DS1")
    runner, _ = _runner([entry], [_cnr_round()], tmp_path)
    logged: list[dict] = []
    runner._log = lambda **fields: logged.append(fields)

    runner._measure_round_specs_for(entry)

    disclosures = [
        e for e in logged if e.get("event") == "cnr_populations_measured"
    ]
    assert len(disclosures) == 1
    assert "SG_low" in disclosures[0]["message"]
    assert "SG_high" in disclosures[0]["message"]
