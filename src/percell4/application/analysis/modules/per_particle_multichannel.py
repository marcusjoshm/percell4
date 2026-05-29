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


# ── produced_when callables (module-level so they're easy to find) ────


def _particle_table_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return not bool(params["single_cell"])


def _cell_table_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return bool(params["single_cell"])


def _donut_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return bool(params["export_donuts"])


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
    version = "1.0.0"
    description = (
        "Single-image-set per-particle condensed-vs-dilute (donut) "
        "quantification across up to eight measurement channels, with no "
        "background subtraction. Optional single-cell aggregation adds "
        "whole-cell intensity statistics."
    )

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

        result = run_one_image_set(
            mask=inputs["mask"],
            channels=channels,
            cp_mask=inputs.get("cp_mask"),
            buffer=params["buffer"],
            donut=params["donut"],
            min_size=params["min_size"],
            single_cell=params["single_cell"],
            export_donuts=params["export_donuts"],
            set_label=set_label,
            log=log,
        )

        out: dict[str, Any] = {}
        if result["particle_rows"] is not None:
            out["particle_table"] = pd.DataFrame(result["particle_rows"])
        if result["cell_rows"] is not None:
            out["cell_table"] = pd.DataFrame(result["cell_rows"])
        if result["donut_mask"] is not None:
            out["multichannel_donut_mask"] = result["donut_mask"]
        return out
