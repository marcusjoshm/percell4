"""Tests for ``percell4.application.use_cases.run_analysis.run_analysis``.

Each test registers a synthetic stub :class:`Analysis` subclass, builds
a small ``.h5`` via :class:`DatasetStore`, and exercises one branch of
the runner. The registry fixture clears ``_REGISTRY`` between tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.application.analysis import register_analysis
from percell4.application.analysis import registry as registry_mod
from percell4.application.analysis.loader import LayerNotFoundError
from percell4.application.use_cases.run_analysis import run_analysis
from percell4.domain.analysis import (
    Analysis,
    BoolParam,
    GroupState,
    ImageOutput,
    ImageRole,
    IntParam,
    TableOutput,
)
from percell4.store import DatasetStore

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    registry_mod._REGISTRY.clear()
    yield
    registry_mod._REGISTRY.clear()


def _make_h5(
    path: Path,
    *,
    channel_names: list[str] | None = None,
    intensity: np.ndarray | None = None,
    masks: dict[str, np.ndarray] | None = None,
    labels: dict[str, np.ndarray] | None = None,
) -> DatasetStore:
    """Build a synthetic ``.h5`` populated with the given layers."""
    store = DatasetStore(path)
    meta = {"source": "test"}
    if channel_names is not None:
        meta["channel_names"] = channel_names
    store.create(metadata=meta)
    if intensity is not None:
        dims = ["C", "H", "W"] if intensity.ndim == 3 else ["H", "W"]
        store.write_array("intensity", intensity, attrs={"dims": dims})
    for name, arr in (masks or {}).items():
        store.write_array(f"masks/{name}", arr)
    for name, arr in (labels or {}).items():
        store.write_array(f"labels/{name}", arr)
    return store


# ── Happy path: simple stub ───────────────────────────────────────────


def test_happy_path_simple_stub(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        parameters = {"k": IntParam(default=1)}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {
                "table": pd.DataFrame(
                    {"mean": [float(inputs["x"].mean())], "k": [params["k"]]}
                )
            }

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        channel_names=["Cap"],
        intensity=np.full((1, 4, 4), 2.0, dtype=np.float32),
    )

    out = run_analysis("stub", h5, {"x": "Cap"})
    assert "table" in out
    assert isinstance(out["table"], pd.DataFrame)
    assert out["table"]["mean"].iloc[0] == pytest.approx(2.0)
    assert out["table"]["k"].iloc[0] == 1  # default


def test_happy_path_dtype_coercion_for_mask(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"m": ImageRole(kind="mask", dtype="binary")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {
                "table": pd.DataFrame(
                    {
                        "dtype": [str(inputs["m"].dtype)],
                        "n_true": [int(inputs["m"].sum())],
                    }
                )
            }

    h5 = tmp_path / "x.h5"
    _make_h5(h5, masks={"m": np.array([[0, 5], [0, 7]], dtype=np.int16)})
    out = run_analysis("stub", h5, {"m": "m"})
    assert out["table"]["dtype"].iloc[0] == "bool"
    assert out["table"]["n_true"].iloc[0] == 2


def test_happy_path_dtype_labels(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"lbl": ImageRole(kind="label", dtype="labels")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame({"dtype": [str(inputs["lbl"].dtype)]})}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, labels={"l": np.array([[0, 1], [2, 3]], dtype=np.uint16)})
    out = run_analysis("stub", h5, {"lbl": "l"})
    assert out["table"]["dtype"].iloc[0] == "int32"


# ── ndim validation ──────────────────────────────────────────────────


def test_error_ndim_validation_runs_through(tmp_path: Path) -> None:
    """ndim mismatch propagates from the loader."""
    from percell4.application.analysis.loader import LayerDtypeError

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"m": ImageRole(kind="mask", dtype="binary", ndim=(2,))}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, masks={"m": np.ones((2, 4, 4), dtype=np.uint8)})
    with pytest.raises(LayerDtypeError):
        run_analysis("stub", h5, {"m": "m"})


# ── Layer not found ───────────────────────────────────────────────────


def test_error_layer_not_found(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"m": ImageRole(kind="mask", dtype="binary")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5)
    with pytest.raises(LayerNotFoundError):
        run_analysis("stub", h5, {"m": "ghost"})


# ── group_requirement ─────────────────────────────────────────────────


def test_error_exactly_one_both_groups_supplied(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        input_groups = {
            "a": {"a_mask": ImageRole(kind="mask", dtype="binary")},
            "b": {"b_mask": ImageRole(kind="mask", dtype="binary")},
        }
        group_requirement = "exactly_one"
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        masks={
            "am": np.ones((4, 4), dtype=np.uint8),
            "bm": np.ones((4, 4), dtype=np.uint8),
        },
    )
    with pytest.raises(ValueError, match="exactly_one"):
        run_analysis("stub", h5, {"a_mask": "am", "b_mask": "bm"})


def test_error_exactly_one_neither_group_supplied(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        input_groups = {
            "a": {"a_mask": ImageRole(kind="mask", dtype="binary")},
            "b": {"b_mask": ImageRole(kind="mask", dtype="binary")},
        }
        group_requirement = "exactly_one"
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5)
    with pytest.raises(ValueError, match="exactly_one"):
        run_analysis("stub", h5, {})


def test_happy_path_at_least_one_one_group(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        input_groups = {
            "a": {"a_mask": ImageRole(kind="mask", dtype="binary")},
            "b": {"b_mask": ImageRole(kind="mask", dtype="binary")},
        }
        group_requirement = "at_least_one"
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame({"n": [int(inputs["a_mask"].sum())]})}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, masks={"am": np.ones((4, 4), dtype=np.uint8)})
    out = run_analysis("stub", h5, {"a_mask": "am"})
    assert out["table"]["n"].iloc[0] == 16


# ── BoolParam.requires ────────────────────────────────────────────────


def test_error_bool_requires_unmet(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        optional_inputs = {"cp_mask": ImageRole(kind="label", dtype="labels")}
        parameters = {
            "single_cell": BoolParam(default=False, requires=("cp_mask",))
        }
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        channel_names=["Cap"],
        intensity=np.ones((1, 4, 4), dtype=np.float32),
    )
    with pytest.raises(ValueError) as exc_info:
        run_analysis("stub", h5, {"x": "Cap"}, params={"single_cell": True})
    msg = str(exc_info.value)
    assert "single_cell" in msg
    assert "cp_mask" in msg


def test_bool_requires_false_does_not_check(tmp_path: Path) -> None:
    """BoolParam=False does NOT trigger the requires check."""

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        optional_inputs = {"cp_mask": ImageRole(kind="label", dtype="labels")}
        parameters = {
            "single_cell": BoolParam(default=False, requires=("cp_mask",))
        }
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame({"sc": [params["single_cell"]]})}

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        channel_names=["Cap"],
        intensity=np.ones((1, 4, 4), dtype=np.float32),
    )
    # Default-False param: no cp_mask required, no error.
    out = run_analysis("stub", h5, {"x": "Cap"})
    assert out["table"]["sc"].iloc[0] is np.False_ or out["table"]["sc"].iloc[0] is False


# ── preset + params strict policy ─────────────────────────────────────


def test_error_preset_plus_explicit_params(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        parameters = {"buffer": IntParam(default=4)}
        presets = {"p1": {"buffer": 8}}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame({"b": [params["buffer"]]})}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(ValueError, match="preset"):
        run_analysis(
            "stub", h5, {"x": "Cap"}, params={"buffer": 9}, preset="p1"
        )


def test_happy_path_preset_applied(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        parameters = {"buffer": IntParam(default=4), "other": IntParam(default=2)}
        presets = {"p1": {"buffer": 8}}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame({"b": [params["buffer"]], "o": [params["other"]]})}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    out = run_analysis("stub", h5, {"x": "Cap"}, preset="p1")
    # Preset value used; unspecified parameter takes its declared default.
    assert out["table"]["b"].iloc[0] == 8
    assert out["table"]["o"].iloc[0] == 2


def test_error_unknown_preset_raises_key_error(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(KeyError, match="ghost_preset"):
        run_analysis("stub", h5, {"x": "Cap"}, preset="ghost_preset")


def test_error_params_unknown_key(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        parameters = {"buffer": IntParam(default=4)}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(ValueError, match="ghost"):
        run_analysis("stub", h5, {"x": "Cap"}, params={"ghost": 1})


# ── layer_map validation ──────────────────────────────────────────────


def test_error_layer_map_unknown_role(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(ValueError, match="unknown role"):
        run_analysis("stub", h5, {"x": "Cap", "wat": "Cap"})


def test_error_required_role_missing(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame()}

    h5 = tmp_path / "x.h5"
    _make_h5(h5)
    with pytest.raises(ValueError, match="required role"):
        run_analysis("stub", h5, {})


# ── Optional input absent ─────────────────────────────────────────────


def test_optional_input_absent_keeps_run_inputs_clean(tmp_path: Path) -> None:
    """An optional role not in layer_map does not appear in inputs."""

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        optional_inputs = {"cp_mask": ImageRole(kind="label", dtype="labels")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {
                "table": pd.DataFrame({"has_cp": ["cp_mask" in inputs]})
            }

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    out = run_analysis("stub", h5, {"x": "Cap"})
    assert out["table"]["has_cp"].iloc[0] == False  # noqa: E712


# ── produced_when filtering ───────────────────────────────────────────


def test_produced_when_filters_outputs(tmp_path: Path) -> None:
    """An output gated by produced_when is allowed to be missing if its
    gate is False, and present if its gate is True."""

    def x_supplied(g: GroupState, p: dict) -> bool:
        return g.x_supplied

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        optional_inputs = {
            "x": ImageRole(kind="intensity", dtype="float"),
            "y": ImageRole(kind="intensity", dtype="float"),
        }
        outputs = {
            "out_x": TableOutput(produced_when=x_supplied),
        }

        def run(self, inputs, params):
            # Only return out_x when x was supplied.
            if "x" in inputs:
                return {"out_x": pd.DataFrame({"v": [float(inputs["x"].mean())]})}
            return {}

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        channel_names=["Cap"],
        intensity=np.full((1, 4, 4), 3.0, dtype=np.float32),
    )

    # x supplied: out_x returned, runner accepts.
    out = run_analysis("stub", h5, {"x": "Cap"})
    assert "out_x" in out

    # x NOT supplied: stub returns {}, runner accepts (out_x not produced).
    out2 = run_analysis("stub", h5, {})
    assert out2 == {}


def test_produced_when_unproduced_output_returned_raises(tmp_path: Path) -> None:
    """Returning an output whose gate is False is an error."""

    def x_supplied(g: GroupState, p: dict) -> bool:
        return g.x_supplied

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        optional_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"out_x": TableOutput(produced_when=x_supplied)}

        def run(self, inputs, params):
            # Misbehaved: returns out_x even though x not supplied.
            return {"out_x": pd.DataFrame({"v": [1.0]})}

    h5 = tmp_path / "x.h5"
    _make_h5(h5)
    with pytest.raises(ValueError, match="undeclared"):
        run_analysis("stub", h5, {})


# ── Undeclared output ─────────────────────────────────────────────────


def test_error_run_returns_undeclared_output(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"surprise": 42}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(ValueError, match="surprise"):
        run_analysis("stub", h5, {"x": "Cap"})


# ── Output type validation ────────────────────────────────────────────


def test_error_table_output_wrong_type(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            # Returns a dict instead of a DataFrame.
            return {"table": {"not": "a frame"}}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(ValueError, match="DataFrame"):
        run_analysis("stub", h5, {"x": "Cap"})


def test_error_image_output_wrong_type(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"img": ImageOutput(dtype="binary")}

        def run(self, inputs, params):
            return {"img": [[1, 0], [0, 1]]}  # list, not ndarray

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    with pytest.raises(ValueError, match="ndarray"):
        run_analysis("stub", h5, {"x": "Cap"})


def test_happy_path_image_output(tmp_path: Path) -> None:
    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"img": ImageOutput(dtype="binary")}

        def run(self, inputs, params):
            return {"img": np.ones((4, 4), dtype=bool)}

    h5 = tmp_path / "x.h5"
    _make_h5(h5, channel_names=["Cap"], intensity=np.ones((1, 4, 4), np.float32))
    out = run_analysis("stub", h5, {"x": "Cap"})
    assert isinstance(out["img"], np.ndarray)


# ── Unknown analysis name ─────────────────────────────────────────────


def test_error_unknown_analysis_name(tmp_path: Path) -> None:
    h5 = tmp_path / "x.h5"
    _make_h5(h5)
    with pytest.raises(KeyError):
        run_analysis("ghost_analysis", h5, {})


# ══════════════════════════════════════════════════════════════════════
# U5 — PerParticleDonut end-to-end integration
# ══════════════════════════════════════════════════════════════════════
#
# These tests do NOT use the autouse ``_clear_registry`` fixture above
# (it clears the registry between tests). They explicitly re-import the
# concrete module to repopulate ``per_particle_donut`` before each run.
# The fixture above clears the registry for the test it runs in; the
# subsequent test re-imports to restore. This keeps the U1+U3 stub tests
# isolated from the live ``per_particle_donut`` registration.


def _reregister_per_particle_donut() -> None:
    """Force-register ``PerParticleDonut`` into the (cleared) registry.

    The autouse fixture clears ``_REGISTRY`` between tests; the U5
    integration tests re-import the module's decorator side effect by
    invoking the decorator directly. Using ``importlib.reload`` here
    would re-run the module-level ``@register_analysis("per_particle_donut")``,
    which works because ``_REGISTRY`` was just cleared.
    """
    import importlib

    import percell4.application.analysis.modules.per_particle_donut as mod

    importlib.reload(mod)


def _build_per_particle_h5(
    h5_path: Path,
    *,
    cap: np.ndarray,
    pnorm: np.ndarray | None = None,
    sgnorm: np.ndarray | None = None,
    pbody_mask: np.ndarray | None = None,
    sg_mask: np.ndarray | None = None,
    cp_mask: np.ndarray | None = None,
) -> None:
    """Construct a ``.h5`` populated with the supplied per-particle layers.

    ``channel_names`` orders the intensity channels as
    ``["Cap", "pnorm", "sgnorm"]`` (filtered to those actually supplied).
    """
    channels: list[tuple[str, np.ndarray]] = [("Cap", cap.astype(np.float32))]
    if pnorm is not None:
        channels.append(("pnorm", pnorm.astype(np.float32)))
    if sgnorm is not None:
        channels.append(("sgnorm", sgnorm.astype(np.float32)))
    intensity = np.stack([arr for _, arr in channels], axis=0)
    names = [n for n, _ in channels]

    masks: dict[str, np.ndarray] = {}
    if pbody_mask is not None:
        masks["pbody"] = pbody_mask.astype(np.uint8)
    if sg_mask is not None:
        masks["sg"] = sg_mask.astype(np.uint8)

    labels = {"cells": cp_mask.astype(np.uint16)} if cp_mask is not None else {}

    _make_h5(
        h5_path,
        channel_names=names,
        intensity=intensity,
        masks=masks,
        labels=labels,
    )


def _toy_pbody_arrays(
    shape: tuple[int, int] = (32, 32),
    centers: tuple[tuple[int, int], ...] = ((8, 8), (24, 24)),
    radius: int = 3,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate a small synthetic P-body fixture.

    Returns ``(cap, pnorm, pbody_mask)`` arrays. The Cap and pnorm
    images have a flat noise-free floor + bright blobs at ``centers``;
    the mask labels the same blobs.
    """
    from skimage.draw import disk

    h, w = shape
    cap = np.full((h, w), 1000.0, dtype=np.float64)
    pnorm = np.full((h, w), 1200.0, dtype=np.float64)
    mask = np.zeros((h, w), dtype=np.uint8)
    for (r, c) in centers:
        rr, cc = disk((r, c), radius, shape=shape)
        cap[rr, cc] += 6000.0
        pnorm[rr, cc] += 4500.0
        mask[rr, cc] = 1
    return cap.astype(np.float32), pnorm.astype(np.float32), mask


def test_per_particle_donut_e2e_pbody_only_with_preset(tmp_path: Path) -> None:
    """Happy path: P-body-only synthetic h5 + ``m7g-cap-v1`` preset.

    Loader resolves ``cap`` → channel ``Cap``, ``pbody_mask`` → mask
    ``pbody``, ``pnorm`` → channel ``pnorm`` via the layer_map. The
    framework returns ``pbody_table`` as a DataFrame populated with
    the analysis's row schema (``pbody_id``, ``pbody_area_px``,
    ``cap_pbody_mean``, ...).
    """
    _reregister_per_particle_donut()

    cap, pnorm, pbody_mask = _toy_pbody_arrays()
    h5 = tmp_path / "ds.h5"
    _build_per_particle_h5(
        h5, cap=cap, pnorm=pnorm, pbody_mask=pbody_mask,
    )

    out = run_analysis(
        "per_particle_donut",
        h5,
        layer_map={"cap": "Cap", "pbody_mask": "pbody", "pnorm": "pnorm"},
        preset="m7g-cap-v1",
    )

    assert set(out) == {"pbody_table"}, (
        f"unexpected outputs returned: {sorted(out)}"
    )
    df = out["pbody_table"]
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2, f"expected 2 P-body rows, got {len(df)}"
    expected_cols = {
        "pbody_id",
        "pbody_area_px",
        "donut_area_px",
        "bg_value",
        "cap_pbody_raw_mean",
        "cap_dilute_raw_mean",
        "cap_pbody_mean",
        "cap_dilute_mean",
        "pnorm_pbody_mean",
        "pnorm_dilute_mean",
        "cap_pbody_over_dilute",
        "pnorm_pbody_over_dilute",
        "cap_over_pnorm_pbody",
        "cap_over_pnorm_dilute",
    }
    missing = expected_cols - set(df.columns)
    assert not missing, f"missing expected columns: {sorted(missing)}"


def test_per_particle_donut_e2e_strict_preset_plus_params(
    tmp_path: Path,
) -> None:
    """Strict mode: passing both preset AND params raises ValueError."""
    _reregister_per_particle_donut()

    cap, pnorm, pbody_mask = _toy_pbody_arrays()
    h5 = tmp_path / "ds.h5"
    _build_per_particle_h5(
        h5, cap=cap, pnorm=pnorm, pbody_mask=pbody_mask,
    )

    with pytest.raises(ValueError, match="preset"):
        run_analysis(
            "per_particle_donut",
            h5,
            layer_map={"cap": "Cap", "pbody_mask": "pbody", "pnorm": "pnorm"},
            params={"buffer": 9},
            preset="m7g-cap-v1",
        )


def test_per_particle_donut_e2e_no_preset_no_params_defaults(
    tmp_path: Path,
) -> None:
    """No preset and no params: framework fills in declared defaults."""
    _reregister_per_particle_donut()

    cap, pnorm, pbody_mask = _toy_pbody_arrays()
    h5 = tmp_path / "ds.h5"
    _build_per_particle_h5(
        h5, cap=cap, pnorm=pnorm, pbody_mask=pbody_mask,
    )

    out = run_analysis(
        "per_particle_donut",
        h5,
        layer_map={"cap": "Cap", "pbody_mask": "pbody", "pnorm": "pnorm"},
    )

    assert "pbody_table" in out
    df = out["pbody_table"]
    assert isinstance(df, pd.DataFrame)
    # Default ``min_size=10`` filters out particles smaller than 10
    # pixels. The synthetic blobs (radius=3 disks) span ~25 pixels each,
    # so both should survive.
    assert len(df) == 2


def test_per_particle_donut_e2e_both_branches_with_export_donuts(
    tmp_path: Path,
) -> None:
    """Both P-body and SG branches active + ``export_donuts=True``.

    Verifies the ``produced_when`` gating: with both groups satisfied
    and ``export_donuts=True`` in params, all four outputs are produced
    (``pbody_table``, ``sg_table``, ``pbody_donut_mask``,
    ``sg_donut_mask``).
    """
    _reregister_per_particle_donut()

    from skimage.draw import disk

    shape = (48, 48)
    h, w = shape
    cap = np.full(shape, 1000.0, dtype=np.float64)
    pnorm = np.full(shape, 1200.0, dtype=np.float64)
    sgnorm = np.full(shape, 1200.0, dtype=np.float64)
    pbody = np.zeros(shape, dtype=np.uint8)
    sg = np.zeros(shape, dtype=np.uint8)

    # P-body blobs (small, radius=3)
    for (r, c) in [(10, 10), (10, 40)]:
        rr, cc = disk((r, c), 3, shape=shape)
        cap[rr, cc] += 6000.0
        pnorm[rr, cc] += 4500.0
        pbody[rr, cc] = 1

    # SG blobs (larger, radius=6, non-overlapping with P-bodies)
    rr, cc = disk((38, 25), 6, shape=shape)
    cap[rr, cc] += 6000.0
    sgnorm[rr, cc] += 4500.0
    sg[rr, cc] = 1

    h5 = tmp_path / "ds.h5"
    _build_per_particle_h5(
        h5,
        cap=cap,
        pnorm=pnorm,
        sgnorm=sgnorm,
        pbody_mask=pbody,
        sg_mask=sg,
    )

    out = run_analysis(
        "per_particle_donut",
        h5,
        layer_map={
            "cap": "Cap",
            "pbody_mask": "pbody",
            "pnorm": "pnorm",
            "sg_mask": "sg",
            "sgnorm": "sgnorm",
        },
        params={
            "buffer": 4,
            "donut": 5,
            "bg_mode": "donut",
            "bg_value": 1,
            "exclude_cap_zero": True,
            "min_size": 4,
            "bgsub_k": 2.5,
            "no_bgsub": True,
            "single_cell": False,
            "export_donuts": True,
        },
    )

    assert set(out) == {
        "pbody_table",
        "sg_table",
        "pbody_donut_mask",
        "sg_donut_mask",
    }, f"unexpected outputs returned: {sorted(out)}"
    assert isinstance(out["pbody_table"], pd.DataFrame)
    assert isinstance(out["sg_table"], pd.DataFrame)
    assert isinstance(out["pbody_donut_mask"], np.ndarray)
    assert isinstance(out["sg_donut_mask"], np.ndarray)
    assert out["pbody_donut_mask"].dtype == np.uint8
    assert out["sg_donut_mask"].dtype == np.uint8
    assert len(out["pbody_table"]) == 2
    assert len(out["sg_table"]) == 1


def test_per_particle_donut_e2e_missing_required_role(tmp_path: Path) -> None:
    """``cap`` is required; omitting it raises before the loader runs."""
    _reregister_per_particle_donut()

    cap, pnorm, pbody_mask = _toy_pbody_arrays()
    h5 = tmp_path / "ds.h5"
    _build_per_particle_h5(
        h5, cap=cap, pnorm=pnorm, pbody_mask=pbody_mask,
    )

    with pytest.raises(ValueError, match="required role"):
        run_analysis(
            "per_particle_donut",
            h5,
            layer_map={"pbody_mask": "pbody", "pnorm": "pnorm"},
        )


def test_per_particle_donut_e2e_no_group_satisfied(tmp_path: Path) -> None:
    """``group_requirement='at_least_one'`` fires when neither branch is supplied."""
    _reregister_per_particle_donut()

    cap, _pnorm, _ = _toy_pbody_arrays()
    h5 = tmp_path / "ds.h5"
    _build_per_particle_h5(h5, cap=cap)

    with pytest.raises(ValueError, match="at_least_one"):
        run_analysis(
            "per_particle_donut",
            h5,
            layer_map={"cap": "Cap"},
        )


# ── CLI parity (R7 / R16 cross-system numeric-parity check) ───────────


def test_per_particle_donut_cli_parity_group_a(tmp_path: Path) -> None:
    """Framework ``pbody_table`` matches the CLI's combined_pbody.csv.

    Same fixture inputs, same parameters, same expected numeric output.
    The CLI is invoked in a subprocess to generate the reference CSV
    (the committed expected CSV in
    ``tests/fixtures/per_particle/group_a_expected/`` was generated
    with ``--no-bgsub``; we reuse those params here so the comparison
    is exact).

    Parity rule per plan: drop the CLI's ``group`` column, sort rows by
    ``pbody_id``, integer columns exact-equal, float columns
    ``np.allclose(rtol=1e-10)``.
    """
    import tifffile

    _reregister_per_particle_donut()

    fixture_dir = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "per_particle"
        / "group_a"
    )
    expected_csv = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "per_particle"
        / "group_a_expected"
        / "combined_pbody.csv"
    )
    repo_root = Path(__file__).resolve().parent.parent.parent
    cli_script = repo_root / "per_particle_analysis.py"

    # Build an h5 from the fixture TIFFs so the framework + CLI see
    # the same pixel data.
    cap = tifffile.imread(fixture_dir / "sample1_Cap.tif")
    pnorm = tifffile.imread(fixture_dir / "sample1_pnorm.tif")
    pbody_mask = tifffile.imread(fixture_dir / "sample1_P-body_mask.tif")

    h5 = tmp_path / "group_a.h5"
    _build_per_particle_h5(
        h5, cap=cap, pnorm=pnorm, pbody_mask=pbody_mask,
    )

    # Run the framework with the exact params the committed expected
    # CSV was produced with (--no-bgsub + ORIGINAL_DEFAULTS).
    fw_out = run_analysis(
        "per_particle_donut",
        h5,
        layer_map={"cap": "Cap", "pbody_mask": "pbody", "pnorm": "pnorm"},
        params={"no_bgsub": True},
    )
    fw_df = fw_out["pbody_table"]
    assert isinstance(fw_df, pd.DataFrame)

    # The CLI's expected combined_pbody.csv was committed in U4.
    # Read it and drop the path-derived ``group`` column.
    cli_df = pd.read_csv(expected_csv)
    if "group" in cli_df.columns:
        cli_df = cli_df.drop(columns=["group"])

    # Stable sort by pbody_id (per-row identity stable across both paths
    # when arrays are loaded with matching shape + dtype + same params).
    fw_df = fw_df.sort_values("pbody_id").reset_index(drop=True)
    cli_df = cli_df.sort_values("pbody_id").reset_index(drop=True)

    assert list(fw_df.columns) == list(cli_df.columns), (
        f"column mismatch\nfw:  {list(fw_df.columns)}\n"
        f"cli: {list(cli_df.columns)}"
    )
    assert len(fw_df) == len(cli_df), (
        f"row count mismatch: fw={len(fw_df)} cli={len(cli_df)}"
    )

    for col in fw_df.columns:
        a = fw_df[col]
        e = cli_df[col]
        if pd.api.types.is_integer_dtype(e):
            pd.testing.assert_series_equal(
                a.astype(e.dtype),
                e,
                check_dtype=False,
                check_exact=True,
                obj=f"column {col!r}",
            )
        elif pd.api.types.is_float_dtype(e):
            np.testing.assert_allclose(
                a.values,
                e.values,
                rtol=1e-10,
                atol=0.0,
                equal_nan=True,
                err_msg=f"float column {col!r} mismatch",
            )
        else:
            pd.testing.assert_series_equal(
                a,
                e,
                check_dtype=False,
                check_exact=True,
                obj=f"column {col!r}",
            )

    # Smoke-check that the CLI script still exists at the expected
    # location (a guard against U4 ever moving it without updating
    # this test's reference path). The subprocess CLI invocation
    # itself is exercised by
    # ``tests/test_scripts/test_per_particle_regression.py``; we keep
    # this parity test purely numeric (committed CSV vs framework) so
    # it stays fast and deterministic.
    assert cli_script.exists()


# ── layer_map threading (U2) ──────────────────────────────────────────


def test_run_receives_layer_map_when_declared(tmp_path: Path) -> None:
    """A run() that names ``layer_map`` receives the resolved role->layer map."""
    captured: dict[str, dict[str, str] | None] = {}

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params, *, layer_map=None):
            captured["layer_map"] = layer_map
            return {"table": pd.DataFrame({"n": [1]})}

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        channel_names=["Cap"],
        intensity=np.full((1, 4, 4), 2.0, dtype=np.float32),
    )

    run_analysis("stub", h5, {"x": "Cap"})
    assert captured["layer_map"] == {"x": "Cap"}


def test_plain_run_signature_not_passed_layer_map(tmp_path: Path) -> None:
    """A plain ``(inputs, params)`` run() still runs and is not passed layer_map.

    Regression guard on ``_accepted_progress_kwargs``: forwarding
    ``layer_map`` unconditionally would raise ``TypeError`` here.
    """

    @register_analysis("stub")
    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {"table": pd.DataFrame({"n": [1]})}

    h5 = tmp_path / "x.h5"
    _make_h5(
        h5,
        channel_names=["Cap"],
        intensity=np.full((1, 4, 4), 2.0, dtype=np.float32),
    )

    out = run_analysis("stub", h5, {"x": "Cap"})
    assert "table" in out
