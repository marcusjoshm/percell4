"""Use case: export cached phasor data to .npz files for external scripts.

One .npz per channel: g, s, g_filtered, s_filtered, lifetime_filtered
(when wavelet has been applied), plus a metadata bytestring. Intensity
is NOT exported — it is decay-derived; the importer reconstructs it
from its own /decay/<ch>. Storing intensity here would re-create the
silent-misalignment hazard documented in
docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md.

Atomic write contract: write to a temporary file in the destination
directory, then os.replace to the final path. Pass an explicit file
object to np.savez to defeat its .npz suffix auto-append behavior.

Metadata format (security): the metadata dict is serialized as
UTF-8 JSON bytes stored as a uint8 array. This intentionally avoids
np.savez's object-array path (which requires allow_pickle=True on
load and would expose pickle-based RCE on collaborator-supplied
files). Importers decode via ``json.loads(bytes(data["metadata"]))``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1


@dataclass
class ExportPhasorResult:
    """Result of exporting cached phasor for the active dataset.

    ``exported`` carries (channel_name, written_path) tuples.
    ``skipped`` carries channel names that had no /phasor/<ch>/g.
    """

    exported: list[tuple[str, Path]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class ExportPhasorNpz:
    """Export every channel's cached phasor as one .npz file per channel."""

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def _read_fresh_metadata(self, handle) -> dict:
        """Read /metadata fresh from disk, with snapshot fallback."""
        reader = getattr(self._repo, "read_metadata", None)
        if reader is not None:
            try:
                return reader(handle)
            except (KeyError, AttributeError, OSError) as exc:
                logger.warning(
                    "Fresh metadata read failed (%s); falling back to "
                    "handle.metadata snapshot", exc,
                )
        return dict(handle.metadata)

    def execute(self, out_dir: Path) -> ExportPhasorResult:
        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        meta = self._read_fresh_metadata(handle)
        channel_names = list(meta.get("channel_names", []))

        result = ExportPhasorResult()
        stem = handle.path.stem

        for ch in channel_names:
            try:
                g_map = self._repo.read_array(handle, f"phasor/{ch}/g")
            except KeyError:
                result.skipped.append(ch)
                continue

            try:
                s_map = self._repo.read_array(handle, f"phasor/{ch}/s")
            except KeyError:
                logger.warning(
                    "Asymmetric phasor cache for channel %s during export: "
                    "g present, s missing. Skipping.", ch,
                )
                result.skipped.append(ch)
                continue

            # Optional wavelet results — all-three-or-none.
            wavelet_keys: dict[str, np.ndarray] = {}
            try:
                wavelet_keys["g_filtered"] = self._repo.read_array(
                    handle, f"phasor/{ch}/g_filtered",
                )
                wavelet_keys["s_filtered"] = self._repo.read_array(
                    handle, f"phasor/{ch}/s_filtered",
                )
                # lifetime_filtered may be absent if wavelet ran without
                # frequency data — accept that case (None lifetime).
                try:
                    wavelet_keys["lifetime_filtered"] = self._repo.read_array(
                        handle, f"phasor/{ch}/lifetime_filtered",
                    )
                except KeyError:
                    pass
            except KeyError:
                # Asymmetric or absent wavelet cache: drop all wavelet
                # keys. Surface a warning when partial wavelet cache is
                # detected (mirrors LoadCachedPhasor's defensive
                # logging) so an in-progress crash mid-write of
                # apply_wavelet is visible at export time too.
                if "g_filtered" in wavelet_keys:
                    logger.warning(
                        "Asymmetric wavelet cache for channel %s during "
                        "export: g_filtered present, s_filtered missing. "
                        "Dropping wavelet keys from .npz.", ch,
                    )
                wavelet_keys = {}

            metadata_dict = {
                "schema_version": SCHEMA_VERSION,
                "channel": ch,
                "flim_frequency_mhz": meta.get("flim_frequency_mhz"),
                "source_dataset_stem": stem,
            }

            # Metadata as UTF-8 JSON bytes — avoids the pickle path that
            # np.savez(..., metadata=np.array(d, dtype=object)) would
            # require, eliminating the allow_pickle=True RCE vector for
            # importers loading collaborator-supplied .npz files.
            metadata_bytes = json.dumps(metadata_dict).encode("utf-8")
            payload: dict[str, np.ndarray] = {
                "g": g_map.astype(np.float32, copy=False),
                "s": s_map.astype(np.float32, copy=False),
                **{
                    k: v.astype(np.float32, copy=False)
                    for k, v in wavelet_keys.items()
                },
                "metadata": np.frombuffer(metadata_bytes, dtype=np.uint8),
            }

            final_path = out_dir / f"{stem}_{ch}_phasor.npz"
            _atomic_savez(payload, final_path)
            result.exported.append((ch, final_path))

        return result


def _atomic_savez(payload: dict[str, np.ndarray], final_path: Path) -> None:
    """Write ``payload`` as an .npz at ``final_path`` atomically.

    Steps:
        1. ``tempfile.mkstemp`` in the destination directory (same
           filesystem so ``os.replace`` is atomic).
        2. Open the tmp file as a binary write handle and pass the
           handle to ``np.savez`` — this defeats np.savez's behavior
           of appending ``.npz`` to a path argument, which would
           otherwise produce ``<tmp>.tmp.npz`` instead of writing to
           ``<tmp>.tmp`` and break the subsequent ``os.replace``.
        3. ``os.replace`` the tmp into the final path.

    On any exception, attempt to remove the tmp file before re-raising.
    """
    parent = final_path.parent
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=parent)
    os.close(fd)  # we'll reopen via open() so np.savez writes to the path we control

    try:
        with open(tmp_path, "wb") as f:
            np.savez(f, **payload)
        os.replace(tmp_path, final_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
