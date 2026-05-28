"""Registered-analyses framework — domain layer.

Pure-domain types and the ``Analysis`` base class. The application-layer
registry (``percell4.application.analysis.registry``) consumes these to
collect, validate, and dispatch registered analyses.

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``
(U1 lands the types and base class; U3 adds the loader and runner;
U5 adds the first concrete analysis).
"""

from percell4.domain.analysis.base import Analysis
from percell4.domain.analysis.types import (
    BoolParam,
    ChoiceParam,
    FloatParam,
    GroupState,
    ImageOutput,
    ImageRole,
    IntParam,
    OutputLike,
    ParamLike,
    TableOutput,
)

__all__ = [
    "Analysis",
    "BoolParam",
    "ChoiceParam",
    "FloatParam",
    "GroupState",
    "ImageOutput",
    "ImageRole",
    "IntParam",
    "OutputLike",
    "ParamLike",
    "TableOutput",
]
