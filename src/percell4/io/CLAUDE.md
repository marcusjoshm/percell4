# src/percell4/io/

TIFF / microscopy file I/O and the TIFF → HDF5 import pipeline.

## Modules

- `models.py` — dataclasses shared across the pipeline: `DiscoveredFile`,
  `ScanResult`, `TokenConfig`, `TileConfig`, `DatasetSpec`.
- `scanner.py` — `FileScanner`. Walks a directory, identifies TIFFs, and
  parses filename tokens (channel, timepoint, z-slice, tile) using regex
  patterns defined in `TokenConfig`.
- `discovery.py` — higher-level dataset discovery for batch compress.
  Groups files into `DatasetSpec` objects by either subdirectory or
  filename token, returning one spec per output `.h5`.
- `readers.py` — thin wrappers around `tifffile` and `sdtfile`. Returns
  raw numpy arrays.
- `assembler.py` — pure-numpy tile stitching, channel assembly, and
  Z-projection (MIP / mean / sum). No HDF5 or GUI coupling.
- `importer.py` — `import_dataset()`. Orchestrates scan → assemble →
  `DatasetStore.write_*` → `ProjectIndex.add_dataset`. Writes HDF5 first,
  then updates `project.csv` (orphan `.h5` is harmless; orphan CSV rows are
  confusing). Accepts optional `files=` override for per-dataset file
  lists from batch compress.
