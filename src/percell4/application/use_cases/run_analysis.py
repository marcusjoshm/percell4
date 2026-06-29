"""``run_analysis`` — the single Python-API entry point.

Given an analysis name, an ``.h5`` path, a ``layer_map``, and either
``params`` or a ``preset``, this use case:

1. Looks up the analysis class via the registry.
2. Validates the ``layer_map`` against the declared roles + groups.
3. Builds a :class:`GroupState` from the layer map and checks the
   class's ``group_requirement``.
4. Resolves parameters from either ``preset`` or ``params`` (strict —
   mixing both raises), filling defaults from the parameter
   declarations.
5. Checks ``BoolParam.requires`` gating: a ``True``-valued bool param
   whose required role/group is not satisfied raises.
6. Reads the arrays through :mod:`percell4.application.analysis.loader`.
7. Calls the analysis's pure ``run(inputs, params)``.
8. Evaluates each output's ``produced_when`` callable to compute the
   set of expected outputs.
9. Validates the returned dict — keys must be a subset of the produced
   set, and value types must match the declared output kind
   (``TableOutput`` → ``pandas.DataFrame``; ``ImageOutput`` →
   ``numpy.ndarray``). Missing produced outputs are tolerated; the
   batch runner reports the names that were actually returned.

No Qt imports, no I/O beyond the loader. Outputs are returned in-memory
for the caller (batch runner or notebook) to route to CSV/HDF5.

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from percell4.application.analysis import registry
from percell4.application.analysis.loader import load_layers
from percell4.domain.analysis import (
    Analysis,
    BoolParam,
    GroupState,
    ImageOutput,
    ImageRole,
    TableOutput,
)
from percell4.store import DatasetStore


def run_analysis(
    analysis_name: str,
    h5_path: Path,
    layer_map: dict[str, str],
    params: dict[str, Any] | None = None,
    preset: str | None = None,
    *,
    log: Callable[[str], None] | None = None,
    set_label: str | None = None,
) -> dict[str, Any]:
    """Execute a single registered analysis against one ``.h5`` file.

    Parameters
    ----------
    analysis_name:
        Registered name (the string passed to ``@register_analysis``).
    h5_path:
        Path to the ``.h5`` dataset.
    layer_map:
        Mapping from role name to on-disk layer name. Required roles
        must be present; optional roles and any group's roles may be
        absent or supplied as a complete group.
    params:
        Explicit parameter overrides. May not be combined with
        ``preset`` (strict mode — raises).
    preset:
        Name of a preset declared on the analysis class. Resolved
        params come verbatim from ``cls.presets[preset]``, with any
        unspecified parameters filled in from their declared
        ``default``.
    log:
        Optional progress sink (e.g. ``print``) forwarded to the
        analysis's ``run()``. When ``None`` (the default) the run is
        silent. Analyses that emit progress stream human-readable lines
        through it as they run.
    set_label:
        Name used for the dataset in progress lines. Defaults to the
        ``.h5`` file stem when ``None``.

    Returns
    -------
    dict[str, Any]
        Output dict from the analysis's ``run()``, validated against
        the declared output schema.

    Raises
    ------
    KeyError
        Unknown ``analysis_name`` or unknown ``preset``.
    ValueError
        Layer map references an unknown role; required role missing;
        group requirement not satisfied; ``preset`` + non-empty
        ``params`` supplied together; resolved params reference an
        undeclared parameter; ``BoolParam.requires`` is unmet at run
        time; returned dict has an undeclared key or a value with the
        wrong type.
    LayerNotFoundError / LayerDtypeError
        Re-raised from the loader.
    """
    h5_path = Path(h5_path)

    # 1. Registry lookup.
    cls: type[Analysis] = registry.get(analysis_name)

    # 2. Collect declared role names + the role objects.
    roles_dict = _collect_roles(cls)
    all_role_names = set(roles_dict)

    # 3. Validate the layer_map.
    unknown = set(layer_map) - all_role_names
    if unknown:
        # Sorted for stable error messages.
        bad = sorted(unknown)[0]
        raise ValueError(
            f"analysis {analysis_name!r}: layer_map has unknown role "
            f"{bad!r} (declared roles: {sorted(all_role_names)})"
        )

    missing_required = [r for r in cls.required_inputs if r not in layer_map]
    if missing_required:
        raise ValueError(
            f"analysis {analysis_name!r}: required role(s) missing from "
            f"layer_map: {sorted(missing_required)}"
        )

    # 3b. Enforce preset-required roles (the headless safety net). When a
    # preset is active, every role it declares in ``preset_required_inputs``
    # must be present in the layer_map — otherwise a filter that depends on
    # that role (e.g. v6's ``mNG_filter`` / ``FLIM_filter``) would silently
    # no-op and produce scientifically-wrong numbers with no error. The
    # dialog blocks Start on the same condition; this guards the headless
    # path (notebook / batch_run_analysis / a future CLI), which otherwise
    # validates only required_inputs / groups / BoolParam.requires.
    if preset is not None:
        for role in getattr(cls, "preset_required_inputs", {}).get(preset, ()):
            if role not in layer_map:
                raise ValueError(
                    f"analysis {analysis_name!r}: preset {preset!r} requires "
                    f"role {role!r} to be supplied (layer_map keys="
                    f"{sorted(layer_map)})"
                )

    # 4. Build GroupState.
    role_flags = {f"{r}_supplied": (r in layer_map) for r in all_role_names}
    group_satisfaction = {
        g: all(role in layer_map for role in g_roles)
        for g, g_roles in cls.input_groups.items()
    }
    group_flags = {f"{g}_satisfied": ok for g, ok in group_satisfaction.items()}
    group_state = GroupState(**role_flags, **group_flags)

    # 5. Check group_requirement.
    _check_group_requirement(
        analysis_name, cls.group_requirement, group_satisfaction
    )

    # 6. Resolve params (strict — no overrides on top of a preset).
    resolved = resolve_params(analysis_name, cls, params, preset)

    # 7. Check BoolParam.requires gating.
    _check_bool_requires(
        analysis_name, cls, resolved, layer_map, group_satisfaction
    )

    # 8-9. Load arrays + run. Pass the optional progress kwargs only when this
    # analysis's ``run()`` actually accepts them — the base contract lets a
    # subclass that emits no progress keep the plain ``(inputs, params)``
    # signature.
    #
    # On a time-lapse dataset the framework loops timepoints: each run receives
    # 2D inputs (``load_layers(timepoint=t)``) and the per-frame outputs are
    # aggregated — TableOutputs concatenated with a ``timepoint`` column,
    # ImageOutputs stacked on a leading T axis -> ``(T, ...)``. This multi-t
    # return shape is a contract the batch runner / tests depend on. The
    # single-timepoint path is byte-identical (one run, no timepoint column).
    run_callable = cls().run
    run_kwargs = _accepted_progress_kwargs(
        run_callable,
        log=log,
        set_label=h5_path.stem if set_label is None else set_label,
        layer_map=layer_map,
    )

    _meta = DatasetStore(h5_path).metadata
    n_timepoints = int(_meta.get("n_timepoints", 1) or 1)
    pixel_size_um = _meta.get("pixel_size_um")
    if n_timepoints <= 1:
        arrays = load_layers(h5_path, layer_map, roles_dict)
        outputs = run_callable(arrays, resolved, **run_kwargs)
    else:
        per_t: list[tuple[int, dict[str, Any]]] = []
        for t in range(n_timepoints):
            arrays_t = load_layers(h5_path, layer_map, roles_dict, timepoint=t)
            per_t.append((t, run_callable(arrays_t, resolved, **run_kwargs)))
        outputs = _aggregate_timepoints(per_t, cls)

    # 10. Compute the produced set.
    produced = {
        out_name
        for out_name, decl in cls.outputs.items()
        if decl.produced_when is None or decl.produced_when(group_state, resolved)
    }

    # 11. Validate returned dict keys ⊆ produced.
    extra = set(outputs) - produced
    if extra:
        bad = sorted(extra)[0]
        raise ValueError(
            f"analysis {analysis_name!r} returned undeclared output "
            f"{bad!r} (declared+produced: {sorted(produced)})"
        )

    # 12. Validate types per declared output kind.
    for name, value in outputs.items():
        decl = cls.outputs[name]
        if isinstance(decl, TableOutput):
            if not isinstance(value, pd.DataFrame):
                raise ValueError(
                    f"analysis {analysis_name!r}: output {name!r} declared "
                    f"TableOutput must be a pandas.DataFrame, got "
                    f"{type(value).__name__}"
                )
        elif isinstance(decl, ImageOutput):
            if not isinstance(value, np.ndarray):
                raise ValueError(
                    f"analysis {analysis_name!r}: output {name!r} declared "
                    f"ImageOutput must be a numpy.ndarray, got "
                    f"{type(value).__name__}"
                )
        else:  # pragma: no cover — exhaustive per OutputLike union
            raise ValueError(
                f"analysis {analysis_name!r}: output {name!r} has unknown "
                f"declaration type {type(decl).__name__}"
            )

    # 13. Add a µm² area sibling next to every ``*_area_px`` column, scaled by
    # the dataset's pixel size. A no-op when the dataset carries no
    # ``pixel_size_um`` (e.g. an import without resolution tags) — the px
    # columns are left untouched, so output is byte-identical for uncalibrated
    # datasets.
    for name, value in outputs.items():
        if isinstance(cls.outputs[name], TableOutput):
            outputs[name] = _add_area_um2_columns(value, pixel_size_um)

    return outputs


# ── Helpers ───────────────────────────────────────────────────────────


def _add_area_um2_columns(
    df: pd.DataFrame, pixel_size_um: float | None
) -> pd.DataFrame:
    """Return ``df`` with a ``<base>_area_um2`` sibling immediately after each
    ``<base>_area_px`` column, scaled by ``pixel_size_um ** 2``.

    A no-op (returns ``df`` unchanged) when ``pixel_size_um`` is missing,
    non-numeric, or non-positive, or when there are no ``*_area_px`` columns —
    so a single uncalibrated dataset's output is byte-identical. Idempotent: a
    ``_area_um2`` column that already exists is left as-is.

    Note: in a *combined* CSV spanning a batch where some datasets are
    calibrated and some are not, pandas unions columns by name, so the
    ``_area_um2`` column appears with ``NaN`` for the uncalibrated rows — the
    correct "µm² unknown" semantic, not a defect.
    """
    try:
        px = float(pixel_size_um)  # pixel_size_um comes from external metadata
    except (TypeError, ValueError):
        return df
    if not (px > 0):
        return df
    area_cols = [c for c in df.columns if c.endswith("_area_px")]
    if not area_cols:
        return df
    factor = px * px
    out = df.copy()
    for col in area_cols:
        sibling = f"{col[: -len('_area_px')]}_area_um2"
        if sibling in out.columns:
            continue
        # Re-fetch the position each iteration — prior inserts shift columns.
        insert_at = out.columns.get_loc(col) + 1
        out.insert(insert_at, sibling, out[col].astype(float) * factor)
    return out


def _aggregate_timepoints(
    per_t: list[tuple[int, dict[str, Any]]],
    cls: type[Analysis],
) -> dict[str, Any]:
    """Aggregate per-timepoint analysis outputs into a single output dict.

    ``TableOutput``s are concatenated with a ``timepoint`` column;
    ``ImageOutput``s are stacked on a new leading T axis -> ``(T, ...)``.
    Output names are the union across frames, so a ``produced_when`` output
    that is absent on some frames is tolerated. This is genuinely new shape
    (one analysis run per frame, then aggregate), not a wrap of one run.
    """
    names: set[str] = set()
    for _t, out in per_t:
        names.update(out)

    aggregated: dict[str, Any] = {}
    for name in names:
        decl = cls.outputs[name]
        if isinstance(decl, TableOutput):
            frames: list[pd.DataFrame] = []
            for t, out in per_t:
                if name in out:
                    df = out[name].copy()
                    df["timepoint"] = t
                    frames.append(df)
            aggregated[name] = (
                pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            )
        elif isinstance(decl, ImageOutput):
            # Exact-T contract: one plane per frame, never fewer. A frame that
            # omitted this ImageOutput contributes an all-zero plane (shaped
            # like the frames that did emit it) so the stacked result keeps
            # exactly n_timepoints planes — a short stack would later raise
            # LayerSizeMismatchError at write. (Tables, above, intentionally
            # tolerate absent frames; images must stay an exact-T stack.) In
            # practice the per-particle donut producer already emits an all-zero
            # plane on a no-particle frame, so the key is present every frame;
            # this guards against a producer ever omitting it.
            template = next(out[name] for _t, out in per_t if name in out)
            planes = [
                out[name] if name in out else np.zeros_like(template)
                for _t, out in per_t
            ]
            aggregated[name] = np.stack(planes, axis=0)
    return aggregated


def _accepted_progress_kwargs(
    run_callable: Callable[..., Any],
    *,
    log: Callable[[str], None] | None,
    set_label: str,
    layer_map: dict[str, str],
) -> dict[str, Any]:
    """Return the subset of ``{log, set_label, layer_map}`` ``run_callable`` accepts.

    The :class:`Analysis` base declares ``log`` / ``set_label`` /
    ``layer_map`` as keyword-only parameters, but documents that a
    subclass that needs none of them may omit them. Passing them
    unconditionally would raise ``TypeError`` against such a ``run()``;
    so we inspect the signature and forward each only when it is named
    explicitly or the callee declares ``**kwargs``.
    """
    try:
        params = inspect.signature(run_callable).parameters
    except (TypeError, ValueError):
        # Builtins / C callables without a signature — assume the strict
        # 2-arg contract and forward nothing.
        return {}

    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    kwargs: dict[str, Any] = {}
    if accepts_var_kw or "log" in params:
        kwargs["log"] = log
    if accepts_var_kw or "set_label" in params:
        kwargs["set_label"] = set_label
    if accepts_var_kw or "layer_map" in params:
        kwargs["layer_map"] = layer_map
    return kwargs


def _collect_roles(cls: type[Analysis]) -> dict[str, ImageRole]:
    """Flatten required + optional + grouped roles into one dict.

    The schema validator already guarantees no name collisions across
    these three buckets; we rely on that and concatenate.
    """
    roles: dict[str, ImageRole] = {}
    roles.update(cls.required_inputs)
    roles.update(cls.optional_inputs)
    for group_roles in cls.input_groups.values():
        roles.update(group_roles)
    return roles


def _check_group_requirement(
    analysis_name: str,
    requirement: str,
    group_satisfaction: dict[str, bool],
) -> None:
    """Validate ``cls.group_requirement`` against the satisfaction map."""
    if not group_satisfaction:
        # No groups declared → requirement is trivially met for every
        # value (``all`` of an empty set is True; ``at_least_one`` is
        # accepted as "no groups means no constraint"; ``exactly_one``
        # likewise).
        return

    n_sat = sum(group_satisfaction.values())
    if requirement == "all":
        if not all(group_satisfaction.values()):
            missing = sorted(g for g, ok in group_satisfaction.items() if not ok)
            raise ValueError(
                f"analysis {analysis_name!r}: group_requirement='all' but "
                f"groups {missing} are not satisfied "
                f"(satisfaction={group_satisfaction})"
            )
    elif requirement == "at_least_one":
        if n_sat == 0:
            raise ValueError(
                f"analysis {analysis_name!r}: group_requirement="
                f"'at_least_one' but no groups are satisfied "
                f"(satisfaction={group_satisfaction})"
            )
    elif requirement == "exactly_one":
        if n_sat != 1:
            raise ValueError(
                f"analysis {analysis_name!r}: group_requirement="
                f"'exactly_one' but {n_sat} group(s) satisfied "
                f"(satisfaction={group_satisfaction})"
            )
    else:  # pragma: no cover — schema validator catches this
        raise ValueError(
            f"analysis {analysis_name!r}: unknown group_requirement "
            f"{requirement!r}"
        )


def resolve_params(
    analysis_name: str,
    cls: type[Analysis],
    params: dict[str, Any] | None,
    preset: str | None,
) -> dict[str, Any]:
    """Resolve the runtime parameter dict.

    Strict policy: ``preset`` and a non-empty ``params`` cannot be
    combined. Empty ``params`` is treated as absent (caller passing
    ``{}`` with a preset is fine).
    """
    if preset is not None and params:
        raise ValueError(
            f"analysis {analysis_name!r}: cannot mix preset and explicit "
            f"params (preset={preset!r}, params keys={sorted(params)})"
        )

    if preset is not None:
        if preset not in cls.presets:
            raise KeyError(
                f"analysis {analysis_name!r}: unknown preset {preset!r} "
                f"(declared: {sorted(cls.presets)})"
            )
        resolved = dict(cls.presets[preset])
    else:
        resolved = dict(params or {})

    # Reject keys that aren't declared parameters.
    unknown = set(resolved) - set(cls.parameters)
    if unknown:
        bad = sorted(unknown)[0]
        raise ValueError(
            f"analysis {analysis_name!r}: params has unknown key {bad!r} "
            f"(declared parameters: {sorted(cls.parameters)})"
        )

    # Fill defaults.
    for name, decl in cls.parameters.items():
        if name not in resolved:
            resolved[name] = decl.default

    return resolved


def _check_bool_requires(
    analysis_name: str,
    cls: type[Analysis],
    resolved: dict[str, Any],
    layer_map: dict[str, str],
    group_satisfaction: dict[str, bool],
) -> None:
    """Enforce ``BoolParam.requires`` at run time.

    Only fires when the param is ``True``. A param value of ``False``
    (or its default ``False``) does not trigger the requires check —
    the requires field gates *enabling* the option, not its disabled
    default.
    """
    for pname, decl in cls.parameters.items():
        if not isinstance(decl, BoolParam):
            continue
        if not decl.requires:
            continue
        if not resolved.get(pname, False):
            continue
        for req in decl.requires:
            # ``req`` is either a role name (must appear in layer_map)
            # or a group name (its group_satisfaction entry must be
            # True). Schema validation guarantees one of those is the
            # case.
            ok = (req in layer_map) or group_satisfaction.get(req, False)
            if not ok:
                raise ValueError(
                    f"analysis {analysis_name!r}: BoolParam {pname!r}=True "
                    f"requires {req!r}, but it is not supplied "
                    f"(layer_map keys={sorted(layer_map)}, "
                    f"group_satisfaction={group_satisfaction})"
                )
