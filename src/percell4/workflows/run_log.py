"""Append-only jsonl audit log for a workflow run.

Each line is a UTC-timestamped JSON object. The log lives alongside
``run_config.json`` in the run folder and captures everything that happened
during execution — phase transitions, per-dataset status, failures — so the
user can retrace a run weeks later without guessing.

File is opened, written, ``flush``-ed, and ``fsync``-ed on every event so the
log survives a crash. This is slightly more expensive than buffered writes
but the audit trail is small (one line per event, tens of events per run) so
the overhead is negligible.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

_LOG_NAME = "run_log.jsonl"


class RunLog:
    """Append-only jsonl audit log.

    Usage::

        log = RunLog(run_folder)
        log.log(phase="segment", dataset="DS1", event="started")
        log.log(phase="segment", dataset="DS1", event="done", n_cells=234)
    """

    def __init__(self, folder: Path) -> None:
        self._path = Path(folder) / _LOG_NAME
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def log(
        self,
        *,
        phase: str = "",
        dataset: str = "",
        event: str,
        **fields: Any,
    ) -> None:
        """Append one event to the log.

        ``phase`` and ``dataset`` are common enough that they get named
        parameters; all other context goes in ``**fields``.
        """
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "phase": phase,
            "dataset": dataset,
            "event": event,
        }
        entry.update(fields)
        line = json.dumps(entry, default=_json_default) + "\n"
        # Open/append/flush/fsync on every call — small file, few events, and
        # we care more about surviving a crash than about write throughput.
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)
