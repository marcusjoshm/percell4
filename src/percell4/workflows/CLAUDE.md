# src/percell4/workflows/

Qt-agnostic building blocks for batch analysis workflows. The pure-Python
core used by `percell4.gui.workflows.*` runners: configuration dataclasses,
run-folder I/O, channel intersection, a host protocol, a failure taxonomy,
and a jsonl audit-log helper. Nothing in this subpackage imports `qtpy`,
`PyQt5`, or `napari` — it is importable and unit-testable without a running
`QApplication`.

## Modules

- `models.py` — `WorkflowConfig` (frozen recipe), `CellposeSettings`,
  `ThresholdingRound` (with strict regex / metric / count validation),
  `WorkflowDatasetEntry`, `RunMetadata` (mutable runtime instance), and the
  `ThresholdAlgorithm` / `GmmCriterion` / `DatasetSource` StrEnums. Every
  dataclass validates invariants in `__post_init__` so a stale
  `run_config.json` fails loudly at load time.
- `failures.py` — `DatasetFailure` (StrEnum) and `FailureRecord`. Per-dataset
  failures are first class: a misbehaving dataset never crashes the run.
- `artifacts.py` — `write_atomic(path, writer_fn)` (`.tmp` + `os.replace`),
  `create_run_folder(output_parent)` (timestamped + uuid suffix,
  `exist_ok=False`, creates `per_dataset/` and `staging/` subdirs),
  `config_to_dict` / `config_from_dict` handling `Path` ↔ `str`, nested
  dataclasses, and `StrEnum` values, plus `write_run_config` /
  `read_run_config` that persist both the recipe and `RunMetadata` in a
  single `run_config.json` file.
- `channels.py` — `intersect_channels(sources)`. Takes a list of
  `(dataset_name, channel_names)` tuples — for `h5_existing` entries the
  names come from `store.metadata["channel_names"]`; for `tiff_pending`
  entries they come from the `CompressDialog` scan result. Returns the
  order-preserving intersection plus a list of outlier dataset names.
- `run_log.py` — `RunLog(folder)`. Append-only jsonl audit trail keyed by
  phase and dataset. Each write flushes and fsyncs so the log survives a
  crash.
- `host.py` — `WorkflowHost` `Protocol`. The narrow six-method surface a
  batch runner uses to talk to the launcher: lock / unlock the main UI,
  show status, get the viewer and data model, close / restore child
  windows. `LauncherWindow` conforms structurally.
