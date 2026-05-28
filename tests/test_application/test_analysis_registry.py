"""Tests for the analysis registry + schema validation.

Covers everything in U1's test-scenarios list verbatim. Each test
declares a stub ``Analysis`` subclass inline; the fixture clears the
registry between tests so name collisions don't leak.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from percell4.application.analysis import (
    AnalysisInfo,
    get,
    list_analyses,
    register_analysis,
)
from percell4.application.analysis import registry as registry_mod
from percell4.domain.analysis import (
    Analysis,
    BoolParam,
    GroupState,
    ImageOutput,
    ImageRole,
    IntParam,
    TableOutput,
)

# ── Fixture ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    """Each test starts with an empty registry."""
    registry_mod._REGISTRY.clear()
    yield
    registry_mod._REGISTRY.clear()


# ── Happy paths ────────────────────────────────────────────────────────


def test_happy_path_minimal_registration() -> None:
    @register_analysis("test_stub")
    class Stub(Analysis):
        name = "test_stub"
        display_name = "Test Stub"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        parameters = {"k": IntParam(default=1)}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    assert get("test_stub") is Stub


def test_happy_path_list_analyses_summary_shape() -> None:
    @register_analysis("a1")
    class A1(Analysis):
        name = "a1"
        display_name = "Analysis One"
        description = "First analysis"
        required_inputs = {"cap": ImageRole(kind="intensity", dtype="float")}
        parameters = {"buffer": IntParam(default=4)}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    @register_analysis("a2")
    class A2(Analysis):
        name = "a2"
        display_name = "Analysis Two"
        required_inputs = {
            "m": ImageRole(kind="mask", dtype="binary"),
        }
        optional_inputs = {
            "lbl": ImageRole(kind="label", dtype="labels"),
        }
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    infos = list_analyses()
    assert isinstance(infos, list)
    assert len(infos) == 2
    by_name = {info.name: info for info in infos}
    assert isinstance(by_name["a1"], AnalysisInfo)
    assert by_name["a1"].display_name == "Analysis One"
    assert by_name["a1"].description == "First analysis"
    # Summary strings are non-empty and reference the declared things.
    assert "cap" in by_name["a1"].input_summary
    assert "buffer" in by_name["a1"].param_summary
    # Optional input shows up in the input summary.
    assert "m" in by_name["a2"].input_summary
    assert "lbl" in by_name["a2"].input_summary


def test_happy_path_empty_groups_registration_succeeds() -> None:
    """No input_groups → group_requirement irrelevant."""

    @register_analysis("no_groups")
    class NoGroups(Analysis):
        name = "no_groups"
        display_name = "No Groups"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        # No input_groups declared
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    assert get("no_groups") is NoGroups


def test_happy_path_bool_param_requires_resolves_to_real_role() -> None:
    @register_analysis("requires_ok")
    class RequiresOK(Analysis):
        name = "requires_ok"
        display_name = "Requires OK"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        optional_inputs = {"cp_mask": ImageRole(kind="label", dtype="labels")}
        parameters = {"single_cell": BoolParam(default=False, requires=("cp_mask",))}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    assert get("requires_ok") is RequiresOK


def test_happy_path_bool_param_requires_resolves_to_group_name() -> None:
    @register_analysis("requires_group")
    class RequiresGroup(Analysis):
        name = "requires_group"
        display_name = "Requires Group"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        input_groups = {
            "pbody": {"pbody_mask": ImageRole(kind="mask", dtype="binary")},
        }
        group_requirement = "at_least_one"
        parameters = {"flag": BoolParam(default=False, requires=("pbody",))}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    assert get("requires_group") is RequiresGroup


def test_happy_path_produced_when_callable_stored_unchanged() -> None:
    """Outputs accept a callable produced_when and store it as-is."""

    def gated(g: GroupState, p: dict) -> bool:
        return bool(p.get("export_donuts", False))

    @register_analysis("with_gating")
    class WithGating(Analysis):
        name = "with_gating"
        display_name = "With Gating"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        parameters = {"export_donuts": BoolParam(default=False)}
        outputs = {
            "table": TableOutput(),
            "donut": ImageOutput(dtype="binary", produced_when=gated),
        }

        def run(self, inputs, params):
            return {}

    cls = get("with_gating")
    assert cls.outputs["donut"].produced_when is gated


# ── Error paths ────────────────────────────────────────────────────────


def test_error_duplicate_registration() -> None:
    @register_analysis("dup")
    class First(Analysis):
        name = "dup"
        display_name = "First"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    with pytest.raises(ValueError, match="dup"):

        @register_analysis("dup")
        class Second(Analysis):
            name = "dup"
            display_name = "Second"
            required_inputs = {"y": ImageRole(kind="intensity", dtype="float")}
            outputs = {"table": TableOutput()}

            def run(self, inputs, params):
                return {}


def test_error_role_name_collision_required_vs_group() -> None:
    """A key in required_inputs also appears inside an input_groups group."""
    with pytest.raises(ValueError, match="dup_role"):

        @register_analysis("collide_req_group")
        class Collide(Analysis):
            name = "collide_req_group"
            display_name = "Collide"
            required_inputs = {"dup_role": ImageRole(kind="intensity", dtype="float")}
            input_groups = {
                "g1": {"dup_role": ImageRole(kind="mask", dtype="binary")},
            }
            group_requirement = "at_least_one"
            outputs = {"table": TableOutput()}

            def run(self, inputs, params):
                return {}


def test_error_role_name_collision_across_groups() -> None:
    """Two groups share a role name."""
    with pytest.raises(ValueError, match="shared_role"):

        @register_analysis("collide_group_group")
        class Collide(Analysis):
            name = "collide_group_group"
            display_name = "Collide"
            input_groups = {
                "g1": {"shared_role": ImageRole(kind="mask", dtype="binary")},
                "g2": {"shared_role": ImageRole(kind="mask", dtype="binary")},
            }
            group_requirement = "at_least_one"
            outputs = {"table": TableOutput()}

            def run(self, inputs, params):
                return {}


def test_error_preset_references_unknown_param() -> None:
    with pytest.raises(ValueError, match="ghost"):

        @register_analysis("bad_preset")
        class BadPreset(Analysis):
            name = "bad_preset"
            display_name = "Bad Preset"
            required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
            parameters = {"buffer": IntParam(default=4)}
            presets = {"p1": {"buffer": 8, "ghost": 1.0}}
            outputs = {"table": TableOutput()}

            def run(self, inputs, params):
                return {}


def test_error_invalid_group_requirement() -> None:
    with pytest.raises(ValueError, match="group_requirement"):

        @register_analysis("bad_group_req")
        class BadGroupReq(Analysis):
            name = "bad_group_req"
            display_name = "Bad Group Req"
            input_groups = {
                "g1": {"a": ImageRole(kind="mask", dtype="binary")},
            }
            group_requirement = "some_weird_value"  # type: ignore[assignment]
            outputs = {"table": TableOutput()}

            def run(self, inputs, params):
                return {}


def test_error_bool_param_requires_unknown_role() -> None:
    with pytest.raises(ValueError, match="ghost_role"):

        @register_analysis("bad_requires")
        class BadRequires(Analysis):
            name = "bad_requires"
            display_name = "Bad Requires"
            required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
            parameters = {"flag": BoolParam(default=False, requires=("ghost_role",))}
            outputs = {"table": TableOutput()}

            def run(self, inputs, params):
                return {}


def test_error_get_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get("does_not_exist")


def test_error_analysis_subclass_without_run_raises_not_implemented() -> None:
    """An Analysis subclass that doesn't override run() inherits the
    abstract default, which raises NotImplementedError when called."""

    @register_analysis("no_run")
    class NoRun(Analysis):
        name = "no_run"
        display_name = "No Run"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

    instance = NoRun()
    with pytest.raises(NotImplementedError):
        instance.run({}, {})


# ── Helpers / behavior ─────────────────────────────────────────────────


def test_list_analyses_returns_empty_when_registry_is_empty() -> None:
    assert list_analyses() == []


def test_register_analysis_returns_class() -> None:
    """Decorator returns the class, not None, so subclassing still works."""

    @register_analysis("returns_cls")
    class Returns(Analysis):
        name = "returns_cls"
        display_name = "Returns Class"
        required_inputs = {"x": ImageRole(kind="intensity", dtype="float")}
        outputs = {"table": TableOutput()}

        def run(self, inputs, params):
            return {}

    assert Returns.__name__ == "Returns"
    assert get("returns_cls") is Returns
