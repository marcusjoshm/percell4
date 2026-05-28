"""Registered-analyses framework — application layer.

Hosts the global registry, schema validator, and (later) HDF5 loader +
batch runner. Domain-layer types live in
:mod:`percell4.domain.analysis`; concrete registered analyses live
under :mod:`percell4.application.analysis.modules` (added in U5).

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``.
"""

# Importing the concrete-module package fires each module's
# ``@register_analysis`` decorator and populates the registry.
from percell4.application.analysis.modules import (
    per_particle_donut,  # noqa: F401  # registers @register_analysis side effect
)
from percell4.application.analysis.registry import (
    AnalysisInfo,
    get,
    list_analyses,
    register_analysis,
    validate_schema,
)

__all__ = [
    "AnalysisInfo",
    "get",
    "list_analyses",
    "register_analysis",
    "validate_schema",
]
