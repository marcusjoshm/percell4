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
