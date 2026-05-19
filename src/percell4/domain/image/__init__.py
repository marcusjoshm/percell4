"""Image-processing helpers (Qt-free domain utilities).

Currently exposes:
- ``nan_safe_gaussian_filter``: normalized-convolution Gaussian filter
  that does not poison neighbors of NaN pixels.
"""

from percell4.domain.image.gaussian import nan_safe_gaussian_filter

__all__ = ["nan_safe_gaussian_filter"]
