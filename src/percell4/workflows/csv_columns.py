"""Shared, Qt-free CSV column-selection defaults and assembly.

The single source of truth for which measurement columns land in the
workflow's ``combined.csv`` / ``per_dataset/*.csv`` exports. Both the GUI
config dialog (``WorkflowConfigDialog``) and the headless
``percell4-batch-measure`` CLI build their selected-column lists from
:func:`build_selected_csv_columns` seeded by the ``DEFAULT_*`` constants,
so the two surfaces cannot drift.

No Qt, no h5py — importable from anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable

# Identity-ish core columns always offered (the export step additionally
# prepends dataset/cell_id/label). Order is display order.
CORE_OPTIONAL_COLUMNS: tuple[str, ...] = (
    "centroid_y",
    "centroid_x",
    "bbox_y",
    "bbox_x",
    "bbox_h",
    "bbox_w",
    "area",
)

# Default per-cell whole-cell metric selection.
DEFAULT_CSV_METRICS: frozenset[str] = frozenset(
    {"area", "integrated_intensity", "mean_intensity"}
)

# Default particle-analysis selections (U7). Per-cell summary metrics emit
# one ``<round>_<metric>`` column; per-channel metrics emit one
# ``<round>_<channel>_<metric>`` column.
DEFAULT_CSV_PARTICLE_PER_CELL: frozenset[str] = frozenset(
    {"particle_count", "total_particle_area", "mean_particle_area"}
)
DEFAULT_CSV_PARTICLE_PER_CHANNEL: frozenset[str] = frozenset(
    {"particle_mean_intensity"}
)


# Full selections for the ``--csv-preset all`` convenience (and the GUI
# picker's "select all"). Kept here as the Qt-free source of truth.
ALL_PARTICLE_PER_CELL: tuple[str, ...] = (
    "particle_count",
    "total_particle_area",
    "mean_particle_area",
    "max_particle_area",
    "particle_coverage_fraction",
)
ALL_PARTICLE_PER_CHANNEL: tuple[str, ...] = (
    "particle_mean_intensity",
    "particle_max_intensity",
    "particle_min_intensity",
    "particle_integrated_intensity",
    "particle_std_intensity",
    "particle_median_intensity",
    "particle_mode_intensity",
    "particle_sg_ratio",
)


def _is_area_column(col: str) -> bool:
    return (
        col == "area"
        or col.endswith("_area")
        or "_area_in_" in col
        or col.endswith("_particle_area")
    )


def build_selected_csv_columns(
    channels: list[str],
    round_names: list[str],
    *,
    metrics: Iterable[str] = DEFAULT_CSV_METRICS,
    particle_per_cell: Iterable[str] = DEFAULT_CSV_PARTICLE_PER_CELL,
    particle_per_channel: Iterable[str] = DEFAULT_CSV_PARTICLE_PER_CHANNEL,
) -> list[str]:
    """Assemble the full CSV column list for a channel × metric × round selection.

    Returns core columns, the ``{channel}_{metric}`` whole-cell columns,
    ``group_{round}`` columns, ``{channel}_{metric}_in_{round}`` mask-overlap
    columns, the particle columns (``{round}_{metric}`` per-cell and
    ``{round}_{channel}_{metric}`` per-channel), and a ``<col>_um2`` sibling
    for every area-style column. Identity columns (dataset/cell_id/label)
    are prepended later by the export step, so they are not included here.

    Columns absent from the measured DataFrame are filtered out downstream
    by the export writer's ``c in df.columns`` guard, so over-listing is safe.
    """
    metrics_sorted = sorted(metrics)
    ppc_sorted = sorted(particle_per_cell)
    ppch_sorted = sorted(particle_per_channel)

    cols: list[str] = list(CORE_OPTIONAL_COLUMNS)

    for ch in channels:
        for m in metrics_sorted:
            cols.append(f"{ch}_{m}")

    for rn in round_names:
        cols.append(f"group_{rn}")

    for ch in channels:
        for m in metrics_sorted:
            for rn in round_names:
                cols.append(f"{ch}_{m}_in_{rn}")

    for rn in round_names:
        for m in ppc_sorted:
            cols.append(f"{rn}_{m}")
        for ch in channels:
            for m in ppch_sorted:
                cols.append(f"{rn}_{ch}_{m}")

    # Emit a `<col>_um2` sibling for every area-style column. Snapshot the
    # list first: `_area_in_` matches the `_um2` sibling too, so iterating
    # the live list while extending it would loop forever.
    cols.extend(f"{c}_um2" for c in list(cols) if _is_area_column(c))
    return cols
