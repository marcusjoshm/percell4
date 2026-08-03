"""Frozen schema types for the registered-analyses framework.

This module is the foundation of the framework introduced in the
analysis-integration plan
(``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``).
Analyses declare their inputs, parameters, and outputs as instances of
these dataclasses at the class level; the application-layer registry
inspects those declarations to validate schemas, drive the loader, and
populate dialogs.

The types deliberately carry no behavior beyond storage: they're
immutable carriers, and bounds-checking (e.g. ``IntParam.min``) is
informational — actual validation happens at runtime in the runner.

Key choices (see plan's "Key Technical Decisions"):

- ``produced_when`` is a Python ``Callable``, not a string DSL. The
  runner invokes it directly with a :class:`GroupState` and the
  resolved params dict. ``None`` means "always produced."
- :class:`GroupState` has no predeclared fields. The runner builds an
  instance per dataset by passing ``<role>_supplied=...`` and
  ``<group>_satisfied=...`` keyword args. Attribute access reads from
  an internal dict; unknown names raise ``AttributeError`` so typos in
  ``produced_when`` callables fail loudly.

No imports from ``qtpy``, ``napari``, ``h5py``, or
``percell4.application.*``. Pure-domain isolation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

# ── Image roles ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageRole:
    """A declared image-layer slot the analysis consumes.

    Attributes
    ----------
    kind:
        ``"intensity"`` (2D intensity image; ``/intensity`` channels OR
        ``/decay/<name>`` with sum-over-bins projection), ``"mask"``
        (binary mask from ``/masks/<name>``), or ``"label"`` (integer
        label image from ``/labels/<name>``). The U3 loader dispatches
        on this field.
    dtype:
        Informational hint for the loader's coercion step:
        ``"float"`` / ``"binary"`` / ``"labels"``.
    desc:
        Human-readable description for dialog labels.
    ndim:
        Tuple of accepted array dimensionalities. Defaults to ``(2,)``;
        future 3D analyses can declare ``(2, 3)`` etc.
    """

    kind: Literal["intensity", "mask", "label"]
    dtype: str
    desc: str = ""
    ndim: tuple[int, ...] = (2,)


# ── Parameters ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntParam:
    """An integer parameter with optional bounds."""

    default: int
    min: int | None = None
    max: int | None = None
    desc: str = ""


@dataclass(frozen=True)
class FloatParam:
    """A floating-point parameter with optional bounds."""

    default: float
    min: float | None = None
    max: float | None = None
    desc: str = ""


@dataclass(frozen=True)
class BoolParam:
    """A boolean parameter.

    ``requires`` lists role names (from ``required_inputs`` /
    ``optional_inputs`` / any ``input_groups`` group) or group names
    that must be supplied/satisfied for the parameter to be enabled.
    The dialog uses this to grey out a checkbox; the runner uses it to
    raise a clear error when the user supplies ``True`` without the
    required layer.
    """

    default: bool
    requires: tuple[str, ...] = ()
    desc: str = ""


@dataclass(frozen=True)
class ChoiceParam:
    """A choice (enumeration) parameter."""

    choices: tuple[str, ...]
    default: str
    desc: str = ""


# Union alias used in ``Analysis.parameters`` declarations.
ParamLike = IntParam | FloatParam | BoolParam | ChoiceParam


# ── Outputs ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TableOutput:
    """A tabular (DataFrame) output the analysis may emit.

    ``produced_when`` is either ``None`` ("always produced") or a
    callable ``(GroupState, dict[str, Any]) -> bool``. The runner
    invokes it after ``run()`` to decide whether the output is
    expected; outputs not in the produced set are not validated, and
    a returned value for a non-produced output is treated as an error.
    """

    produced_when: Callable[[GroupState, dict[str, Any]], bool] | None = None
    desc: str = ""


@dataclass(frozen=True)
class ImageOutput:
    """An image (numpy.ndarray) output the analysis may emit.

    ``dtype`` is ``"binary"`` (binary mask → ``store.write_mask``) or
    ``"labels"`` (integer labels → ``store.write_labels``).
    ``produced_when`` semantics match :class:`TableOutput`.
    """

    dtype: Literal["binary", "labels"]
    produced_when: Callable[[GroupState, dict[str, Any]], bool] | None = None
    desc: str = ""


# Union alias used in ``Analysis.outputs`` declarations.
OutputLike = TableOutput | ImageOutput


# ── Group state ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GroupState:
    """Per-dataset group-and-role satisfaction snapshot.

    Built by the U3 runner from the user-supplied layer map. Has no
    predeclared fields — the runner constructs an instance by passing
    one keyword per role (``<role>_supplied=True/False``) and one per
    declared input group (``<group>_satisfied=True/False``).

    Attribute access reads from an internal ``_flags`` dict; unknown
    attributes raise ``AttributeError`` so typos in ``produced_when``
    callables fail loudly instead of silently returning ``False``.
    """

    _flags: dict[str, bool] = field(default_factory=dict)

    def __init__(self, **flags: bool) -> None:
        # Bypass frozen __setattr__ to populate the single backing dict.
        object.__setattr__(self, "_flags", dict(flags))

    def __getattr__(self, name: str) -> bool:
        # Triggered only when normal attribute lookup fails (i.e. not in
        # __dict__ or the class). The dataclass machinery installs
        # ``_flags`` via __dict__, so this reaches here only for the
        # dynamically-supplied boolean flags.
        if name.startswith("_"):
            raise AttributeError(name)
        flags = object.__getattribute__(self, "_flags")
        if name in flags:
            return flags[name]
        raise AttributeError(
            f"GroupState has no flag {name!r}; declared flags: {sorted(flags)}"
        )

    def as_dict(self) -> dict[str, bool]:
        """Return a copy of the underlying flag map (for tests / logging)."""
        return dict(object.__getattribute__(self, "_flags"))
