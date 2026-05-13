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


class CalibrationCSVError(PercellError):
    """The FLIM calibration CSV failed to parse or validate.

    Carries a list of human-readable error messages — one per failing
    row or global check — so the caller can show every problem at once
    instead of bailing on the first. ``str(error)`` joins them with
    semicolons; ``error.errors`` is the original tuple.
    """

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors: tuple[str, ...] = tuple(errors)
        super().__init__("; ".join(self.errors))
