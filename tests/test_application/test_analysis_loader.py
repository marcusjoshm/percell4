"""Tests for ``percell4.application.analysis.loader.load_layers``.

Each test builds a small synthetic ``.h5`` via :class:`DatasetStore`
(no mocks) and exercises one branch of the kind-dispatched reader.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.analysis.loader import (
    LayerDtypeError,
    LayerNotFoundError,
    load_layers,
)
from percell4.domain.analysis import ImageRole
from percell4.store import DatasetStore

# ── Helpers ───────────────────────────────────────────────────────────


def _make_store(path: Path, channel_names: list[str] | None = None) -> DatasetStore:
    """Create an empty ``.h5`` (optionally with channel_names)."""
    store = DatasetStore(path)
    meta = {"source": "test"}
    if channel_names is not None:
        meta["channel_names"] = channel_names
    store.create(metadata=meta)
    return store


# ── kind="intensity" ──────────────────────────────────────────────────


def test_intensity_resolved_via_channel_names(tmp_path: Path) -> None:
    """An intensity role's layer name is matched against channel_names."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5, channel_names=["Cap", "pnorm"])
    data = np.zeros((2, 4, 4), dtype=np.float32)
    data[0] = 1.0  # Cap
    data[1] = 7.0  # pnorm
    store.write_array("intensity", data, attrs={"dims": ["C", "H", "W"]})

    roles = {"Cap": ImageRole(kind="intensity", dtype="float")}
    out = load_layers(h5, {"Cap": "Cap"}, roles)

    assert out["Cap"].shape == (4, 4)
    assert out["Cap"].dtype == np.float64
    np.testing.assert_array_equal(out["Cap"], np.ones((4, 4)))


def test_intensity_picks_second_channel_by_name(tmp_path: Path) -> None:
    """A role can resolve to a non-zero index by name."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5, channel_names=["Cap", "pnorm"])
    data = np.stack(
        [np.full((4, 4), 1.0, dtype=np.float32), np.full((4, 4), 9.0, dtype=np.float32)]
    )
    store.write_array("intensity", data, attrs={"dims": ["C", "H", "W"]})

    roles = {"pnorm": ImageRole(kind="intensity", dtype="float")}
    out = load_layers(h5, {"pnorm": "pnorm"}, roles)

    np.testing.assert_array_equal(out["pnorm"], np.full((4, 4), 9.0))


def test_intensity_fallback_to_decay_sum(tmp_path: Path) -> None:
    """Layer name not in channel_names but in /decay/ → sum over bins."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5, channel_names=["Cap"])
    # Cap matches channel_names but we'll request a different channel
    # that only exists under /decay.
    cap_int = np.ones((4, 4), dtype=np.float32)
    store.write_array("intensity", cap_int[None, ...], attrs={"dims": ["C", "H", "W"]})
    decay = np.full((4, 4, 8), 3.0, dtype=np.uint16)
    store.write_array("decay/Cap2", decay, is_decay=True)

    roles = {"Cap2": ImageRole(kind="intensity", dtype="float")}
    out = load_layers(h5, {"Cap2": "Cap2"}, roles)

    assert out["Cap2"].shape == (4, 4)
    assert out["Cap2"].dtype == np.float64
    # 8 bins * 3 = 24
    np.testing.assert_array_equal(out["Cap2"], np.full((4, 4), 24.0))


def test_intensity_not_found_anywhere(tmp_path: Path) -> None:
    """Name absent from channel_names AND /decay/ raises LayerNotFoundError."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5, channel_names=["Cap"])
    store.write_array("intensity", np.zeros((1, 4, 4), dtype=np.float32),
                      attrs={"dims": ["C", "H", "W"]})

    roles = {"Cap": ImageRole(kind="intensity", dtype="float")}
    with pytest.raises(LayerNotFoundError) as exc_info:
        load_layers(h5, {"Cap": "ghost_channel"}, roles)

    msg = str(exc_info.value)
    assert "Cap" in msg
    assert "ghost_channel" in msg
    assert str(h5) in msg


def test_intensity_no_channel_names_metadata_falls_back_to_decay(
    tmp_path: Path,
) -> None:
    """Store without channel_names: pure /decay/ lookup."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5)  # no channel_names
    decay = np.full((4, 4, 4), 2.0, dtype=np.uint16)
    store.write_array("decay/X", decay, is_decay=True)

    roles = {"X": ImageRole(kind="intensity", dtype="float")}
    out = load_layers(h5, {"X": "X"}, roles)
    np.testing.assert_array_equal(out["X"], np.full((4, 4), 8.0))


# ── kind="mask" ───────────────────────────────────────────────────────


def test_mask_coerced_to_bool(tmp_path: Path) -> None:
    """Mask layer: non-zero pixels become True."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5)
    raw = np.array([[0, 5], [0, 7]], dtype=np.int16)
    store.write_array("masks/m", raw)

    roles = {"m": ImageRole(kind="mask", dtype="binary")}
    out = load_layers(h5, {"m": "m"}, roles)

    assert out["m"].dtype == np.bool_
    np.testing.assert_array_equal(out["m"], np.array([[False, True], [False, True]]))


def test_mask_not_found(tmp_path: Path) -> None:
    h5 = tmp_path / "x.h5"
    _make_store(h5)
    roles = {"m": ImageRole(kind="mask", dtype="binary")}
    with pytest.raises(LayerNotFoundError) as exc_info:
        load_layers(h5, {"m": "missing"}, roles)
    msg = str(exc_info.value)
    assert "missing" in msg
    assert "masks/missing" in msg
    assert str(h5) in msg


# ── kind="label" ──────────────────────────────────────────────────────


def test_label_coerced_to_int32(tmp_path: Path) -> None:
    h5 = tmp_path / "x.h5"
    store = _make_store(h5)
    raw = np.array([[0, 1, 2], [3, 0, 5]], dtype=np.uint16)
    # write_labels enforces int32 conversion on write, but we
    # cross-check the loader's own astype here.
    store.write_array("labels/lbl", raw)

    roles = {"lbl": ImageRole(kind="label", dtype="labels")}
    out = load_layers(h5, {"lbl": "lbl"}, roles)

    assert out["lbl"].dtype == np.int32
    np.testing.assert_array_equal(out["lbl"], raw.astype(np.int32))


def test_label_not_found(tmp_path: Path) -> None:
    h5 = tmp_path / "x.h5"
    _make_store(h5)
    roles = {"lbl": ImageRole(kind="label", dtype="labels")}
    with pytest.raises(LayerNotFoundError) as exc_info:
        load_layers(h5, {"lbl": "ghost"}, roles)
    msg = str(exc_info.value)
    assert "ghost" in msg
    assert "labels/ghost" in msg


# ── ndim validation ───────────────────────────────────────────────────


def test_ndim_mismatch_raises(tmp_path: Path) -> None:
    """A 3D layer assigned to a 2D-only role raises LayerDtypeError."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5)
    # Write a 3D mask (allowed by the store's mask validator only via
    # write_mask but we use write_array directly to bypass shape checks).
    store.write_array("masks/m", np.ones((2, 4, 4), dtype=np.uint8))

    roles = {"m": ImageRole(kind="mask", dtype="binary", ndim=(2,))}
    with pytest.raises(LayerDtypeError) as exc_info:
        load_layers(h5, {"m": "m"}, roles)
    msg = str(exc_info.value)
    assert "m" in msg
    assert "ndim=3" in msg
    assert "(2,)" in msg


def test_ndim_accepts_declared_alternatives(tmp_path: Path) -> None:
    """A role declaring ndim=(2, 3) accepts a 3D array."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5)
    store.write_array("masks/m", np.ones((2, 4, 4), dtype=np.uint8))

    roles = {"m": ImageRole(kind="mask", dtype="binary", ndim=(2, 3))}
    out = load_layers(h5, {"m": "m"}, roles)
    assert out["m"].shape == (2, 4, 4)
    assert out["m"].dtype == np.bool_


# ── Multiple roles in one call ────────────────────────────────────────


def test_multiple_roles_each_dispatched_by_kind(tmp_path: Path) -> None:
    """Reading several roles at once routes each to the right path."""
    h5 = tmp_path / "x.h5"
    store = _make_store(h5, channel_names=["Cap"])
    store.write_array(
        "intensity",
        np.full((1, 4, 4), 5.0, dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    store.write_array("masks/m", np.array([[0, 1], [1, 0]], dtype=np.uint8))
    store.write_array("labels/lbl", np.array([[0, 7], [7, 0]], dtype=np.int32))

    roles = {
        "Cap": ImageRole(kind="intensity", dtype="float"),
        "m": ImageRole(kind="mask", dtype="binary"),
        "lbl": ImageRole(kind="label", dtype="labels"),
    }
    out = load_layers(
        h5,
        {"Cap": "Cap", "m": "m", "lbl": "lbl"},
        roles,
    )
    assert out["Cap"].dtype == np.float64
    assert out["m"].dtype == np.bool_
    assert out["lbl"].dtype == np.int32


def test_empty_layer_map_returns_empty_dict(tmp_path: Path) -> None:
    """Caller can pass an empty layer_map (no roles supplied)."""
    h5 = tmp_path / "x.h5"
    _make_store(h5)
    roles = {"x": ImageRole(kind="intensity", dtype="float")}
    out = load_layers(h5, {}, roles)
    assert out == {}
