"""CNR population-mask columns survive CSV export (U6).

U5 makes the runner measure ``<round>_low`` / ``<round>_high``, but
``_ordered_csv_columns`` keeps only columns that are BOTH selected at config
time and present in the aggregated frame. Without predicting the population
names at config time the measurements land in ``measurements.parquet`` and are
filtered out of ``combined.csv`` and every per-dataset CSV.

Deliberately NOT done here: extending the export ``round_names`` override.
That would add zero ``summary_groups.csv`` rows — ``_build_summary_groups``
selects by a ``group_<round_name>`` column that only exists when a
``/groups/<name>`` table was written, and the CNR post-step writes masks plus
``/classification/<round>`` but never a group table — while silently changing
what ``n_rounds_thresholding`` means.
"""

from __future__ import annotations

from percell4.gui.workflows.single_cell.config_dialog import (
    _round_names_with_cnr_populations,
)
from percell4.workflows.csv_columns import build_selected_csv_columns
from percell4.workflows.models import (
    AdaptiveClipSettings,
    CnrClassifySettings,
    ThresholdAlgorithm,
    ThresholdingRound,
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


# ── Name expansion ──────────────────────────────────────────────────────


def test_cnr_round_contributes_both_population_names() -> None:
    assert _round_names_with_cnr_populations([_cnr_round()]) == [
        "SG",
        "SG_low",
        "SG_high",
    ]


def test_plain_round_contributes_only_itself() -> None:
    assert _round_names_with_cnr_populations([_plain_round()]) == ["SG"]


def test_population_names_follow_their_own_round() -> None:
    """Ordering matters — CSV columns are emitted in round order."""
    rounds = [_plain_round("A"), _cnr_round("B"), _plain_round("C")]

    assert _round_names_with_cnr_populations(rounds) == [
        "A",
        "B",
        "B_low",
        "B_high",
        "C",
    ]


def test_empty_round_list_yields_no_names() -> None:
    assert _round_names_with_cnr_populations([]) == []


def test_multiple_cnr_rounds_each_expand() -> None:
    names = _round_names_with_cnr_populations([_cnr_round("A"), _cnr_round("B")])
    assert names == ["A", "A_low", "A_high", "B", "B_low", "B_high"]


# ── Effect on the exported column list ──────────────────────────────────


def test_population_columns_reach_the_selected_column_list() -> None:
    """The columns the researcher opens combined.csv to find."""
    cols = build_selected_csv_columns(
        ["GFP"],
        _round_names_with_cnr_populations([_cnr_round()]),
        metrics=["mean_intensity"],
    )

    assert "GFP_mean_intensity_in_SG" in cols
    assert "GFP_mean_intensity_in_SG_low" in cols
    assert "GFP_mean_intensity_in_SG_high" in cols


def test_non_cnr_config_column_list_is_unchanged() -> None:
    """A run with no CNR round must export byte-identical columns."""
    rounds = [_plain_round("A"), _plain_round("B")]
    before = build_selected_csv_columns(
        ["GFP"], [r.name for r in rounds], metrics=["mean_intensity"]
    )
    after = build_selected_csv_columns(
        ["GFP"],
        _round_names_with_cnr_populations(rounds),
        metrics=["mean_intensity"],
    )

    assert after == before


def test_particle_columns_are_emitted_for_populations() -> None:
    """Particle analysis is the reason the researcher asked for this."""
    cols = build_selected_csv_columns(
        ["GFP"],
        _round_names_with_cnr_populations([_cnr_round()]),
        metrics=[],
        particle_per_cell=["particle_count"],
    )

    assert any("SG_low" in c and "particle_count" in c for c in cols), cols
    assert any("SG_high" in c and "particle_count" in c for c in cols), cols
