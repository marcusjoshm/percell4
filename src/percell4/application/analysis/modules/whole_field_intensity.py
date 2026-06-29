"""``WholeFieldIntensity`` — register the whole-field decapping-sensor analysis.

Schema-only :class:`Analysis` subclass wrapping the pure
:func:`percell4.domain.analysis._impl.whole_field_intensity.run_one_image_set`.

Modeling notes (see the plan's Key Technical Decisions):

* The four intermediate masks are plain ``optional_inputs`` (NOT a group):
  a single ``at_least_one`` group would make all four de-facto required and
  break the two-region / v2 / v3 runs. The v4/v5 requirement is enforced via
  ``intermediate_assemblies = BoolParam(requires=(...four roles...))``.
* The dual-typed CLI background mode (keyword OR integer) is modeled as a
  ``ChoiceParam`` (keywords + ``"manual"``) plus an ``IntParam`` manual value;
  ``run()`` maps ``"manual"`` to the integer and other choices to the keyword.
* The 3-way filter choices include ``"none"``; ``run()`` maps ``"none" → None``
  to hit the pure core's ``is None`` branches.
* Cross-cutting constraints the schema cannot express (mutually-exclusive
  options; intermediate requiring the filter choices to be set) are enforced
  as ``ValueError`` guards in ``run()`` — a deliberate stricter upgrade over
  the CLI's warn-and-abort.

Preset ``decapping-sensor-v1`` (cross-dataset ``SiR_mean``) is intentionally
not shipped — the framework is strictly per-dataset.

Plan: ``docs/plans/2026-05-28-001-feat-incorporate-whole-field-multichannel-analyses-plan.md``
(unit U7).
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
    ImageRole,
    IntParam,
    TableOutput,
)
from percell4.domain.analysis._impl.whole_field_intensity import (
    run_one_image_set,
)

_MNG_BG_CHOICES = (
    "mean", "median", "mode", "top_quintile", "top_quartile", "top_decile",
    "manual",
)
_HALO_BG_CHOICES = (
    "mean", "median", "mode", "top_quintile", "top_quartile", "top_decile",
    "mng-nan", "mng-nan-median", "mng-nan-mode", "mng-nan-top_quintile",
    "mng-nan-top_quartile", "mng-nan-top_decile", "mng-nan-max", "manual",
)
_FILTER_CHOICES = ("none", "zero", "NaN")


@register_analysis("whole_field_intensity")
class WholeFieldIntensity(Analysis):
    """Whole-field decapping-sensor intensity analysis.

    Aggregate (not per-particle) quantification of a Halo channel vs an mNG
    normalization channel across a P-body compartment and a dilute-cytoplasm
    compartment (plus an optional v4/v5 intermediate compartment), with
    per-field background subtraction and an optional single-cell mode.

    The math lives in
    :mod:`percell4.domain.analysis._impl.whole_field_intensity`.
    """

    # ── Identity ──────────────────────────────────────────────────
    name = "whole_field_intensity"
    display_name = "Whole-field decapping-sensor intensity"
    version = "1.0.0"
    description = (
        "Whole-field (aggregate) mNG/Halo compartment quantification with "
        "per-field background subtraction. Presets decapping-sensor-v2..v6; "
        "optional three-region (intermediate) and single-cell modes."
    )

    # ── Inputs ────────────────────────────────────────────────────
    required_inputs = {
        "condensate_mask": ImageRole(kind="mask", dtype="binary",
                                     desc="Condensate binary mask "
                                     "(P-body / stress granule)"),
        "dilute_mask": ImageRole(kind="mask", dtype="binary",
                                 desc="Dilute-cytoplasm mask (bg region)"),
        "halo": ImageRole(kind="intensity", dtype="float",
                          desc="Halo (decapping-sensor) channel"),
        "mng": ImageRole(kind="intensity", dtype="float",
                         desc="mNeonGreen normalization channel"),
    }
    # All optional, no group — intermediate masks gated by the
    # intermediate_assemblies BoolParam.requires (see module docstring).
    optional_inputs = {
        "cp_mask": ImageRole(kind="label", dtype="labels",
                             desc="Cell-segmentation labels (single_cell)"),
        "mng_mask": ImageRole(kind="mask", dtype="binary",
                              desc="mNG-filter mask (mNG_filter, percent, v4/v5)"),
        "interaction_mask": ImageRole(kind="mask", dtype="binary",
                                      desc="FLIM interaction mask (FLIM_filter, v4/v5)"),
        "sir_mask": ImageRole(kind="mask", dtype="binary",
                              desc="SiR mask (SiR_subtract / SiR_filter)"),
        "dcp2_mask_2": ImageRole(kind="mask", dtype="binary",
                                 desc="Inner Dcp2 mask (v4/v5 intermediate)"),
        "interaction_mask_2": ImageRole(kind="mask", dtype="binary",
                                        desc="Inner interaction mask (v4/v5)"),
    }

    # ── Parameters ────────────────────────────────────────────────
    parameters = {
        "min_size": IntParam(default=10, min=0,
                             desc="Drop P-body components <= this many px."),
        "mng_bg_mode": ChoiceParam(
            choices=_MNG_BG_CHOICES, default="mean",
            desc="mNG background estimation over the dilute mask; "
            "'manual' uses mng_bg_value."),
        "mng_bg_value": IntParam(
            default=0,
            desc="Manual mNG background (used when mng_bg_mode='manual'; "
            "0 = no subtraction)."),
        "halo_bg_mode": ChoiceParam(
            choices=_HALO_BG_CHOICES, default="median",
            desc="Halo background estimation; mng-nan* use Halo where mNG is "
            "NaN in the dilute mask; 'manual' uses halo_bg_value."),
        "halo_bg_value": IntParam(
            default=0,
            desc="Manual Halo background (used when halo_bg_mode='manual')."),
        "exclude_halo_zero": BoolParam(
            default=True, desc="Exclude 0 from Halo before bg estimation."),
        "exclude_halo_one": BoolParam(
            default=False,
            desc="Exclude 0 and 1 from Halo before bg estimation "
            "(supersedes exclude_halo_zero)."),
        "mNG_filter": ChoiceParam(
            choices=_FILTER_CHOICES, default="none",
            desc="Set mNG outside the mNG-filter mask to zero/NaN "
            "(needs mng_mask)."),
        "FLIM_filter": ChoiceParam(
            choices=_FILTER_CHOICES, default="none",
            desc="Set Halo outside interaction_mask to zero/NaN "
            "(needs interaction_mask)."),
        "SiR_subtract": ChoiceParam(
            choices=_FILTER_CHOICES, default="none",
            desc="Set Halo where SiR_mask>0 to zero/NaN (needs sir_mask)."),
        "SiR_filter": BoolParam(
            default=False, requires=("sir_mask",),
            desc="Restrict Halo measurements to within SiR_mask."),
        "mNG_in_FLIM": BoolParam(
            default=False, requires=("interaction_mask", "mng_mask"),
            desc="NaN both channels outside (interaction_mask & mng_mask)."),
        "percent": BoolParam(
            default=False,
            desc="Add pct_halo_in_mNG_* columns per compartment."),
        "intermediate_assemblies": BoolParam(
            default=False,
            requires=("mng_mask", "interaction_mask", "dcp2_mask_2",
                      "interaction_mask_2"),
            desc="Three-region (P-body/intermediate/dilute) measurement."),
        "intermediate_zero_fill": BoolParam(
            default=False,
            desc="v5 Halo handling for intermediate_assemblies "
            "(zero-fill outside inner masks)."),
        "single_cell": BoolParam(
            default=False, requires=("cp_mask",),
            desc="Aggregate per cell using cp_mask; one row per cell."),
        # Whole-cell mean of BOTH channels per cell (for expression grouping).
        # Deliberately NO requires=("cp_mask",): a True flag without a cp_mask
        # would make run_analysis._check_bool_requires raise and fail the whole
        # dataset (the per-particle cell_mean precedent omits requires too).
        # It is a no-op outside single-cell mode (gated inside the single-cell
        # core branch, reached only with cp_mask); the dialog greys it unless
        # single_cell is on for UX only.
        "channel_cell_mean": BoolParam(
            default=True,
            desc="Add a whole-cell mean of BOTH the mNG and Halo channels per "
            "cell (mNG_cell_mean + Halo_cell_mean), for grouping cells by "
            "expression level. Single-cell mode only (needs single_cell + "
            "cp_mask); on by default."),
    }

    presets: dict[str, dict[str, Any]] = {
        "decapping-sensor-v2": {
            "min_size": 2, "mng_bg_mode": "manual", "mng_bg_value": 0,
            "halo_bg_mode": "manual", "halo_bg_value": 0, "mNG_filter": "NaN",
            "percent": False, "exclude_halo_zero": True,
            "exclude_halo_one": False, "SiR_subtract": "none",
            "SiR_filter": True, "FLIM_filter": "none", "mNG_in_FLIM": False,
            "intermediate_assemblies": False, "intermediate_zero_fill": False,
        },
        "decapping-sensor-v3": {
            "min_size": 2, "mng_bg_mode": "manual", "mng_bg_value": 0,
            "halo_bg_mode": "manual", "halo_bg_value": 0, "mNG_filter": "NaN",
            "percent": True, "exclude_halo_zero": True,
            "exclude_halo_one": False, "SiR_subtract": "none",
            "SiR_filter": False, "FLIM_filter": "zero", "mNG_in_FLIM": False,
            "intermediate_assemblies": False, "intermediate_zero_fill": False,
        },
        "decapping-sensor-v4": {
            "min_size": 2, "mng_bg_mode": "manual", "mng_bg_value": 0,
            "halo_bg_mode": "manual", "halo_bg_value": 0, "mNG_filter": "NaN",
            "percent": True, "exclude_halo_zero": True,
            "exclude_halo_one": False, "SiR_subtract": "none",
            "SiR_filter": False, "FLIM_filter": "zero", "mNG_in_FLIM": False,
            "intermediate_assemblies": True, "intermediate_zero_fill": False,
        },
        "decapping-sensor-v5": {
            "min_size": 2, "mng_bg_mode": "manual", "mng_bg_value": 0,
            "halo_bg_mode": "manual", "halo_bg_value": 0, "mNG_filter": "NaN",
            "percent": True, "exclude_halo_zero": True,
            "exclude_halo_one": False, "SiR_subtract": "none",
            "SiR_filter": False, "FLIM_filter": "zero", "mNG_in_FLIM": False,
            "intermediate_assemblies": True, "intermediate_zero_fill": True,
        },
        # Stress-granule two-region variant. Same param set as v3 (two-region,
        # mNG_filter='NaN', FLIM_filter='zero', percent, NO intermediate
        # assemblies); the SG/mNG region substitution is the user assigning
        # their SG_mask / mNG_mask layers to the condensate_mask / mng_mask
        # roles — no preset-side role mapping. Keeps the generic pbody/dilute
        # output column names. See preset_required_inputs / preset_hidden_inputs
        # below for the v6-specific role gating.
        "decapping-sensor-v6": {
            "min_size": 2, "mng_bg_mode": "manual", "mng_bg_value": 0,
            "halo_bg_mode": "manual", "halo_bg_value": 0, "mNG_filter": "NaN",
            "percent": True, "exclude_halo_zero": True,
            "exclude_halo_one": False, "SiR_subtract": "none",
            "SiR_filter": False, "FLIM_filter": "zero", "mNG_in_FLIM": False,
            "intermediate_assemblies": False, "intermediate_zero_fill": False,
        },
    }

    # ── Preset-aware role gating (U3 capability) ──────────────────
    # v6's mNG_filter='NaN' silently no-ops without mng_mask, and its
    # FLIM_filter='zero' only applies when interaction_mask is supplied
    # (else the Halo filtering never runs and the means are wrong) — so
    # both are required. The v4/v5 intermediate masks and the SiR mask are
    # irrelevant to v6's two-region path, so they are hidden.
    preset_required_inputs = {
        "decapping-sensor-v6": ("mng_mask", "interaction_mask"),
    }
    preset_hidden_inputs = {
        "decapping-sensor-v6": ("dcp2_mask_2", "interaction_mask_2",
                                "sir_mask"),
    }
    # ``single_cell`` (and its dependent ``channel_cell_mean`` expression
    # toggle) are run-mode/output choices orthogonal to the science a preset
    # fixes, so they stay user-editable under any preset (e.g. run v6 in
    # single-cell mode and group cells by expression). The dialog leaves them
    # clickable and the toggled value is overlaid onto the preset by
    # ``resolve_params``.
    preset_editable_params = ("single_cell", "channel_cell_mean")

    # ── Outputs ───────────────────────────────────────────────────
    # One table whose columns vary by mode (two-region / three-region /
    # single-cell / three-region+single-cell). Not named "whole_field" to
    # avoid colliding with the reserved /labels/whole_field segmentation.
    outputs = {
        "whole_field_table": TableOutput(
            desc="Whole-field (or per-cell) compartment measurements.",
        ),
    }

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
        """Validate cross-cutting constraints, normalize params, dispatch."""
        sir_subtract = _none_to_py(params["SiR_subtract"])
        flim_filter = _none_to_py(params["FLIM_filter"])
        mng_filter = _none_to_py(params["mNG_filter"])
        sir_filter = bool(params["SiR_filter"])
        intermediate = bool(params["intermediate_assemblies"])
        zero_fill = bool(params["intermediate_zero_fill"])

        # Constraints the schema can't express (stricter than the CLI's
        # warn-and-abort): raise so the batch runner records a failed item.
        if sir_filter and sir_subtract is not None:
            raise ValueError(
                "SiR_filter cannot be combined with SiR_subtract."
            )
        if zero_fill and not intermediate:
            raise ValueError(
                "intermediate_zero_fill requires intermediate_assemblies."
            )
        if intermediate:
            if sir_subtract is not None or sir_filter:
                raise ValueError(
                    "intermediate_assemblies is incompatible with any SiR "
                    "option."
                )
            if mng_filter is None:
                raise ValueError(
                    "intermediate_assemblies requires mNG_filter to be set."
                )
            if flim_filter is None:
                raise ValueError(
                    "intermediate_assemblies requires FLIM_filter to be set."
                )

        mng_bg = (
            int(params["mng_bg_value"]) if params["mng_bg_mode"] == "manual"
            else params["mng_bg_mode"]
        )
        halo_bg = (
            int(params["halo_bg_value"]) if params["halo_bg_mode"] == "manual"
            else params["halo_bg_mode"]
        )

        result = run_one_image_set(
            pbody_mask=inputs["condensate_mask"],
            dilute_mask=inputs["dilute_mask"],
            halo=inputs["halo"],
            mng=inputs["mng"],
            cp_mask=inputs.get("cp_mask"),
            dcp2_mask=inputs.get("mng_mask"),
            interaction_mask=inputs.get("interaction_mask"),
            sir_mask=inputs.get("sir_mask"),
            dcp2_mask_2=inputs.get("dcp2_mask_2"),
            interaction_mask_2=inputs.get("interaction_mask_2"),
            mng_bg_mode=mng_bg,
            halo_bg_mode=halo_bg,
            min_size=params["min_size"],
            exclude_halo_zero=params["exclude_halo_zero"],
            exclude_halo_one=params["exclude_halo_one"],
            sir_subtract_mode=sir_subtract,
            flim_filter_mode=flim_filter,
            mng_in_flim=bool(params["mNG_in_FLIM"]),
            mng_filter_mode=mng_filter,
            compute_percent=params["percent"],
            single_cell=params["single_cell"],
            sir_filter=sir_filter,
            intermediate_assemblies=intermediate,
            intermediate_zero_fill=zero_fill,
            channel_cell_mean=bool(params["channel_cell_mean"]),
            set_label=set_label,
            log=log,
        )
        return {"whole_field_table": pd.DataFrame(result["rows"])}


def _none_to_py(choice: str) -> str | None:
    """Map the ChoiceParam sentinel ``"none"`` to Python ``None``."""
    return None if choice == "none" else choice
