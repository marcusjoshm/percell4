"""Use case: import phasor data from a .npz file into the active dataset.

Schema (v1): top-level keys ``g``, ``s`` (required, float32 (H,W));
``g_filtered``, ``s_filtered``, ``lifetime_filtered`` (optional but
all-three-or-none — wavelet writes them together); ``metadata`` (a
1-d uint8 array of UTF-8 JSON bytes containing a dict with at
minimum ``schema_version``).

Intensity is intentionally NOT in the schema. It is decay-derived; the
target dataset's /decay/<target_channel> is the canonical source.
Storing intensity in the .npz would re-create the silent-misalignment
hazard from
docs/solutions/logic-errors/flim-phasor-cross-layer-alignment-2026-04-29.md.

Security:
- ``target_channel`` is sanitized BEFORE any HDF5 access. Without this,
  a malicious .npz with ``metadata['channel'] = '../segmentation/cellpose'``
  could overwrite arbitrary HDF5 groups. The regex matches the
  channel-name conventions seen in real fixtures (``mTQ2``, ``CA-SiR``,
  ``mTQ2_v2``, ``GFP_488``, ``ch.0``).
- ``np.load`` is called WITHOUT ``allow_pickle=True``. Metadata is a
  uint8 array of UTF-8 JSON bytes (decoded via ``json.loads``); the
  pickle-based object-array path is intentionally avoided so a
  malicious .npz cannot execute arbitrary code at load time.

Conflict resolution mirrors decay-append: ``LayerAlreadyExistsError``
is raised when the target ``/phasor/<channel>/g`` exists and
``force=False``. The dialog catches this and surfaces a per-row
conflict chip; the user sets the row to Overwrite (force=True) to
proceed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from percell4.application.session import Session
from percell4.domain.errors import NoDatasetError
from percell4.ports.dataset_repository import DatasetRepository
from percell4.store import LayerAlreadyExistsError

logger = logging.getLogger(__name__)


# Channel-name allowlist. Matches real fixtures (mTQ2, CA-SiR,
# mTQ2_v2, ch.0, GFP_488). Rejects path separators (/, \), parent
# traversal, null bytes, leading dots/dashes (which h5py resolves to
# the parent group — `phasor/./g` becomes `/phasor/g`, polluting the
# channel-name namespace), and anything outside the allowlist.
# First character must be alphanumeric or underscore; subsequent
# characters may include `-` and `.`. The `..` substring is
# additionally rejected explicitly below as defense-in-depth.
_CHANNEL_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-\.]{0,63}$")

# Valid top-level keys in the .npz schema. Any other key signals a
# multi-channel file (declared non-goal) or a third-party format we
# do not understand — reject with a clear error.
_VALID_NPZ_KEYS = frozenset({
    "g", "s", "g_filtered", "s_filtered", "lifetime_filtered", "metadata",
})


@dataclass
class ImportPhasorResult:
    """Result of importing one .npz into the dataset."""

    target_channel: str
    wrote_filtered: bool


class ImportPhasorNpz:
    """Validate a single .npz and write its arrays to /phasor/<target_channel>."""

    def __init__(self, repo: DatasetRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    def execute(
        self,
        npz_path: Path,
        target_channel: str,
        force: bool = False,
    ) -> ImportPhasorResult:
        # Sanitize FIRST — before any HDF5 access. This is the
        # security guardrail against path traversal via untrusted
        # metadata['channel'] values that the dialog may have used to
        # populate the Target channel cell.
        _sanitize_channel_name(target_channel)

        handle = self._session.dataset
        if handle is None:
            raise NoDatasetError("No dataset loaded")

        # Single `with` block guarantees data.close() across every exit
        # path (validation failure, conflict raise, mid-write exception)
        # without needing per-branch close() calls. ``allow_pickle`` is
        # intentionally left False — metadata is a JSON bytestring;
        # accepting pickled objects from collaborator-supplied files
        # would expose arbitrary code execution at load time.
        with np.load(npz_path) as data:
            _validate_npz(data)

            # Conflict check: target /phasor/<channel>/g already exists and
            # force=False → raise; dialog catches and shows conflict chip.
            if not force:
                try:
                    self._repo.read_array(
                        handle, f"phasor/{target_channel}/g",
                    )
                except KeyError:
                    pass  # No conflict — proceed
                else:
                    raise LayerAlreadyExistsError(target_channel)

            # If force=True, clear the existing /phasor/<target> group so
            # orphan keys (e.g., a stale lifetime_filtered) don't survive.
            delete = getattr(self._repo, "delete_path", None)
            if force and delete is not None:
                try:
                    delete(handle, f"phasor/{target_channel}")
                except KeyError:
                    pass

            attrs_base = {"channel": target_channel, "dims": ["H", "W"]}

            self._repo.write_array(
                handle, f"phasor/{target_channel}/g",
                data["g"].astype(np.float32, copy=False),
                attrs=dict(attrs_base),
            )
            self._repo.write_array(
                handle, f"phasor/{target_channel}/s",
                data["s"].astype(np.float32, copy=False),
                attrs=dict(attrs_base),
            )

            wrote_filtered = "g_filtered" in data.files
            if wrote_filtered:
                self._repo.write_array(
                    handle, f"phasor/{target_channel}/g_filtered",
                    data["g_filtered"].astype(np.float32, copy=False),
                    attrs=dict(attrs_base),
                )
                self._repo.write_array(
                    handle, f"phasor/{target_channel}/s_filtered",
                    data["s_filtered"].astype(np.float32, copy=False),
                    attrs=dict(attrs_base),
                )
                if "lifetime_filtered" in data.files:
                    self._repo.write_array(
                        handle, f"phasor/{target_channel}/lifetime_filtered",
                        data["lifetime_filtered"].astype(np.float32, copy=False),
                        attrs=dict(attrs_base),
                    )

        return ImportPhasorResult(
            target_channel=target_channel,
            wrote_filtered=wrote_filtered,
        )


def _sanitize_channel_name(name: str) -> None:
    """Raise ValueError unless ``name`` matches the channel-name allowlist.

    Runs BEFORE any HDF5 access so a malicious metadata['channel']
    value can never reach delete_path / write_array.
    """
    if not isinstance(name, str) or not _CHANNEL_NAME_RE.match(name):
        raise ValueError(
            f"Channel name {name!r} must match [A-Za-z0-9_-.]{{1,64}}"
        )
    # Defense-in-depth: reject parent-traversal even though the regex
    # technically allows two dots (``a.b.c`` is fine; ``..`` would also
    # match if ever allowed; ``../x`` wouldn't because of /).
    if ".." in name:
        raise ValueError(
            f"Channel name {name!r} must match [A-Za-z0-9_-.]{{1,64}} "
            "(contains '..')"
        )


def _validate_npz(data) -> None:
    """Validate a loaded .npz against the v1 schema.

    Raises:
        ValueError: when required keys are missing, shapes mismatch,
            asymmetric wavelet keys are present, or unexpected
            top-level keys are present (catches multi-channel files
            and third-party schemas).
    """
    files = set(data.files)

    # Reject unexpected top-level keys early — surfaces multi-channel
    # files (g_ch0, g_ch1, ...) and accidental intensity carriers.
    extras = files - _VALID_NPZ_KEYS
    if extras:
        raise ValueError(
            f"`.npz` has unexpected keys: {sorted(extras)}. "
            f"Valid keys: {sorted(_VALID_NPZ_KEYS)}."
        )

    if "g" not in files:
        raise ValueError("`.npz` missing required key 'g'")
    if "s" not in files:
        raise ValueError("`.npz` missing required key 's'")

    g = data["g"]
    s = data["s"]
    if g.shape != s.shape:
        raise ValueError(
            f"`g` shape {g.shape} != `s` shape {s.shape}; both must match"
        )

    has_gf = "g_filtered" in files
    has_sf = "s_filtered" in files
    if has_gf != has_sf:
        raise ValueError(
            "g_filtered/s_filtered/lifetime_filtered must be present together "
            "(wavelet writes all three together)"
        )
    if has_gf:
        if data["g_filtered"].shape != g.shape:
            raise ValueError(
                f"`g_filtered` shape {data['g_filtered'].shape} "
                f"!= `g` shape {g.shape}"
            )
        if data["s_filtered"].shape != g.shape:
            raise ValueError(
                f"`s_filtered` shape {data['s_filtered'].shape} "
                f"!= `g` shape {g.shape}"
            )
        if "lifetime_filtered" in files:
            if data["lifetime_filtered"].shape != g.shape:
                raise ValueError(
                    f"`lifetime_filtered` shape "
                    f"{data['lifetime_filtered'].shape} != `g` shape {g.shape}"
                )

    if "metadata" in files:
        meta = _decode_metadata(data["metadata"])
        # If metadata claims a channel, sanitize it. The dialog should
        # already have done this, but defense-in-depth.
        if "channel" in meta:
            _sanitize_channel_name(str(meta["channel"]))


def _decode_metadata(meta_arr) -> dict:
    """Decode the metadata field from a uint8 JSON bytestring.

    Schema v1 stores metadata as ``np.frombuffer(json.dumps(d).encode(),
    dtype=np.uint8)``. Reject anything else (object arrays, strings,
    multi-dim) so a malicious .npz cannot smuggle in a pickled object
    even if the importer were ever called with allow_pickle=True.
    """
    if not isinstance(meta_arr, np.ndarray):
        raise ValueError(
            "`.npz` metadata must be a 1-d uint8 array of UTF-8 JSON bytes"
        )
    if meta_arr.dtype != np.uint8 or meta_arr.ndim != 1:
        raise ValueError(
            f"`.npz` metadata must be a 1-d uint8 array (got dtype "
            f"{meta_arr.dtype}, ndim {meta_arr.ndim}); refusing to decode "
            "object/pickle metadata"
        )
    try:
        meta = json.loads(bytes(meta_arr).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"`.npz` metadata is not valid UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(meta, dict):
        raise ValueError(
            f"`.npz` metadata JSON must decode to a dict (got {type(meta).__name__})"
        )
    return meta
