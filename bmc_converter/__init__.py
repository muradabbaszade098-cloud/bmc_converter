"""BMC (AGAT diamond-impact) → generic G-code converter."""

__version__ = "0.1.0"

from .bmc_reader import BmcHeader, BmcPoint, open_bmc, iter_points
from .gcode_writer import GcodeOptions, write_gcode

__all__ = [
    "BmcHeader",
    "BmcPoint",
    "open_bmc",
    "iter_points",
    "GcodeOptions",
    "write_gcode",
    "__version__",
]
