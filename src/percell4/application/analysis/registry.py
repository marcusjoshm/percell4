"""Global registry of :class:`Analysis` subclasses + schema validation.

Analyses register via the :func:`register_analysis` decorator. The
registry validates each schema (parameter coverage in presets, role
collisions, valid ``group_requirement``, output-name uniqueness,
``BoolParam.requires`` resolves to known roles/groups) BEFORE inserting
into ``_REGISTRY`` — bad schemas fail at module-import time, with the
offending role / param / group name in the exception message.

The :func:`list_analyses` helper returns lightweight
:class:`AnalysisInfo` records used by the Scripts tab (U8) to populate
entries without instantiating any heavy GUI classes.

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``,
unit U1.
"""

from __future__ import annotations

from dataclasses import dataclass

from percell4.domain.analysis import (
    Analysis,
    BoolParam,
    ImageRole,
)

# Module-level registry. Cleared between tests via a fixture; in
# production it's populated once at import time (U5 lands the first
# entry).
_REGISTRY: dict[str, type[Analysis]] = {}


_VALID_GROUP_REQS = ("at_least_one", "exactly_one", "all")


# ── Public dataclass ──────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalysisInfo:
    """A one-line summary record used by the Scripts tab."""

    name: str
    display_name: str
    description: str
    input_summary: str
    param_summary: str


# ── Public API ────────────────────────────────────────────────────────


def register_analysis(name: str):
    """Decorator: validate and register an :class:`Analysis` subclass.

    Raises :class:`ValueError` if the schema is malformed or if
    ``name`` is already registered.
    """

    def _decorate(cls: type[Analysis]) -> type[Analysis]:
        if name in _REGISTRY:
            raise ValueError(f"Analysis {name!r} already registered")
        validate_schema(cls)
        _REGISTRY[name] = cls
        return cls

    return _decorate


def get(name: str) -> type[Analysis]:
    """Return the registered analysis class.

    Raises :class:`KeyError` if ``name`` is not registered.
    """
    if name not in _REGISTRY:
        raise KeyError(f"Analysis {name!r} is not registered")
    return _REGISTRY[name]


def list_analyses() -> list[AnalysisInfo]:
    """Return one :class:`AnalysisInfo` per registered analysis.

    Stable insertion order (Python 3.7+ dicts preserve it).
    """
    return [_make_info(name, cls) for name, cls in _REGISTRY.items()]


# ── Schema validation ─────────────────────────────────────────────────


def validate_schema(cls: type[Analysis]) -> None:
    """Validate an :class:`Analysis` subclass's declarations.

    Raises :class:`ValueError` with the offending name in the message
    on any violation. Called by :func:`register_analysis`; safe to
    invoke directly from a test.
    """
    role_names = _collect_role_names(cls)

    # ── group_requirement ───────────────────────────────────────
    if cls.group_requirement not in _VALID_GROUP_REQS:
        raise ValueError(
            f"Analysis {cls.__name__!r}: invalid group_requirement "
            f"{cls.group_requirement!r}; must be one of {_VALID_GROUP_REQS}"
        )

    # ── preset keys ⊆ parameters ────────────────────────────────
    declared_params = set(cls.parameters)
    for preset_name, preset in cls.presets.items():
        for key in preset:
            if key not in declared_params:
                raise ValueError(
                    f"Analysis {cls.__name__!r}: preset {preset_name!r} "
                    f"references unknown parameter {key!r}"
                )

    # ── preset_required_inputs / preset_hidden_inputs ───────────
    # A preset may declare optional roles it requires / hides. Each
    # mapping is {preset_name: (role, ...)}; the preset must exist in
    # ``presets`` and every role must be a declared input. Enforced in
    # both the dialog (UX) and ``run_analysis`` (headless safety net).
    for field_name in ("preset_required_inputs", "preset_hidden_inputs"):
        mapping = getattr(cls, field_name, {})
        for preset_name, roles in mapping.items():
            if preset_name not in cls.presets:
                raise ValueError(
                    f"Analysis {cls.__name__!r}: {field_name} references "
                    f"unknown preset {preset_name!r} (declared presets: "
                    f"{sorted(cls.presets)})"
                )
            for role in roles:
                if role not in role_names:
                    raise ValueError(
                        f"Analysis {cls.__name__!r}: {field_name}"
                        f"[{preset_name!r}] references {role!r} which is not "
                        f"a known role (declared roles: {sorted(role_names)})"
                    )
    # A role declared both required and hidden by the same preset is a
    # contradiction: the dialog would hide its row (no way to assign it) yet
    # block Start on its absence, and headless ``run_analysis`` would always
    # raise. Fail loud at registration rather than ship a soft-locked preset.
    required_map = getattr(cls, "preset_required_inputs", {})
    hidden_map = getattr(cls, "preset_hidden_inputs", {})
    for preset_name in set(required_map) & set(hidden_map):
        clash = set(required_map[preset_name]) & set(hidden_map[preset_name])
        if clash:
            raise ValueError(
                f"Analysis {cls.__name__!r}: preset {preset_name!r} declares "
                f"role(s) {sorted(clash)} as both required and hidden"
            )

    # ── output-name uniqueness ──────────────────────────────────
    output_names = list(cls.outputs)
    if len(set(output_names)) != len(output_names):
        raise ValueError(
            f"Analysis {cls.__name__!r}: duplicate output names in "
            f"declared outputs: {output_names}"
        )

    # ── BoolParam.requires resolves to a known role or group ────
    group_names = set(cls.input_groups)
    known_targets = role_names | group_names
    for pname, param in cls.parameters.items():
        if isinstance(param, BoolParam):
            for req in param.requires:
                if req not in known_targets:
                    raise ValueError(
                        f"Analysis {cls.__name__!r}: BoolParam "
                        f"{pname!r} requires {req!r} which is not a "
                        "known role or group name"
                    )


def _collect_role_names(cls: type[Analysis]) -> set[str]:
    """Return the union of all role names, raising on collisions.

    Collisions detected:

    - a key in ``required_inputs`` also appears in any
      ``input_groups[<g>]``
    - the same key appears in two different ``input_groups[<g>]``

    ``optional_inputs`` is folded in without collision checks
    against ``required_inputs`` (typed as "optional in addition to
    required" — but if they DO collide the run-time loader would
    behave weirdly, so still check that case as well).
    """
    names: set[str] = set()

    # required_inputs
    for r in cls.required_inputs:
        if r in names:
            raise ValueError(
                f"Analysis {cls.__name__!r}: role name {r!r} appears more than once"
            )
        names.add(r)

    # optional_inputs (collision with required_inputs is an error)
    for r in cls.optional_inputs:
        if r in names:
            raise ValueError(
                f"Analysis {cls.__name__!r}: role name {r!r} appears in both "
                "required_inputs and optional_inputs"
            )
        names.add(r)

    # input_groups: collide with required_inputs OR with another group
    seen_in_groups: dict[str, str] = {}  # role_name → group_name where first seen
    for group_name, group_roles in cls.input_groups.items():
        if not isinstance(group_roles, dict):
            raise ValueError(
                f"Analysis {cls.__name__!r}: input_groups[{group_name!r}] "
                "must be a dict of role_name -> ImageRole"
            )
        for r, role in group_roles.items():
            if not isinstance(role, ImageRole):
                raise ValueError(
                    f"Analysis {cls.__name__!r}: input_groups[{group_name!r}]"
                    f"[{r!r}] must be an ImageRole, got {type(role).__name__}"
                )
            if r in cls.required_inputs:
                raise ValueError(
                    f"Analysis {cls.__name__!r}: role name {r!r} appears in "
                    f"both required_inputs and input_groups[{group_name!r}]"
                )
            if r in cls.optional_inputs:
                raise ValueError(
                    f"Analysis {cls.__name__!r}: role name {r!r} appears in "
                    f"both optional_inputs and input_groups[{group_name!r}]"
                )
            if r in seen_in_groups:
                raise ValueError(
                    f"Analysis {cls.__name__!r}: role name {r!r} appears in "
                    f"input_groups[{seen_in_groups[r]!r}] and "
                    f"input_groups[{group_name!r}]"
                )
            seen_in_groups[r] = group_name
            names.add(r)

    return names


# ── Summaries (used by Scripts tab) ───────────────────────────────────


def _make_info(name: str, cls: type[Analysis]) -> AnalysisInfo:
    return AnalysisInfo(
        name=name,
        display_name=cls.display_name,
        description=cls.description,
        input_summary=_input_summary(cls),
        param_summary=_param_summary(cls),
    )


def _input_summary(cls: type[Analysis]) -> str:
    parts: list[str] = []
    if cls.required_inputs:
        parts.append("required: " + ", ".join(cls.required_inputs))
    for gname, group in cls.input_groups.items():
        parts.append(f"group {gname}: " + ", ".join(group))
    if cls.optional_inputs:
        parts.append("optional: " + ", ".join(cls.optional_inputs))
    return "; ".join(parts) if parts else "(no inputs)"


def _param_summary(cls: type[Analysis]) -> str:
    if not cls.parameters:
        return "(no parameters)"
    return ", ".join(cls.parameters)
