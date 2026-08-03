"""Concrete registered :class:`Analysis` modules.

Each module under this package declares one or more analyses via
``@register_analysis``. Importing this package's submodules is the
side-effect that populates ``percell4.application.analysis.registry``.

The submodules are imported from
``percell4.application.analysis.__init__`` so that any consumer of the
parent package sees a populated registry without having to know which
modules exist.

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``,
unit U5.
"""
