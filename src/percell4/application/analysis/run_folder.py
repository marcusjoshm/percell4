"""Run-folder I/O for analysis batches.

Reuses :func:`percell4.workflows.artifacts.create_run_folder` and
:func:`write_atomic`. The single-cell + FLIM-FRET workflows already
use the same helpers; the analysis batch runner adopts the same
``<prefix>_<timestamp>_<shortuuid>/`` folder convention with
``prefix="analysis_run"``.

The ``run_config.json`` schema is different from
:class:`WorkflowConfig`/:class:`RunMetadata`, so we write a simple
dict payload via :func:`write_atomic` rather than reusing
``write_run_config``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from percell4.workflows.artifacts import create_run_folder, write_atomic

_RUN_CONFIG_NAME = "run_config.json"


def create_analysis_run_folder(output_parent: Path) -> Path:
    """Create ``<output_parent>/analysis_run_<ts>_<uuid>/`` with per_dataset/ + staging/."""
    return create_run_folder(
        Path(output_parent),
        prefix="analysis_run",
        create_subdirs=True,
    )


def write_analysis_run_config(folder: Path, payload: dict[str, Any]) -> None:
    """Atomically write ``run_config.json`` under ``folder``."""
    folder = Path(folder)

    def _writer(tmp: Path) -> None:
        tmp.write_text(
            json.dumps(payload, indent=2, default=_json_default),
            encoding="utf-8",
        )

    write_atomic(folder / _RUN_CONFIG_NAME, _writer)


def percell4_version() -> str:
    """Best-effort retrieval of the installed PerCell4 version."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("percell4")
    except PackageNotFoundError:
        return "unknown"
    except Exception:
        return "unknown"


def _json_default(obj: Any) -> Any:
    """JSON encoder for Path objects."""
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"{type(obj).__name__} not JSON serializable")
