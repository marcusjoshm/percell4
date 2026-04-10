"""Run-folder I/O: atomic writes, run_config.json round-trip, folder layout.

Every artifact write goes through :func:`write_atomic`, which writes to a
``.tmp`` file and calls :func:`os.replace` on success. There is never an
``os.unlink`` before the replace — a crash between unlink and replace would
leave the user with neither the old nor the new content.

JSON serialization explicitly reconstructs nested dataclasses and converts
``Path`` ↔ ``str`` and ``datetime`` ↔ ISO string — ``dataclasses.asdict`` +
``WorkflowConfig(**data)`` silently produces a ``list[dict]`` where a
``list[ThresholdingRound]`` is expected, so we do it by hand.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from percell4.workflows.failures import DatasetFailure, FailureRecord
from percell4.workflows.models import (
    CellposeSettings,
    DatasetSource,
    GmmCriterion,
    RunMetadata,
    ThresholdAlgorithm,
    ThresholdingRound,
    WorkflowConfig,
    WorkflowDatasetEntry,
)

# ── Atomic writes ────────────────────────────────────────────────────────


def write_atomic(path: Path, writer_fn: Callable[[Path], None]) -> None:
    """Write a file atomically via a .tmp sibling + fsync + os.replace.

    ``writer_fn`` receives the temp path and is responsible for actually
    writing the bytes. After it returns, this helper fsyncs the temp file's
    contents so a crash between write and replace cannot surface a
    zero-length file on ext4 / APFS. Then :func:`os.replace` atomically
    moves the temp into place; on POSIX we also fsync the parent directory
    so the rename itself is durable.

    On any exception the temp file is removed and the error re-raised.
    We never ``os.unlink`` the target first — a crash between unlink and
    replace would leave the user with *nothing*. ``os.replace`` is atomic
    on every supported platform (POSIX and Windows).

    The temp path uses ``path.name + ".tmp"`` rather than ``with_suffix``
    so a multi-dot path like ``measurements.parquet.gz`` produces
    ``measurements.parquet.gz.tmp``, not ``measurements.parquet.tmp`` that
    would collide with an unrelated sibling.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        writer_fn(tmp)
        # Ensure the temp file's bytes are on disk before the rename.
        with open(tmp, "rb") as fd:
            os.fsync(fd.fileno())
    except BaseException:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    os.replace(tmp, path)
    # Fsync the parent directory so the rename survives a crash. POSIX
    # only; Windows does not support directory fsync and is best-effort.
    if os.name == "posix":
        try:
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass


# ── Run folder creation ──────────────────────────────────────────────────


def create_run_folder(output_parent: Path) -> Path:
    """Create a new ``run_<utc-timestamp>_<shortuuid>/`` folder.

    The timestamp is UTC in ``YYYY-MM-DDTHHMMSSZ`` form — matches the
    ``run_log.jsonl`` entries (also UTC), sorts lexicographically, and is
    DST-safe. The uuid suffix guards against collisions when two runs
    start within the same second. ``mkdir(exist_ok=False)`` fails fast on
    any collision — the caller should surface the error rather than
    silently sharing a folder between runs.

    Subdirectories created up front: ``per_dataset/`` and ``staging/``.
    """
    output_parent = Path(output_parent)
    output_parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    folder = output_parent / f"run_{ts}_{suffix}"
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "per_dataset").mkdir()
    (folder / "staging").mkdir()
    return folder


# ── JSON encoding helpers ────────────────────────────────────────────────


def _json_default(obj: Any) -> Any:
    """JSON encoder for Path, datetime, and StrEnum values."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"{type(obj).__name__} not JSON serializable")


# ── WorkflowConfig <-> dict ──────────────────────────────────────────────


def _cellpose_to_dict(c: CellposeSettings) -> dict[str, Any]:
    return {
        "model": c.model,
        "diameter": c.diameter,
        "gpu": c.gpu,
        "flow_threshold": c.flow_threshold,
        "cellprob_threshold": c.cellprob_threshold,
        "min_size": c.min_size,
    }


def _cellpose_from_dict(d: dict[str, Any]) -> CellposeSettings:
    return CellposeSettings(
        model=d.get("model", "cpsam"),
        diameter=d.get("diameter", 30.0),
        gpu=d.get("gpu", True),
        flow_threshold=d.get("flow_threshold", 0.4),
        cellprob_threshold=d.get("cellprob_threshold", 0.0),
        min_size=d.get("min_size", 15),
    )


def _round_to_dict(r: ThresholdingRound) -> dict[str, Any]:
    return {
        "name": r.name,
        "channel": r.channel,
        "metric": r.metric,
        "algorithm": r.algorithm.value,
        "gmm_criterion": r.gmm_criterion.value,
        "gmm_max_components": r.gmm_max_components,
        "kmeans_n_clusters": r.kmeans_n_clusters,
        "gaussian_sigma": r.gaussian_sigma,
    }


def _round_from_dict(d: dict[str, Any]) -> ThresholdingRound:
    return ThresholdingRound(
        name=d["name"],
        channel=d["channel"],
        metric=d["metric"],
        algorithm=ThresholdAlgorithm(d["algorithm"]),
        gmm_criterion=GmmCriterion(d.get("gmm_criterion", "bic")),
        gmm_max_components=d.get("gmm_max_components", 4),
        kmeans_n_clusters=d.get("kmeans_n_clusters", 3),
        gaussian_sigma=d.get("gaussian_sigma", 1.0),
    )


def _entry_to_dict(e: WorkflowDatasetEntry) -> dict[str, Any]:
    return {
        "name": e.name,
        "source": e.source.value,
        "h5_path": str(e.h5_path),
        "channel_names": list(e.channel_names),
        "compress_plan": e.compress_plan,
    }


def _entry_from_dict(d: dict[str, Any]) -> WorkflowDatasetEntry:
    return WorkflowDatasetEntry(
        name=d["name"],
        source=DatasetSource(d["source"]),
        h5_path=Path(d["h5_path"]),
        channel_names=list(d.get("channel_names", [])),
        compress_plan=d.get("compress_plan"),
    )


def config_to_dict(cfg: WorkflowConfig) -> dict[str, Any]:
    """Serialize a WorkflowConfig to a plain JSON-safe dict."""
    return {
        "datasets": [_entry_to_dict(e) for e in cfg.datasets],
        "cellpose": _cellpose_to_dict(cfg.cellpose),
        "thresholding_rounds": [_round_to_dict(r) for r in cfg.thresholding_rounds],
        "selected_csv_columns": list(cfg.selected_csv_columns),
        "output_parent": str(cfg.output_parent),
    }


def config_from_dict(data: dict[str, Any]) -> WorkflowConfig:
    """Reconstruct a WorkflowConfig from its JSON-safe dict form."""
    return WorkflowConfig(
        datasets=[_entry_from_dict(d) for d in data["datasets"]],
        cellpose=_cellpose_from_dict(data["cellpose"]),
        thresholding_rounds=[
            _round_from_dict(r) for r in data["thresholding_rounds"]
        ],
        selected_csv_columns=list(data["selected_csv_columns"]),
        output_parent=Path(data["output_parent"]),
    )


# ── RunMetadata <-> dict ─────────────────────────────────────────────────


def _failure_to_dict(f: FailureRecord) -> dict[str, Any]:
    return {
        "dataset_name": f.dataset_name,
        "phase_name": f.phase_name,
        "failure": f.failure.value,
        "message": f.message,
        "ts": f.ts.isoformat(),
    }


def _failure_from_dict(d: dict[str, Any]) -> FailureRecord:
    return FailureRecord(
        dataset_name=d["dataset_name"],
        phase_name=d["phase_name"],
        failure=DatasetFailure(d["failure"]),
        message=d["message"],
        ts=datetime.fromisoformat(d["ts"]),
    )


def metadata_to_dict(meta: RunMetadata) -> dict[str, Any]:
    return {
        "run_id": meta.run_id,
        "run_folder": str(meta.run_folder),
        "started_at": meta.started_at.isoformat(),
        "finished_at": meta.finished_at.isoformat() if meta.finished_at else None,
        "intersected_channels": list(meta.intersected_channels),
        "failures": [_failure_to_dict(f) for f in meta.failures],
    }


def metadata_from_dict(data: dict[str, Any]) -> RunMetadata:
    return RunMetadata(
        run_id=data["run_id"],
        run_folder=Path(data["run_folder"]),
        started_at=datetime.fromisoformat(data["started_at"]),
        finished_at=(
            datetime.fromisoformat(data["finished_at"])
            if data.get("finished_at")
            else None
        ),
        intersected_channels=list(data.get("intersected_channels", [])),
        failures=[_failure_from_dict(f) for f in data.get("failures", [])],
    )


# ── run_config.json read/write ───────────────────────────────────────────


_RUN_CONFIG_NAME = "run_config.json"


def write_run_config(
    folder: Path,
    cfg: WorkflowConfig,
    meta: RunMetadata,
) -> None:
    """Atomically write ``run_config.json`` into ``folder``.

    The file has two top-level keys: ``config`` (the recipe) and
    ``metadata`` (the runtime instance: run_id, timestamps, intersected
    channels, failures so far).
    """
    payload = {
        "config": config_to_dict(cfg),
        "metadata": metadata_to_dict(meta),
    }
    blob = json.dumps(payload, indent=2, default=_json_default)

    def _writer(tmp: Path) -> None:
        tmp.write_text(blob, encoding="utf-8")

    write_atomic(Path(folder) / _RUN_CONFIG_NAME, _writer)


def read_run_config(folder: Path) -> tuple[WorkflowConfig, RunMetadata]:
    """Read and parse ``run_config.json`` from ``folder``.

    Raises ``FileNotFoundError`` if the file is missing, ``ValueError`` /
    ``KeyError`` if the payload is malformed. ``WorkflowConfig.__post_init__``
    runs during reconstruction and will raise on any invariant violation.
    """
    path = Path(folder) / _RUN_CONFIG_NAME
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    cfg = config_from_dict(payload["config"])
    meta = metadata_from_dict(payload["metadata"])
    return cfg, meta
