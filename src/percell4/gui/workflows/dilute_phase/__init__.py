"""Dilute-phase mask generation workflow (interactive single-dataset).

The user iteratively runs Grouped Threshold against a NaN-subtracted
working copy of the active channel, accepting each round's condensed
mask through the existing :class:`ThresholdQCController` in no-persist
mode. On Done the workflow writes ONE final binary mask
``(in_cell) AND NOT cumulative_dilated_condensed`` to ``/masks/<name>``.
No intermediate state persists to the dataset's ``.h5``.

Public surface:

* :class:`DilutePhaseMaskController` — owns the round state machine
  (working buffer, cumulative-condensed union, locked config, round
  counter); drives the per-round Worker + QC pipeline.
"""

from percell4.gui.workflows.dilute_phase.controller import (
    DilutePhaseMaskController,
)

__all__ = ["DilutePhaseMaskController"]
