"""File format readers for microscopy data.

Each reader returns a dict with standardized keys:
    'array': numpy array (the image data)
    'metadata': dict (format-specific metadata)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def read_tiff(filepath: str | Path) -> dict[str, Any]:
    """Read a TIFF file.

    Returns dict with:
        'array': ndarray (shape depends on file content)
        'metadata': dict with 'shape', 'dtype', 'pixel_size_um' (if available)
    """
    import tifffile

    img = tifffile.imread(str(filepath))
    metadata: dict[str, Any] = {
        "shape": img.shape,
        "dtype": str(img.dtype),
    }

    # Try to extract pixel size from TIFF metadata
    try:
        with tifffile.TiffFile(str(filepath)) as tif:
            if tif.pages and tif.pages[0].tags.get("XResolution"):
                xres = tif.pages[0].tags["XResolution"].value
                if xres and xres[0] > 0:
                    # Resolution is in pixels-per-unit; invert for unit-per-pixel
                    metadata["pixel_size_um"] = xres[1] / xres[0]
    except Exception:
        pass

    return {"array": img, "metadata": metadata}


def read_tiff_metadata(filepath: str | Path) -> dict[str, Any]:
    """Read only TIFF metadata without loading pixel data."""
    import tifffile

    metadata: dict[str, Any] = {}
    try:
        with tifffile.TiffFile(str(filepath)) as tif:
            page = tif.pages[0]
            metadata["shape"] = page.shape
            metadata["dtype"] = str(page.dtype)
            if page.tags.get("XResolution"):
                xres = page.tags["XResolution"].value
                if xres and xres[0] > 0:
                    metadata["pixel_size_um"] = xres[1] / xres[0]
    except Exception:
        pass
    return metadata


def read_sdt(filepath: str | Path) -> dict[str, Any]:
    """Read a Becker & Hickl .sdt FLIM file.

    Returns dict with:
        'array': ndarray shape (H, W, T) — TCSPC decay histograms
        'intensity': ndarray shape (H, W) — summed intensity
        'metadata': dict with 'frequency_mhz' if available
    """
    import sdtfile

    sdt = sdtfile.SdtFile(str(filepath))
    decay = sdt.data[0]  # shape: (H, W, T)
    intensity = decay.sum(axis=-1)

    metadata: dict[str, Any] = {
        "shape": decay.shape,
        "dtype": str(decay.dtype),
        "n_time_bins": decay.shape[-1],
    }

    try:
        measure = sdt.measure_info[0]
        if hasattr(measure, "laser_rep_rate"):
            metadata["frequency_mhz"] = measure.laser_rep_rate / 1e6
    except (IndexError, AttributeError):
        pass

    return {"array": decay, "intensity": intensity, "metadata": metadata}


def read_flim_bin(
    filepath: str | Path,
    x_dim: int = 512,
    y_dim: int = 512,
    t_dim: int = 132,
    dtype: str = "uint16",
    byte_order: str = "little",
    dim_order: str = "YXT",
    header_bytes: int = 0,
) -> dict[str, Any]:
    """Read pre-aggregated TCSPC histogram data from a raw .bin file.

    The .bin format is unstructured binary — dimensions and dtype must be
    specified by the user. Adapted from leelab/bin_reader.

    Parameters
    ----------
    filepath : path to the .bin file
    x_dim, y_dim, t_dim : spatial and temporal dimensions
    dtype : numpy dtype string ('uint8', 'uint16', 'uint32', 'float32')
    byte_order : 'little' or 'big'
    dim_order : ordering of dimensions in the file, e.g. 'YXT', 'XYT', 'TYX'
    header_bytes : bytes to skip at file start (0 = auto-detect)

    Returns
    -------
    dict with:
        'array': ndarray shape (H, W, T) — canonical ordering
        'intensity': ndarray shape (H, W) — summed over time
        'metadata': dict
    """
    filepath = Path(filepath)

    # Build numpy dtype with byte order
    np_dtype = np.dtype(dtype)
    if byte_order == "big":
        np_dtype = np_dtype.newbyteorder(">")
    else:
        np_dtype = np_dtype.newbyteorder("<")

    expected_elements = x_dim * y_dim * t_dim
    expected_bytes = expected_elements * np_dtype.itemsize

    # Auto-detect header if not specified
    file_size = filepath.stat().st_size
    if header_bytes == 0 and file_size > expected_bytes:
        excess = file_size - expected_bytes
        if excess < 1000:
            header_bytes = excess

    # Read raw binary data — use fromfile for direct read (no double-buffer)
    raw = np.fromfile(str(filepath), dtype=np_dtype, offset=header_bytes)

    if len(raw) != expected_elements:
        raise ValueError(
            f"Expected {expected_elements} elements, got {len(raw)}. "
            f"Check dimensions ({x_dim}x{y_dim}x{t_dim}) and dtype ({dtype})."
        )

    # Reshape according to dim_order
    dim_map = {"X": x_dim, "Y": y_dim, "T": t_dim}
    shape = tuple(dim_map[d] for d in dim_order)
    data = raw.reshape(shape)

    # Transpose to canonical (H, W, T) = (Y, X, T)
    t_ax = dim_order.index("T")
    y_ax = dim_order.index("Y")
    x_ax = dim_order.index("X")
    data = np.transpose(data, (y_ax, x_ax, t_ax))

    intensity = data.sum(axis=-1, dtype=np.int64).astype(np.float32)

    metadata: dict[str, Any] = {
        "shape": data.shape,
        "dtype": str(data.dtype),
        "n_time_bins": t_dim,
        "dim_order_original": dim_order,
        "header_bytes": header_bytes,
    }

    return {"array": data, "intensity": intensity, "metadata": metadata}
