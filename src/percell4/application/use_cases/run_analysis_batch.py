"""Analysis batch runner.

Iterates over selected ``.h5`` datasets, calls
:func:`percell4.application.use_cases.run_analysis.run_analysis` for
each, isolates per-dataset failures into structured
:class:`BatchAnalysisItemResult` items, writes image outputs back
into each ``.h5``, accumulates table outputs into combined +
per-dataset CSVs, and finalizes a ``run_config.json`` describing the
run.

Cancel semantics: **fail-fast, no resume.** ``cancel_check`` is polled
at the start of each per-dataset iteration. On cancel mid-batch,
datasets-so-far have their image outputs persisted; combined and
per-dataset CSVs are written for those datasets only;
``run_config.json`` records ``status="cancelled"`` and
``completed_dataset_count=N``. Re-running re-processes from the start.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from percell4.application.analysis import registry
from percell4.application.analysis.run_folder import (
    create_analysis_run_folder,
    percell4_version,
    write_analysis_run_config,
)
from percell4.application.analysis.types import (
    BatchAnalysisItemResult,
    BatchAnalysisReport,
)
from percell4.application.use_cases.run_analysis import (
    resolve_params,
    run_analysis,
)
from percell4.domain.analysis import ImageOutput, TableOutput
from percell4.store import DatasetStore


def batch_run_analysis(
    analysis_name: str,
    h5_paths: list[Path],
    layer_map_resolver: Callable[[Path], dict[str, str]],
    *,
    output_parent: Path,
    params: dict[str, Any] | None = None,
    preset: str | None = None,
    progress_callback: Callable[[BatchAnalysisItemResult, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> BatchAnalysisReport:
    """Run ``analysis_name`` over each path in ``h5_paths`` with isolation.

    ``layer_map_resolver`` is invoked once per dataset to obtain the
    role-name → layer-name dict for that dataset. For v1 the GUI
    passes a closure returning the same map for every dataset; per-
    dataset overrides are a future extension.

    Returns a :class:`BatchAnalysisReport` and persists a run folder
    under ``output_parent``. The folder always exists on a successful
    return — even if every item failed.

    ``log`` is an optional progress sink (e.g. ``print``). When
    provided, a per-dataset banner is emitted and the same per-step
    progress the CLI streams is forwarded from each analysis's
    ``run()``. When ``None`` (the default) the batch is silent.
    """
    if not h5_paths:
        raise ValueError("batch_run_analysis requires at least one h5_path")

    cls = registry.get(analysis_name)
    h5_paths = [Path(p) for p in h5_paths]
    output_parent = Path(output_parent)

    resolved_params = resolve_params(analysis_name, cls, params, preset)
    preset_values = dict(cls.presets[preset]) if preset is not None else None

    folder = create_analysis_run_folder(output_parent)

    table_accumulators: dict[str, list[pd.DataFrame]] = {}
    layer_maps_per_dataset: dict[str, dict[str, str]] = {}
    results: list[BatchAnalysisItemResult] = []
    cancelled = False

    for idx, path in enumerate(h5_paths):
        if cancel_check is not None and cancel_check():
            cancelled = True
            break

        try:
            layer_map = layer_map_resolver(path)
        except Exception as exc:
            item = BatchAnalysisItemResult(
                h5_path=path,
                status="failed",
                error=f"layer_map_resolver raised: {exc}",
            )
            results.append(item)
            if progress_callback is not None:
                progress_callback(item, idx, len(h5_paths))
            continue

        layer_maps_per_dataset[path.stem] = dict(layer_map)

        if log is not None:
            log(f"\n=== {analysis_name}: {path.stem} "
                f"({idx + 1}/{len(h5_paths)}) ===")

        try:
            outputs = run_analysis(
                analysis_name,
                path,
                layer_map,
                params=params,
                preset=preset,
                log=log,
                set_label=path.stem,
            )
        except Exception as exc:
            item = BatchAnalysisItemResult(
                h5_path=path,
                status="failed",
                error=str(exc),
            )
        else:
            try:
                produced_names = _persist_outputs(
                    path, outputs, cls, table_accumulators
                )
            except Exception as exc:
                item = BatchAnalysisItemResult(
                    h5_path=path,
                    status="failed",
                    error=f"output write failed: {exc}",
                )
            else:
                item = BatchAnalysisItemResult(
                    h5_path=path,
                    status="succeeded",
                    produced_outputs=tuple(produced_names),
                )

        results.append(item)
        if progress_callback is not None:
            progress_callback(item, idx, len(h5_paths))

    _write_csvs(folder, table_accumulators)

    payload = _build_run_config_payload(
        analysis_name=analysis_name,
        cls_version=cls.version,
        preset_name=preset,
        preset_values=preset_values,
        resolved_params=resolved_params,
        layer_maps_per_dataset=layer_maps_per_dataset,
        h5_paths=h5_paths,
        results=results,
        cancelled=cancelled,
    )
    write_analysis_run_config(folder, payload)

    return BatchAnalysisReport(
        items=tuple(results),
        run_folder=folder,
        cancelled=cancelled,
    )


def _persist_outputs(
    h5_path: Path,
    outputs: dict[str, Any],
    cls: type,
    table_accumulators: dict[str, list[pd.DataFrame]],
) -> list[str]:
    """Write image outputs to the dataset; accumulate table outputs.

    Returns the list of output names actually persisted.
    """
    produced: list[str] = []
    store: DatasetStore | None = None
    declared_outputs = cls.outputs

    for name, value in outputs.items():
        decl = declared_outputs[name]
        if isinstance(decl, ImageOutput):
            if store is None:
                store = DatasetStore(h5_path)
            if decl.dtype == "binary":
                store.write_mask(name, value)
            elif decl.dtype == "labels":
                store.write_labels(name, value)
            else:
                raise ValueError(
                    f"output {name!r}: unsupported ImageOutput dtype {decl.dtype!r}"
                )
            produced.append(name)
        elif isinstance(decl, TableOutput):
            df = value.copy()
            df.insert(0, "dataset", h5_path.stem)
            table_accumulators.setdefault(name, []).append(df)
            produced.append(name)
        else:
            raise ValueError(
                f"output {name!r}: unknown declared type {type(decl).__name__}"
            )
    return produced


def _write_csvs(
    folder: Path,
    table_accumulators: dict[str, list[pd.DataFrame]],
) -> None:
    """Write combined_<table>.csv + per_dataset/<stem>_<table>.csv files."""
    per_dataset_dir = folder / "per_dataset"
    for table_name, dfs in table_accumulators.items():
        if not dfs:
            continue
        combined = pd.concat(dfs, ignore_index=True)
        combined.to_csv(folder / f"combined_{table_name}.csv", index=False)
        for df in dfs:
            stem = df["dataset"].iloc[0] if len(df) else "unknown"
            df.to_csv(per_dataset_dir / f"{stem}_{table_name}.csv", index=False)


def _build_run_config_payload(
    *,
    analysis_name: str,
    cls_version: str,
    preset_name: str | None,
    preset_values: dict[str, Any] | None,
    resolved_params: dict[str, Any],
    layer_maps_per_dataset: dict[str, dict[str, str]],
    h5_paths: list[Path],
    results: list[BatchAnalysisItemResult],
    cancelled: bool,
) -> dict[str, Any]:
    completed = sum(1 for r in results if r.status != "skipped")
    return {
        "analysis_name": analysis_name,
        "analysis_version": cls_version,
        "preset_name": preset_name,
        "preset_values": preset_values,
        "params": resolved_params,
        "layer_map_per_dataset": layer_maps_per_dataset,
        "dataset_paths": [str(p.resolve()) for p in h5_paths],
        "timestamp": datetime.now(UTC).isoformat(),
        "percell4_version": percell4_version(),
        "status": "cancelled" if cancelled else "completed",
        "completed_dataset_count": completed,
        "results": [
            {
                "path": str(r.h5_path),
                "status": r.status,
                "produced_outputs": list(r.produced_outputs),
                "error": r.error,
            }
            for r in results
        ],
    }
