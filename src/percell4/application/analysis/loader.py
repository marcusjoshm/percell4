"""HDF5 layer loader for the registered-analyses framework.

Given a user-supplied ``layer_map`` (role name → on-disk layer name) and
the analysis's role declarations, this module reads each layer from the
``.h5`` file via :class:`DatasetStore`, dispatches by ``ImageRole.kind``,
applies dtype coercion, and validates the array's ``ndim`` against the
role's declaration. The result is a dict of role name → ``numpy.ndarray``
ready to hand to ``Analysis.run()``.

Dispatch rules (see plan U3):

- ``kind="intensity"``: resolve the layer name against
  ``metadata["channel_names"]``. If matched, read via
  ``store.read_channel("intensity", channel_idx)`` and cast to
  ``float64``. Else if the name appears in ``store.list_groups("decay")``
  (FLIM-style per-channel decay), read via ``store.read_decay(name)`` and
  project to 2D via ``.sum(axis=-1).astype(float64)``. Otherwise raise.
- ``kind="mask"``: ``store.read_array(f"masks/{name}")`` then
  ``(arr > 0).astype(bool)``.
- ``kind="label"``: ``store.read_labels(name).astype(int32)``.

All errors carry the role name, requested layer name, and ``.h5`` path
in the message so downstream batch reporting can surface them without
re-deriving context.

Plan: ``docs/plans/2026-05-27-004-feat-analysis-integration-plan.md``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from percell4.domain.analysis import ImageRole
from percell4.store import DatasetStore

# ── Exceptions ────────────────────────────────────────────────────────


class LayerError(Exception):
    """Base class for loader errors."""


class LayerNotFoundError(LayerError):
    """The requested layer is missing from the ``.h5`` file."""


class LayerDtypeError(LayerError):
    """The loaded layer's shape or dtype does not match the role's declaration."""


# ── Public API ────────────────────────────────────────────────────────


def load_layers(
    h5_path: Path,
    layer_map: dict[str, str],
    roles: dict[str, ImageRole],
    timepoint: int | None = None,
) -> dict[str, NDArray]:
    """Read every role in ``layer_map`` from ``h5_path``.

    Parameters
    ----------
    h5_path:
        Path to the ``.h5`` dataset file.
    layer_map:
        Mapping from declared role name (e.g. ``"Cap"``) to the on-disk
        layer name the user picked (channel name for intensity roles,
        mask/label name for mask/label roles). Only roles present here
        are loaded; absent roles (e.g. unspecified optional inputs) are
        silently skipped.
    roles:
        Combined role declarations (required + optional + flattened
        input-groups) the loader dispatches on. Must contain every key
        in ``layer_map``.
    timepoint:
        When given, load the **2D frame** for that timepoint of a
        time-lapse dataset (intensity sliced per channel, labels/masks
        sliced or 2D-broadcast). ``None`` (default) reads whole — the
        single-timepoint path. The analysis framework loops timepoints and
        passes ``timepoint=t`` so each analysis run receives 2D inputs that
        satisfy the role ``ndim`` declarations.

    Returns
    -------
    dict[str, np.ndarray]
        One entry per role in ``layer_map``, dtype-coerced per
        :class:`ImageRole.kind`.

    Raises
    ------
    LayerNotFoundError
        Requested layer is missing from disk.
    LayerDtypeError
        Loaded array's ``ndim`` is not in ``role.ndim``.
    """
    store = DatasetStore(h5_path)
    out: dict[str, NDArray] = {}
    for role_name, layer_name in layer_map.items():
        if role_name not in roles:
            # Defensive: the runner validates this before calling, but
            # surface a clear error if a caller skips the runner.
            raise LayerError(
                f"role {role_name!r} not declared by analysis "
                f"(path={h5_path})"
            )
        role = roles[role_name]
        arr = _read_one(store, role_name, layer_name, role, h5_path, timepoint)
        if arr.ndim not in role.ndim:
            raise LayerDtypeError(
                f"role {role_name!r}: layer {layer_name!r} has ndim="
                f"{arr.ndim}, expected one of {role.ndim} (path={h5_path})"
            )
        out[role_name] = arr
    return out


# ── Per-kind dispatch ─────────────────────────────────────────────────


def _read_one(
    store: DatasetStore,
    role_name: str,
    layer_name: str,
    role: ImageRole,
    h5_path: Path,
    timepoint: int | None,
) -> NDArray:
    """Read a single layer per ``role.kind``."""
    kind = role.kind
    if kind == "intensity":
        return _read_intensity(store, role_name, layer_name, h5_path, timepoint)
    if kind == "mask":
        return _read_mask(store, role_name, layer_name, h5_path, timepoint)
    if kind == "label":
        return _read_label(store, role_name, layer_name, h5_path, timepoint)
    # ImageRole.kind is a Literal so this is unreachable from a typed
    # caller; guard anyway for the runtime contract.
    raise LayerError(
        f"role {role_name!r}: unknown kind {kind!r} (path={h5_path})"
    )


def _read_intensity(
    store: DatasetStore,
    role_name: str,
    layer_name: str,
    h5_path: Path,
    timepoint: int | None,
) -> NDArray:
    """Resolve an intensity layer.

    First try ``metadata["channel_names"]``; fall back to
    ``/decay/<layer_name>`` with sum-over-bins projection. ``timepoint``
    slices the intensity channel to a single frame on a time-lapse dataset
    (decay has no acquisition-T axis, so it is read whole).
    """
    channel_names = store.metadata.get("channel_names") or []
    if layer_name in channel_names:
        idx = list(channel_names).index(layer_name)
        try:
            arr = store.read_channel("intensity", idx, timepoint=timepoint)
        except KeyError as exc:
            raise LayerNotFoundError(
                f"role {role_name!r}: channel {layer_name!r} in "
                f"channel_names but /intensity is missing (path={h5_path})"
            ) from exc
        return arr.astype(np.float64, copy=False)

    decay_names = store.list_groups("decay")
    if layer_name in decay_names:
        decay = store.read_decay(layer_name)
        return decay.sum(axis=-1).astype(np.float64, copy=False)

    raise LayerNotFoundError(
        f"role {role_name!r}: layer {layer_name!r} not found in "
        f"channel_names or /decay/ (path={h5_path})"
    )


def _read_mask(
    store: DatasetStore,
    role_name: str,
    layer_name: str,
    h5_path: Path,
    timepoint: int | None,
) -> NDArray:
    """Read ``/masks/<layer_name>`` and coerce to bool.

    ``timepoint`` slices a ``(T, H, W)`` mask to one frame, or broadcasts a
    2D time-invariant mask, via ``store.read_mask``.
    """
    try:
        arr = store.read_mask(layer_name, timepoint=timepoint)
    except KeyError as exc:
        raise LayerNotFoundError(
            f"role {role_name!r}: mask {layer_name!r} not found at "
            f"/masks/{layer_name} (path={h5_path})"
        ) from exc
    return (arr > 0).astype(bool, copy=False)


def _read_label(
    store: DatasetStore,
    role_name: str,
    layer_name: str,
    h5_path: Path,
    timepoint: int | None,
) -> NDArray:
    """Read ``/labels/<layer_name>`` and coerce to int32.

    ``timepoint`` slices a ``(T, H, W)`` labels stack to one frame, or
    broadcasts a 2D time-invariant label, via ``store.read_labels``.
    """
    try:
        arr = store.read_labels(layer_name, timepoint=timepoint)
    except KeyError as exc:
        raise LayerNotFoundError(
            f"role {role_name!r}: labels {layer_name!r} not found at "
            f"/labels/{layer_name} (path={h5_path})"
        ) from exc
    return arr.astype(np.int32, copy=False)
