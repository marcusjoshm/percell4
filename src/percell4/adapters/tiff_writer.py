"""Atomic TIFF writes that preserve spatial-calibration metadata.

The reader-side counterpart is ``src/percell4/adapters/readers.py``
(``_pixel_size_um_from_tags``). Together they round-trip a TIFF's
physical pixel size through PerCell4's HDF5 store.

Writes are atomic via ``tempfile.mkstemp`` + ``os.replace`` per the
project's atomic-write contract — same shape as
``percell4.workflows.artifacts.write_atomic`` (which targets json /
parquet) and ``percell4.store.DatasetStore.create_atomic`` (HDF5). A
partial or empty TIFF is never observable mid-write.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Inverse of ``readers._TIFF_UNIT_TO_UM``: we always emit centimeter
# resolution (ResolutionUnit=3) because every microscope source we
# encounter stores cm and the reader decodes that loss-lessly. INCH (2)
# would round-trip but adds nothing.
_CM_PER_UM = 1.0 / 10000.0


def _resolution_kwargs(
    pixel_size_um: float | None, view_bin: int,
) -> dict[str, Any]:
    """Map ``(pixel_size_um, view_bin)`` to ``tifffile.imwrite`` kwargs.

    Returns an empty dict when ``pixel_size_um`` is None or non-positive,
    matching the reader's defensive behavior (no resolution tags rather
    than wrong ones). When ``view_bin > 1``, the exported file describes
    its own *coarser* sampling:
    ``effective_um_per_px = pixel_size_um * view_bin``.
    """
    if pixel_size_um is None or pixel_size_um <= 0:
        return {}
    bin_factor = view_bin if view_bin and view_bin > 0 else 1
    effective_um_per_px = float(pixel_size_um) * bin_factor
    px_per_cm = 1.0 / (effective_um_per_px * _CM_PER_UM)
    return {
        "resolution": (px_per_cm, px_per_cm),
        "resolutionunit": "CENTIMETER",
    }


def write_tiff_with_metadata(
    path: str | Path,
    data: np.ndarray,
    *,
    pixel_size_um: float | None = None,
    view_bin: int = 1,
) -> None:
    """Write ``data`` to ``path`` as a TIFF, atomically, with calibration.

    Parameters
    ----------
    path:
        Destination file path. Parent directories are created.
    data:
        Array to write. Dtype and shape determine the TIFF layout;
        spatial-calibration tags apply uniformly to channels, label
        layers (``uint32``), and masks (``uint8``).
    pixel_size_um:
        Linear pixel size of the *stored* (creation-bin-scaled) array
        in micrometers. ``None`` skips resolution tags entirely.
    view_bin:
        Read-time bin factor applied upstream. ``1`` emits the stored
        scale; ``>1`` emits ``pixel_size_um × view_bin`` so the output
        file is self-describing.
    """
    import tifffile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    res_kwargs = _resolution_kwargs(pixel_size_um, view_bin)

    fd, tmp_path = tempfile.mkstemp(suffix=".tif.tmp", dir=path.parent)
    os.close(fd)
    try:
        tifffile.imwrite(
            tmp_path,
            data,
            software="PerCell4",
            datetime=datetime.now(timezone.utc),
            **res_kwargs,
        )
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
