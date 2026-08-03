"""``PerParticleMultichannel`` — register the multichannel donut analysis.

Schema-only :class:`Analysis` subclass that wires the pure
:func:`percell4.domain.analysis._impl.per_particle_multichannel.run_one_image_set`
into the registered-analyses framework.

The channel pool is modeled as ``channel_1`` (required) plus
``channel_2..channel_8`` (optional) — NOT a single input group, because a
group with ``group_requirement="at_least_one"`` is satisfied only when
*all* its roles are supplied, which would force all 8 channels. Output
columns are named after the user-chosen layer for each slot, recovered
from the ``layer_map`` kwarg (the loader keys inputs by role and drops
the layer names).

Plan: ``docs/plans/2026-05-28-001-feat-incorporate-whole-field-multichannel-analyses-plan.md``
(unit U4).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from numpy.typing import NDArray

from percell4.application.analysis.registry import register_analysis
from percell4.domain.analysis import (
    Analysis,
    BoolParam,
    GroupState,
    ImageOutput,
    ImageRole,
    IntParam,
    TableOutput,
)
from percell4.domain.analysis._impl.per_particle_multichannel import (
    run_one_image_set,
)

# Maximum number of measurement-channel slots. The dialog (U5) lets the
# user add up to this many channel rows; channel_1 is required, the rest
# optional.
_MAX_CHANNELS = 8
_CHANNEL_ROLES = [f"channel_{i}" for i in range(1, _MAX_CHANNELS + 1)]
# Per-slot "add a whole-cell mean column" toggles. The dialog renders these
# as a checkbox on each channel row rather than in the generic param form,
# so it filters them out by this key set.
_CELL_MEAN_PARAM_KEYS = frozenset(
    f"channel_{i}_cell_mean" for i in range(1, _MAX_CHANNELS + 1)
)


# ── produced_when callables (module-level so they're easy to find) ────


def _particle_table_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return not bool(params["single_cell"])


def _cell_table_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return bool(params["single_cell"])


def _donut_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return bool(params["export_donuts"])


def _particle_column_order(
    *,
    analyzed: list[str],
    cell_mean_channels: list[str],
    has_cp: bool,
) -> list[str]:
    """Exact particle_table column order, matching the original make_csv.py.

    ``group`` is prepended later by the batch runner, so it is absent here.
    ``analyzed`` and ``cell_mean_channels`` are expected pre-sorted by the
    caller; the cell-mean infixes are therefore a subsequence of the
    analyzed infixes. The list is built only from these known channel
    names — never by parsing existing column strings — so a channel named
    e.g. ``cell`` cannot corrupt the ordering.
    """
    order: list[str] = ["particle_id"]
    if has_cp:
        order.append("cell_id")
    order += [f"cell_{ch}_mean" for ch in cell_mean_channels]
    order += ["particle_area_px", "donut_area_px"]
    for ch in analyzed:
        order += [
            f"condensed_{ch}_mean",
            f"dilute_{ch}_mean",
            f"{ch}_condensed_over_dilute",
        ]
    return order


# ── Class ─────────────────────────────────────────────────────────────


@register_analysis("per_particle_multichannel")
class PerParticleMultichannel(Analysis):
    """Per-particle multi-channel dilute-vs-condensed phase analysis.

    For each particle in a binary mask, measures mean and integrated
    intensity inside the particle (condensed) and in a donut ring around
    it (dilute) for every supplied channel, plus the condensed/dilute
    ratio. No background subtraction, no normalization channel. An
    optional single-cell mode aggregates per cell and adds whole-cell
    intensity statistics.

    The math lives in
    :mod:`percell4.domain.analysis._impl.per_particle_multichannel`.
    """

    # ── Identity ──────────────────────────────────────────────────
    name = "per_particle_multichannel"
    display_name = "Per-particle multi-channel intensity"
    # 1.1.0: particle_table reshaped to the exact original-script column
    # order (no *_integ), gained optional per-particle cell_<ch>_mean
    # columns, and renamed its CSV id column to "group".
    version = "1.1.0"
    description = (
        "Single-image-set per-particle condensed-vs-dilute (donut) "
        "quantification across up to eight measurement channels, with no "
        "background subtraction. Optional single-cell aggregation adds "
        "whole-cell intensity statistics."
    )

    # CSV identity column named "group" (= .h5 filename stem) to match the
    # original make_csv.py output. Applies to every table this analysis
    # emits (particle_table and, in single-cell mode, cell_table).
    dataset_column_label = "group"

    # ── Inputs ────────────────────────────────────────────────────
    # channel_1 required + channel_2..8 optional (no group). Modeling the
    # slots as a single at_least_one group would require ALL eight.
    required_inputs = {
        "mask": ImageRole(
            kind="mask",
            dtype="binary",
            desc="Particle (condensed-phase) binary mask",
        ),
        "channel_1": ImageRole(
            kind="intensity",
            dtype="float",
            desc="Measurement channel 1 (required)",
        ),
    }
    optional_inputs = {
        "cp_mask": ImageRole(
            kind="label",
            dtype="labels",
            desc="Cell-segmentation labels (for single_cell mode / cell_id)",
        ),
        **{
            role: ImageRole(
                kind="intensity",
                dtype="float",
                desc=f"Measurement channel {i} (optional)",
            )
            for i, role in enumerate(_CHANNEL_ROLES[1:], start=2)
        },
    }

    # ── Parameters ────────────────────────────────────────────────
    # Defaults mirror the CLI's argparse defaults (per_particle_multichannel.py).
    parameters = {
        "buffer": IntParam(
            default=5,
            min=0,
            desc="Buffer-zone dilation in pixels around each particle "
            "before the donut starts.",
        ),
        "donut": IntParam(
            default=5,
            min=1,
            desc="Donut ring width in pixels.",
        ),
        "min_size": IntParam(
            default=4,
            min=0,
            desc="Only analyze particles larger than this many pixels.",
        ),
        "single_cell": BoolParam(
            default=False,
            requires=("cp_mask",),
            desc="Aggregate particle measurements by single cell using a "
            "cell-segmentation label image; adds whole-cell statistics.",
        ),
        "export_donuts": BoolParam(
            default=False,
            desc="Export a binary union donut mask for overlay "
            "visualization.",
        ),
        # Per-slot "add cell_<layer>_mean" toggles. Deliberately NO
        # requires=("cp_mask",): a True flag without a cp_mask would make
        # the runner raise and fail the whole dataset. Instead run() drops
        # cell-mean when no cp_mask is mapped; the dialog disables the
        # checkboxes for UX only.
        **{
            f"channel_{i}_cell_mean": BoolParam(
                default=False,
                desc=f"Add a whole-cell-mean column (cell_<layer>_mean) for "
                f"measurement channel {i}. Needs a cell-segmentation label "
                f"image (cp_mask); ignored without one.",
            )
            for i in range(1, _MAX_CHANNELS + 1)
        },
    }
    presets: dict[str, dict[str, Any]] = {}

    # ── Outputs ───────────────────────────────────────────────────
    outputs = {
        "particle_table": TableOutput(
            produced_when=_particle_table_produced,
            desc="One row per particle (when single_cell is off).",
        ),
        "cell_table": TableOutput(
            produced_when=_cell_table_produced,
            desc="One row per cell (when single_cell is on).",
        ),
        "multichannel_donut_mask": ImageOutput(
            dtype="binary",
            produced_when=_donut_produced,
            desc="Union of per-particle donut rings (export_donuts).",
        ),
    }

    # ── Interface binding (set by GUI layer in U5) ─────────────────
    dialog_class = None

    # ── Pure run ──────────────────────────────────────────────────
    def run(
        self,
        inputs: dict[str, NDArray],
        params: dict[str, Any],
        *,
        log: Callable[[str], None] | None = None,
        set_label: str = "",
        layer_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Dispatch to :func:`run_one_image_set` and pack the result.

        The channel slots present in ``inputs`` are keyed by their chosen
        on-disk layer name (from ``layer_map``) so output columns read
        ``condensed_<layername>_mean`` rather than ``condensed_channel_1_mean``;
        channels are passed in sorted-by-name order to match the CLI.
        Exactly one table is returned (``particle_table`` XOR
        ``cell_table``) matching ``single_cell``, plus the donut mask only
        when ``export_donuts``.
        """
        names = layer_map or {}
        channels: dict[str, NDArray] = {}
        for role in _CHANNEL_ROLES:
            if role in inputs:
                channels[names.get(role, role)] = inputs[role]
        channels = dict(sorted(channels.items()))
        if not channels:
            raise ValueError(
                "per_particle_multichannel: at least one measurement "
                "channel must be supplied"
            )

        has_cp = "cp_mask" in inputs
        # Channels selected for a whole-cell-mean column — only when a
        # cp_mask is mapped (no cp_mask → silently no cell-mean, never a
        # raise; see the no-requires note on the parameters).
        cell_mean_channels = (
            sorted(
                {
                    names.get(role, role)
                    for role in _CHANNEL_ROLES
                    if role in inputs and params.get(f"{role}_cell_mean")
                }
            )
            if has_cp
            else []
        )

        result = run_one_image_set(
            mask=inputs["mask"],
            channels=channels,
            cp_mask=inputs.get("cp_mask"),
            buffer=params["buffer"],
            donut=params["donut"],
            min_size=params["min_size"],
            single_cell=params["single_cell"],
            export_donuts=params["export_donuts"],
            cell_mean_channels=cell_mean_channels,
            set_label=set_label,
            log=log,
        )

        out: dict[str, Any] = {}
        if result["particle_rows"] is not None:
            df = pd.DataFrame(result["particle_rows"])
            order = _particle_column_order(
                analyzed=list(channels.keys()),
                cell_mean_channels=cell_mean_channels,
                has_cp=has_cp,
            )
            # reindex (not df[order]): NaN-fills the contract columns for an
            # empty (no-particle) frame instead of raising, and drops the
            # core's *_integ columns that aren't in the target order.
            # Guard: when the frame is non-empty, every target column must
            # already exist — otherwise the core stopped emitting an
            # expected column and reindex would silently NaN-fill it. Fail
            # loud instead of shipping a silently-corrupt CSV.
            if len(df):
                missing = [c for c in order if c not in df.columns]
                if missing:
                    raise ValueError(
                        "per_particle_multichannel: expected particle_table "
                        f"columns missing from core output: {missing}"
                    )
            out["particle_table"] = df.reindex(columns=order)
        if result["cell_rows"] is not None:
            out["cell_table"] = pd.DataFrame(result["cell_rows"])
        if result["donut_mask"] is not None:
            out["multichannel_donut_mask"] = result["donut_mask"]
        return out
