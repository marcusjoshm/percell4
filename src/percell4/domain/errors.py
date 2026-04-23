"""Domain exception hierarchy for PerCell4.

Use these instead of bare ValueError in use cases so callers can
catch specific failure modes without string matching.
"""


class PercellError(Exception):
    """Base exception for all PerCell4 domain errors."""


class NoDatasetError(PercellError):
    """No dataset is currently loaded."""


class NoSegmentationError(PercellError):
    """No active segmentation layer is set."""


class NoMaskError(PercellError):
    """No active mask layer is set."""


class NoChannelError(PercellError):
    """No active channel is set."""


class MissingOptionalDependencyError(PercellError):
    """An optional dependency (declared under a pip extra) is not installed."""
