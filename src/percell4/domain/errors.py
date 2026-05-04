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


class NoCachedPhasorError(PercellError):
    """No cached phasor data exists for the requested channel.

    Raised by LoadCachedPhasor.execute when /phasor/<channel>/g is
    absent from the dataset. Callers (FlimPanel buttons, PhasorPlot
    auto-load) catch this to fall through to compute or to leave the
    phasor window empty.
    """
