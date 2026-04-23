"""Synthetic FLIM data generators for tests and benchmarks."""

from percell4.domain.flim.synthetic.spoke_phantom import (
    SpokePhantom,
    generate_spoke_tcspc,
)

__all__ = ["SpokePhantom", "generate_spoke_tcspc"]
