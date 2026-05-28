"""``PerParticleDonut`` — register the per-particle donut analysis.

Schema-only :class:`Analysis` subclass that wires the pure
:func:`percell4.domain.analysis._impl.per_particle_donut.run_one_image_set`
into the registered-analyses framework introduced in U1–U4.

The declared schema matches the original CLI's argparse + ``PRESETS`` /
``ORIGINAL_DEFAULTS`` verbatim, so a user mapping ``.h5`` layers to
roles and picking the ``m7g-cap-v1`` preset gets identical scientific
output to the CLI (numeric parity per the U4 regression fixture).

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``,
unit U5.
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
    ChoiceParam,
    FloatParam,
    GroupState,
    ImageOutput,
    ImageRole,
    IntParam,
    TableOutput,
)
from percell4.domain.analysis._impl.per_particle_donut import (
    run_one_image_set,
)

# ── produced_when callables (module-level so they're easy to find) ────


def _pbody_table_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return g.pbody_satisfied


def _sg_table_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return g.sg_satisfied


def _pbody_donut_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return g.pbody_satisfied and bool(params["export_donuts"])


def _sg_donut_produced(g: GroupState, params: dict[str, Any]) -> bool:
    return g.sg_satisfied and bool(params["export_donuts"])


# ── Class ─────────────────────────────────────────────────────────────


@register_analysis("per_particle_donut")
class PerParticleDonut(Analysis):
    """Per-particle donut background-subtraction analysis.

    Quantifies fluorescence in masked subcellular regions (P-bodies
    and/or Stress Granules) with a per-particle local-background donut
    around each region. Supports an optional single-cell aggregation
    mode that groups particle measurements by a cell-segmentation
    label image.

    The math lives in
    :mod:`percell4.domain.analysis._impl.per_particle_donut`; this class
    is the declarative bridge that exposes the math to the framework's
    runner, registry, and (eventually) GUI dialog.
    """

    # ── Identity ──────────────────────────────────────────────────
    name = "per_particle_donut"
    display_name = "Per-particle donut background subtraction"
    version = "1.0.0"
    description = (
        "Single-image-set per-particle quantification with donut-shaped "
        "local background subtraction. Supports P-body and Stress Granule "
        "analysis branches; optional cell-segmentation aggregation."
    )

    # ── Inputs ────────────────────────────────────────────────────
    required_inputs = {
        "cap": ImageRole(
            kind="intensity",
            dtype="float",
            desc="Capture (signal) channel",
        ),
    }
    input_groups = {
        "pbody": {
            "pbody_mask": ImageRole(
                kind="mask",
                dtype="binary",
                desc="P-body binary mask",
            ),
            "pnorm": ImageRole(
                kind="intensity",
                dtype="float",
                desc="P-body normalization channel",
            ),
        },
        "sg": {
            "sg_mask": ImageRole(
                kind="mask",
                dtype="binary",
                desc="Stress Granule binary mask",
            ),
            "sgnorm": ImageRole(
                kind="intensity",
                dtype="float",
                desc="Stress Granule normalization channel",
            ),
        },
    }
    group_requirement = "at_least_one"
    optional_inputs = {
        "cp_mask": ImageRole(
            kind="label",
            dtype="labels",
            desc="Cell-segmentation labels (for single_cell mode)",
        ),
    }

    # ── Parameters ────────────────────────────────────────────────
    # Defaults mirror the CLI's ``ORIGINAL_DEFAULTS`` (see
    # ``per_particle_analysis.py`` lines ~157-167). ChoiceParam.choices
    # for ``bg_mode`` mirror the CLI's ``--bg-mode`` argparse choices.
    parameters = {
        "buffer": IntParam(
            default=4,
            min=0,
            desc="Buffer-zone dilation in pixels around each particle "
            "before the donut starts.",
        ),
        "donut": IntParam(
            default=5,
            min=1,
            desc="Donut ring width in pixels.",
        ),
        "bg_mode": ChoiceParam(
            choices=("donut", "donut-mean", "flat"),
            default="donut",
            desc="Background subtraction mode: 'donut' uses the donut "
            "median, 'donut-mean' uses the donut mean, 'flat' uses a "
            "single fixed value.",
        ),
        "bg_value": IntParam(
            default=1,
            desc="Flat background value when bg_mode='flat'.",
        ),
        "exclude_cap_zero": BoolParam(
            default=True,
            desc="Exclude zero values from the Cap channel before "
            "estimating background.",
        ),
        "min_size": IntParam(
            default=10,
            min=0,
            desc="Only analyze particles larger than this many pixels.",
        ),
        "bgsub_k": FloatParam(
            default=2.5,
            desc="Background-subtraction threshold expressed as "
            "mu + k*sigma of the background Gaussian fit. Higher k is "
            "more permissive.",
        ),
        "no_bgsub": BoolParam(
            default=False,
            desc="Disable automatic background subtraction of the Cap "
            "image.",
        ),
        "single_cell": BoolParam(
            default=False,
            requires=("cp_mask",),
            desc="Aggregate all particle measurements by single cell "
            "using the cp_mask segmentation. Each integer value in the "
            "mask represents one cell.",
        ),
        "export_donuts": BoolParam(
            default=False,
            desc="Export binary donut masks (one per branch) for "
            "overlay visualization.",
        ),
    }

    # ── Presets ───────────────────────────────────────────────────
    # Presets are immutable — never modify in place. Versioning a
    # preset's behaviour is done by creating a new key (e.g.
    # ``m7g-cap-v2``). The snapshot test
    # (``tests/test_application/test_presets_immutable.py``) keeps the
    # in-code values in sync with
    # ``tests/fixtures/preset_snapshots/per_particle_donut.json``.
    presets = {
        "m7g-cap-v1": {
            "buffer": 5,
            "donut": 5,
            "bg_mode": "donut-mean",
            "min_size": 4,
            "bgsub_k": 2.5,
            "no_bgsub": False,
            "bg_value": 1,
            "exclude_cap_zero": True,
            "export_donuts": False,
        },
    }

    # ── Outputs ───────────────────────────────────────────────────
    outputs = {
        "pbody_table": TableOutput(
            produced_when=_pbody_table_produced,
            desc="Per-P-body row table (or per-cell rows when "
            "single_cell=True).",
        ),
        "sg_table": TableOutput(
            produced_when=_sg_table_produced,
            desc="Per-SG row table (or per-cell rows when "
            "single_cell=True).",
        ),
        "pbody_donut_mask": ImageOutput(
            dtype="binary",
            produced_when=_pbody_donut_produced,
            desc="Union of P-body donut pixels as a uint8 mask "
            "(255 inside donut, 0 elsewhere).",
        ),
        "sg_donut_mask": ImageOutput(
            dtype="binary",
            produced_when=_sg_donut_produced,
            desc="Union of SG donut pixels as a uint8 mask "
            "(255 inside donut, 0 elsewhere).",
        ),
    }

    # GUI dialog is wired in U7; left None here so the Scripts tab
    # falls back to a stub message until then.
    dialog_class = None

    # ── Pure run ──────────────────────────────────────────────────
    def run(
        self,
        inputs: dict[str, NDArray],
        params: dict[str, Any],
        *,
        log: Callable[[str], None] | None = None,
        set_label: str = "",
    ) -> dict[str, Any]:
        """Dispatch to :func:`run_one_image_set` and pack the result.

        The loader supplies only those role keys that were present in
        the layer map (required + supplied optional + supplied group
        roles). Optional/group roles are passed through ``inputs.get``
        so an absent role becomes ``None`` for the pure function.

        ``log`` and ``set_label`` are forwarded to the pure core so the
        same per-step progress the CLI streams (background subtraction,
        per-branch particle counts, single-cell aggregation) is emitted
        when a sink is supplied. They default to silent.
        """
        result = run_one_image_set(
            cap=inputs["cap"],
            pbody_mask=inputs.get("pbody_mask"),
            pnorm=inputs.get("pnorm"),
            sg_mask=inputs.get("sg_mask"),
            sgnorm=inputs.get("sgnorm"),
            cp_mask=inputs.get("cp_mask"),
            set_label=set_label,
            log=log,
            **params,
        )

        out: dict[str, Any] = {}
        if result["pbody_rows"] is not None:
            out["pbody_table"] = pd.DataFrame(result["pbody_rows"])
        if result["sg_rows"] is not None:
            out["sg_table"] = pd.DataFrame(result["sg_rows"])
        if result["pbody_donut_mask"] is not None:
            out["pbody_donut_mask"] = result["pbody_donut_mask"]
        if result["sg_donut_mask"] is not None:
            out["sg_donut_mask"] = result["sg_donut_mask"]
        return out
