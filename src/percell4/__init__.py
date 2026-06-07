__version__ = "0.1.0"

# Register the Blosc HDF5 filter process-wide as early as possible. New files
# write intensity/labels/masks with Blosc (see store._compression_kwargs); any
# process that reads them — the GUI, CLIs, and spawned parallel-decode workers —
# needs the filter registered first. Importing any percell4 module triggers this,
# so every read path is covered. (Existing gzip files read without it.)
import hdf5plugin  # noqa: E402, F401
