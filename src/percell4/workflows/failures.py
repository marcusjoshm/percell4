"""Per-dataset failure taxonomy for batch workflows.

Failures are first-class: a misbehaving dataset never crashes the run. Each
failure gets a typed code plus a human-readable message and is recorded on
``RunMetadata.failures`` for the final ``run_config.json`` summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DatasetFailure(StrEnum):
    """Enumerated per-dataset failure modes."""

    COMPRESS_FAILED = "compress_failed"
    SEGMENTATION_EMPTY = "segmentation_empty"
    SEGMENTATION_ERROR = "segmentation_error"
    THRESHOLD_EMPTY = "threshold_empty"
    THRESHOLD_ERROR = "threshold_error"
    MEASUREMENT_ERROR = "measurement_error"


@dataclass(kw_only=True, slots=True)
class FailureRecord:
    """One per-dataset failure."""

    dataset_name: str
    phase_name: str
    failure: DatasetFailure
    message: str
    ts: datetime
