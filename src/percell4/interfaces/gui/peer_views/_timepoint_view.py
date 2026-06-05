"""Pure (Qt-free) per-timepoint slicing for peer views (U16/U17).

The cell table and data plot show measurements for the **active timepoint** on a
time-lapse dataset (slider-follows-frame, contract §F / D8). Single-timepoint
measurements have no ``timepoint`` column, so the whole df is returned
byte-identically. Extracted so the slice is unit-testable without a Qt window.
"""

from __future__ import annotations

import pandas as pd


def active_timepoint_view(df: pd.DataFrame, active_timepoint: int) -> pd.DataFrame:
    """Return the rows for ``active_timepoint`` when ``df`` is timepoint-bearing.

    When ``df`` has a ``timepoint`` column, return only that timepoint's rows
    (index reset so the peer view's positional ``label``→row maps stay clean);
    otherwise return ``df`` unchanged (the single-timepoint path).
    """
    if "timepoint" in df.columns:
        return df[df["timepoint"] == active_timepoint].reset_index(drop=True)
    return df
