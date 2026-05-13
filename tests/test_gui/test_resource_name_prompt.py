"""Tests for the shared resource-name prompt helper (U1)."""

from __future__ import annotations

from typing import Any

import pytest

from percell4.gui import _resource_name_prompt as rnp
from percell4.gui._resource_name_prompt import prompt_for_resource_name


def _make_input_dialog_stub(
    responses: list[tuple[str, bool]],
) -> tuple[Any, list[dict]]:
    """Build a stub for QInputDialog.getText that returns each tuple in order.

    Returns (stub, calls) — calls accumulates each invocation's kwargs so
    tests can assert on the prompt's default-text value.
    """
    calls: list[dict] = []
    it = iter(responses)

    def fake(parent, title, label, text=""):  # noqa: ARG001
        calls.append({"title": title, "label": label, "text": text})
        try:
            return next(it)
        except StopIteration as exc:
            raise AssertionError(
                "QInputDialog.getText called more times than the test scripted"
            ) from exc

    return fake, calls


def test_happy_path_returns_typed_name(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    """First-try valid name is returned."""
    fake, calls = _make_input_dialog_stub([("my_resource", True)])
    monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))

    result = prompt_for_resource_name(
        None,
        title="Save Thing",
        label="Name:",
        default="thing",
        existing_names=["other"],
    )
    assert result == "my_resource"
    assert len(calls) == 1
    assert calls[0]["text"] == "thing"  # default seeded


def test_cancel_returns_none(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _make_input_dialog_stub([("anything", False)])  # ok=False
    monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))

    result = prompt_for_resource_name(
        None, title="t", label="l", default="d", existing_names=[]
    )
    assert result is None


def test_empty_name_reprompts_with_original_default(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty submission must re-prompt; the second prompt's default is the
    *original* default, not the blank string the user just submitted."""
    fake, calls = _make_input_dialog_stub(
        [("", True), ("valid_name", True)],
    )
    monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))

    result = prompt_for_resource_name(
        None,
        title="t",
        label="l",
        default="original",
        existing_names=[],
    )
    assert result == "valid_name"
    assert len(calls) == 2
    assert calls[0]["text"] == "original"
    assert calls[1]["text"] == "original"  # not "" — original restored


def test_whitespace_only_name_treated_as_empty(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, calls = _make_input_dialog_stub([("   ", True), ("clean", True)])
    monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))

    result = prompt_for_resource_name(
        None, title="t", label="l", default="orig", existing_names=[]
    )
    assert result == "clean"
    assert len(calls) == 2


def test_collision_warns_and_reprompts_with_colliding_name_as_default(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Colliding submission warns; the next prompt's default is the
    conflicting name (per the Phasor pattern)."""
    fake, calls = _make_input_dialog_stub(
        [("existing", True), ("fresh", True)],
    )
    monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))

    warn_count = {"n": 0}

    def fake_warn(*args, **kwargs):  # noqa: ARG001
        warn_count["n"] += 1

    monkeypatch.setattr(rnp.QMessageBox, "warning", staticmethod(fake_warn))

    result = prompt_for_resource_name(
        None,
        title="t",
        label="l",
        default="seed",
        existing_names=["existing", "other"],
    )
    assert result == "fresh"
    assert warn_count["n"] == 1
    assert calls[0]["text"] == "seed"
    assert calls[1]["text"] == "existing"  # colliding name becomes new default


def test_existing_names_accepts_any_iterable(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callers may pass a list, tuple, set, or any iterable of strings."""
    fake, _ = _make_input_dialog_stub([("name", True)])
    monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))

    for collection in (["a"], ("a",), {"a"}, iter(["a"])):
        fake, _ = _make_input_dialog_stub([("name", True)])
        monkeypatch.setattr(rnp.QInputDialog, "getText", staticmethod(fake))
        assert (
            prompt_for_resource_name(
                None, title="t", label="l", default="d", existing_names=collection
            )
            == "name"
        )
