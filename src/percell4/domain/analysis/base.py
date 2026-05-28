"""The :class:`Analysis` base class.

Subclasses declare their inputs / parameters / outputs as class-level
attributes — see :mod:`percell4.domain.analysis.types` for the
declaration types. The application-layer registry inspects these
attributes at registration time (see
``percell4.application.analysis.registry``).

``dialog_class`` is intentionally typed as ``type | None`` rather than
``type[QDialog] | None`` to keep the domain layer Qt-free. The GUI
layer assigns it externally (see plan U7).
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from numpy.typing import NDArray

from percell4.domain.analysis.types import (
    ImageRole,
    OutputLike,
    ParamLike,
)


class Analysis:
    """Declarative base class for a registered analysis.

    Subclasses override the class-level schema attributes and provide a
    pure ``run(inputs, params) -> outputs`` method. The framework — not
    the analysis — handles I/O, batching, and dialog wiring.

    The base class itself raises :class:`NotImplementedError` from
    :meth:`run`, so a subclass that forgets to override it fails loudly
    at the first invocation rather than silently returning ``None``.
    """

    # ── Identity ──────────────────────────────────────────────────
    name: ClassVar[str]
    display_name: ClassVar[str]
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""

    # ── Inputs ────────────────────────────────────────────────────
    required_inputs: ClassVar[dict[str, ImageRole]] = {}
    # Each group maps a group name to a {role_name: ImageRole} dict.
    # ``group_requirement`` controls how many groups must be supplied
    # at runtime (e.g. P-body and SG groups with "at_least_one").
    input_groups: ClassVar[dict[str, dict[str, ImageRole]]] = {}
    group_requirement: ClassVar[Literal["at_least_one", "exactly_one", "all"]] = "all"
    optional_inputs: ClassVar[dict[str, ImageRole]] = {}

    # ── Parameters & presets ──────────────────────────────────────
    parameters: ClassVar[dict[str, ParamLike]] = {}
    # Each preset is a {param_name: value} dict; preset names are
    # what the dialog shows in the Preset combo. Param-name keys are
    # validated against ``parameters`` at registration time.
    presets: ClassVar[dict[str, dict[str, Any]]] = {}

    # ── Outputs ───────────────────────────────────────────────────
    outputs: ClassVar[dict[str, OutputLike]] = {}

    # ── Interface binding (set by GUI layer) ──────────────────────
    # Typed ``type | None`` to avoid pulling Qt into the domain layer.
    # The GUI layer assigns the concrete QDialog subclass at import
    # time (see plan U7); the Scripts tab calls
    # ``cls.dialog_class(parent, ...)`` to open the dialog.
    dialog_class: ClassVar[type | None] = None

    def run(
        self,
        inputs: dict[str, NDArray],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Pure analysis logic.

        Subclasses must override. ``inputs`` is a dict of numpy arrays
        keyed by declared role name (already dtype-coerced by the
        loader); ``params`` is the resolved parameter dict (preset or
        explicit). Returns a dict keyed by declared output name; only
        outputs whose ``produced_when`` predicate evaluated to True are
        expected.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.run() is not implemented; subclasses "
            "must override Analysis.run()."
        )
