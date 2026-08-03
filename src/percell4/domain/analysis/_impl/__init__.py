"""Pure-function implementations of registered analyses.

Each module here exposes a single ``run_*`` function that takes
pre-loaded numpy arrays + parameters and returns numpy arrays + row
dicts. No Qt, no h5py, no tifffile, no napari — just numpy + scipy +
scikit-image. The application-layer registered ``Analysis`` subclasses
(in ``percell4.application.analysis.modules.*``) call these.

The CLI script ``per_particle_analysis.py`` at the repo root imports
``run_one_image_set`` from here so the CLI and the framework share
exactly one source of truth for the donut math.
"""
