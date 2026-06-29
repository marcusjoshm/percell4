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

from collections.abc import Callable
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

    # ── Preset-aware optional roles ────────────────────────────────
    # A preset may declare optional roles it *requires* and optional
    # roles it *hides*. Keyed by preset name; each value is a tuple of
    # declared role names (in ``required_inputs`` / ``optional_inputs`` /
    # any ``input_groups`` group). Validated at registration time (the
    # preset must exist; each role must be declared). Both surfaces read
    # these: the dialog blocks Start with a reason on a missing required
    # role and hides hidden-role rows; the use-case (``run_analysis``)
    # raises a hard ``ValueError`` when an active preset's required role
    # is absent from the layer map — so headless runs cannot silently
    # produce wrong numbers. Default empty, so existing analyses that
    # declare neither are unaffected.
    preset_required_inputs: ClassVar[dict[str, tuple[str, ...]]] = {}
    preset_hidden_inputs: ClassVar[dict[str, tuple[str, ...]]] = {}

    # ── Outputs ───────────────────────────────────────────────────
    outputs: ClassVar[dict[str, OutputLike]] = {}

    # ── Interface binding (set by GUI layer) ──────────────────────
    # Typed ``type | None`` to avoid pulling Qt into the domain layer.
    # The GUI layer assigns the concrete QDialog subclass at import
    # time (see plan U7); the Scripts tab calls
    # ``cls.dialog_class(parent, ...)`` to open the dialog.
    dialog_class: ClassVar[type | None] = None

    # ── CSV identity column ────────────────────────────────────────
    # Name of the dataset-identity column the batch runner prepends to
    # every ``TableOutput`` CSV (value = the ``.h5`` filename stem).
    # Defaults to ``"dataset"``; an analysis may override it (e.g.
    # ``"group"``) to match an external CSV schema. The runner reads
    # this via ``getattr(cls, "dataset_column_label", "dataset")``.
    dataset_column_label: ClassVar[str] = "dataset"

    def run(
        self,
        inputs: dict[str, NDArray],
        params: dict[str, Any],
        *,
        log: Callable[[str], None] | None = None,
        set_label: str = "",
        layer_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Pure analysis logic.

        Subclasses must override. ``inputs`` is a dict of numpy arrays
        keyed by declared role name (already dtype-coerced by the
        loader); ``params`` is the resolved parameter dict (preset or
        explicit). Returns a dict keyed by declared output name; only
        outputs whose ``produced_when`` predicate evaluated to True are
        expected.

        ``log`` is an optional progress sink (e.g. ``print``). When
        ``None`` (the default) the run is silent; when provided, the
        analysis may stream human-readable progress lines through it.
        ``set_label`` names the dataset/image set being processed and is
        used only in those progress lines. ``layer_map`` is the resolved
        ``{role_name: on_disk_layer_name}`` mapping; the loader keys
        ``inputs`` by role and discards the chosen layer names, so an
        analysis that needs to name outputs after the user-chosen layer
        (e.g. multichannel column headers) reads them from here. All
        three are keyword-only so a subclass that needs none of them can
        simply omit them from its override signature — the runner
        forwards each only when the override declares it.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.run() is not implemented; subclasses "
            "must override Analysis.run()."
        )
