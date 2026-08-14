<p align="center">
  <img src="art/percell4_logo.png" width="180" alt="PerCell4 logo">
</p>

# PerCell4

[![CI](https://github.com/marcusjoshm/percell4/actions/workflows/ci.yml/badge.svg)](https://github.com/marcusjoshm/percell4/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)](docs/installation.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

PerCell4 is a desktop application for single-cell analysis of fluorescence microscopy
images, including fluorescence lifetime (FLIM) data. It combines Cellpose segmentation,
per-cell thresholding and puncta detection, per-cell measurement, and phasor analysis in
one workflow, and stores each experiment as a single HDF5 file. It is written in Python
and built on Qt, napari, scikit-image and h5py.

Every batch operation is also available as a headless command-line tool.

> PerCell4 is under active development and currently pre-release (0.1.0). Interfaces and
> file layouts may still change between versions.

<p align="center">
  <img src="docs/screenshots/_placeholder.png" width="820" alt="The PerCell4 viewer showing a cell segmentation overlay alongside the per-cell data table">
</p>

## Documentation

| | |
|---|---|
| [Installation](docs/installation.md) | Per-OS setup, optional extras, standalone bundles, troubleshooting |
| [Workflow protocol](docs/workflow-protocol.md) | Step-by-step guide to the single-cell analysis workflow |
| [Command-line tools](docs/cli.md) | All command-line tools and their options |
| [Architecture](docs/architecture.md) | Code layout, storage model, and testing approach |
| [Concepts](CONCEPTS.md) | Vocabulary — Dataset, Channel, Label Set, Segmentation, Mask |
| [Writing an analysis](docs/writing_an_analysis.md) | Adding a new analysis module |
| [Methods](docs/methods/) | How puncta detection works, and its validation record |
| [Changelog](CHANGELOG.md) | Dated feature history |

## Installation

PerCell4 requires Python 3.12 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional extras are available for GPU support (`gpu`), FLIM file formats (`flim`),
ImageJ ROI import (`imagej`), and OCR tooling (`ocr`):

```bash
pip install -e ".[flim,imagej]"
```

See the [installation guide](docs/installation.md) for per-OS instructions, PyTorch and
Cellpose notes, standalone bundles, and troubleshooting.

## Usage

Launch the application:

```bash
percell4-gui
```

The [workflow protocol](docs/workflow-protocol.md) walks through the single-cell
analysis workflow step by step, from importing `.tiff` exports through segmentation,
thresholding, and measurement.

Batch operations can also be run without a display:

```bash
percell4-inspect data/*.h5
percell4-batch-threshold data/*.h5 --round-name SG_mask --channel mNG \
    --strategy adaptive-clip --d-min-um 1.0
percell4-batch-measure data/*.h5 --mask SG_mask --output results/
```

See the [command-line reference](docs/cli.md) for all tools and options.

## Key capabilities

- **Segmentation and tracking** — Cellpose segmentation with interactive quality control
  in napari, and cell tracking with lineage across time-lapse acquisitions.
- **Thresholding and puncta detection** — grouped per-cell autothresholding, adaptive
  local clipping for puncta, and subpopulation classification by contrast-to-noise ratio
  or other per-particle metrics.
- **FLIM and phasor analysis** — phasor computation from TCSPC decays with per-channel
  and per-harmonic calibration, wavelet filtering, phasor-based masks, and FLIM-FRET.
- **Measurement and export** — configurable per-cell and per-particle measurements across
  every segmentation and mask, exported as parquet, CSV, and TIFF.

## Getting help

Questions and bug reports are welcome in
[GitHub issues](https://github.com/marcusjoshm/percell4/issues). Please include your
operating system, Python version, and the output of `percell4-inspect` for the dataset
involved where relevant.

## Contributing

Contributions are welcome. [Writing an analysis](docs/writing_an_analysis.md) describes
how to add a new analysis module, and [the architecture notes](docs/architecture.md)
describe the code layout and how the test suite is organised.

## Citing PerCell4

If you use PerCell4 in your research, please cite it. Citation metadata is in
[`CITATION.cff`](CITATION.cff), which GitHub renders through the **Cite this repository**
button on this page.

## License

PerCell4 is distributed under the MIT License. See [`LICENSE`](LICENSE).
