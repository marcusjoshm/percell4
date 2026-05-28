"""Tests for ``percell4.domain.analysis`` framework types.

Pins the shape of the declared types (roles, params, outputs) and the
dynamic ``GroupState`` dataclass used by ``produced_when`` callables.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from percell4.domain.analysis import (
    Analysis,
    BoolParam,
    ChoiceParam,
    FloatParam,
    GroupState,
    ImageOutput,
    ImageRole,
    IntParam,
    OutputLike,
    ParamLike,
    TableOutput,
)

# ── ImageRole ───────────────────────────────────────────────────────────


def test_image_role_construction_with_defaults() -> None:
    role = ImageRole(kind="intensity", dtype="float")
    assert role.kind == "intensity"
    assert role.dtype == "float"
    assert role.desc == ""
    assert role.ndim == (2,)


def test_image_role_with_explicit_fields() -> None:
    role = ImageRole(
        kind="mask",
        dtype="binary",
        desc="P-body mask",
        ndim=(2,),
    )
    assert role.kind == "mask"
    assert role.dtype == "binary"
    assert role.desc == "P-body mask"
    assert role.ndim == (2,)


def test_image_role_is_frozen() -> None:
    role = ImageRole(kind="label", dtype="labels")
    with pytest.raises(FrozenInstanceError):
        role.kind = "intensity"  # type: ignore[misc]


# ── Param types ────────────────────────────────────────────────────────


def test_int_param_construction() -> None:
    p = IntParam(default=4, min=0, max=100, desc="Buffer pixels")
    assert p.default == 4
    assert p.min == 0
    assert p.max == 100
    assert p.desc == "Buffer pixels"


def test_int_param_defaults() -> None:
    p = IntParam(default=4)
    assert p.default == 4
    assert p.min is None
    assert p.max is None
    assert p.desc == ""


def test_int_param_is_frozen() -> None:
    p = IntParam(default=4)
    with pytest.raises(FrozenInstanceError):
        p.default = 9  # type: ignore[misc]


def test_float_param_construction() -> None:
    p = FloatParam(default=0.5, min=0.0, max=1.0)
    assert p.default == 0.5
    assert p.min == 0.0
    assert p.max == 1.0


def test_float_param_defaults() -> None:
    p = FloatParam(default=0.5)
    assert p.min is None
    assert p.max is None


def test_float_param_is_frozen() -> None:
    p = FloatParam(default=0.5)
    with pytest.raises(FrozenInstanceError):
        p.default = 0.7  # type: ignore[misc]


def test_bool_param_construction_with_requires() -> None:
    p = BoolParam(default=False, requires=("cp_mask",), desc="Single-cell mode")
    assert p.default is False
    assert p.requires == ("cp_mask",)
    assert p.desc == "Single-cell mode"


def test_bool_param_defaults() -> None:
    p = BoolParam(default=False)
    assert p.requires == ()
    assert p.desc == ""


def test_bool_param_is_frozen() -> None:
    p = BoolParam(default=False)
    with pytest.raises(FrozenInstanceError):
        p.default = True  # type: ignore[misc]


def test_choice_param_construction() -> None:
    p = ChoiceParam(choices=("a", "b", "c"), default="b", desc="Mode")
    assert p.choices == ("a", "b", "c")
    assert p.default == "b"
    assert p.desc == "Mode"


def test_choice_param_is_frozen() -> None:
    p = ChoiceParam(choices=("a", "b"), default="a")
    with pytest.raises(FrozenInstanceError):
        p.default = "b"  # type: ignore[misc]


# ── Outputs ────────────────────────────────────────────────────────────


def test_table_output_construction_with_none_predicate() -> None:
    o = TableOutput(produced_when=None, desc="P-body table")
    assert o.produced_when is None
    assert o.desc == "P-body table"


def test_table_output_construction_with_callable() -> None:
    def pred(groups: GroupState, params: dict) -> bool:
        return True

    o = TableOutput(produced_when=pred)
    assert o.produced_when is pred


def test_table_output_defaults() -> None:
    o = TableOutput()
    assert o.produced_when is None
    assert o.desc == ""


def test_table_output_is_frozen() -> None:
    o = TableOutput()
    with pytest.raises(FrozenInstanceError):
        o.desc = "x"  # type: ignore[misc]


def test_image_output_construction() -> None:
    def pred(groups: GroupState, params: dict) -> bool:
        return bool(params.get("export_donuts", False))

    o = ImageOutput(dtype="binary", produced_when=pred, desc="Donut mask")
    assert o.dtype == "binary"
    assert o.produced_when is pred
    assert o.desc == "Donut mask"


def test_image_output_with_labels_dtype() -> None:
    o = ImageOutput(dtype="labels")
    assert o.dtype == "labels"
    assert o.produced_when is None


def test_image_output_is_frozen() -> None:
    o = ImageOutput(dtype="binary")
    with pytest.raises(FrozenInstanceError):
        o.dtype = "labels"  # type: ignore[misc]


# ── GroupState ─────────────────────────────────────────────────────────


def test_group_state_attribute_lookup() -> None:
    gs = GroupState(pbody_satisfied=True, sg_satisfied=False)
    assert gs.pbody_satisfied is True
    assert gs.sg_satisfied is False


def test_group_state_unknown_attribute_raises() -> None:
    gs = GroupState(pbody_satisfied=True)
    with pytest.raises(AttributeError):
        _ = gs.unknown_thing


def test_group_state_as_dict() -> None:
    gs = GroupState(pbody_satisfied=True, sg_satisfied=False)
    assert gs.as_dict() == {"pbody_satisfied": True, "sg_satisfied": False}


def test_group_state_empty() -> None:
    gs = GroupState()
    assert gs.as_dict() == {}


def test_group_state_role_supplied_flags() -> None:
    gs = GroupState(cap_supplied=True, cp_mask_supplied=False)
    assert gs.cap_supplied is True
    assert gs.cp_mask_supplied is False


def test_group_state_callable_use() -> None:
    """The produced_when callable reads attributes off a GroupState."""
    gs = GroupState(pbody_satisfied=True, sg_satisfied=False)

    def pred_pbody(g: GroupState, p: dict) -> bool:
        return g.pbody_satisfied

    def pred_sg(g: GroupState, p: dict) -> bool:
        return g.sg_satisfied

    assert pred_pbody(gs, {}) is True
    assert pred_sg(gs, {}) is False


# ── Type aliases ───────────────────────────────────────────────────────


def test_param_like_union_includes_all_params() -> None:
    """ParamLike accepts each declared param type at the type level."""
    params: list[ParamLike] = [
        IntParam(default=1),
        FloatParam(default=1.0),
        BoolParam(default=False),
        ChoiceParam(choices=("a",), default="a"),
    ]
    assert len(params) == 4


def test_output_like_union_includes_both_outputs() -> None:
    outputs: list[OutputLike] = [TableOutput(), ImageOutput(dtype="binary")]
    assert len(outputs) == 2


# ── Module __init__ re-exports ─────────────────────────────────────────


def test_domain_analysis_reexports() -> None:
    import percell4.domain.analysis as mod

    for name in (
        "ImageRole",
        "IntParam",
        "FloatParam",
        "BoolParam",
        "ChoiceParam",
        "TableOutput",
        "ImageOutput",
        "GroupState",
        "Analysis",
        "ParamLike",
        "OutputLike",
    ):
        assert hasattr(mod, name), f"percell4.domain.analysis missing {name}"


# ── Analysis base ──────────────────────────────────────────────────────


def test_analysis_base_run_raises_not_implemented() -> None:
    a = Analysis()
    with pytest.raises(NotImplementedError):
        a.run({}, {})


def test_analysis_base_class_attrs_default_empty() -> None:
    """Subclasses inherit empty defaults for every schema attribute."""

    class Stub(Analysis):
        name = "stub"
        display_name = "Stub"

    assert Stub.required_inputs == {}
    assert Stub.input_groups == {}
    assert Stub.optional_inputs == {}
    assert Stub.parameters == {}
    assert Stub.presets == {}
    assert Stub.outputs == {}
    assert Stub.group_requirement == "all"
    assert Stub.dialog_class is None
    assert Stub.version == "1.0.0"
    assert Stub.description == ""
