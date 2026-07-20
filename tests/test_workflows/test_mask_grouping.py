"""Tests for the Qt-free mask-signature grouping helper.

These are pure-logic tests with no Qt dependency, so they run in the local
venv (unlike the GUI dialog tests, which are CI-gated).
"""

from __future__ import annotations

from percell4.workflows.mask_grouping import (
    MaskGroupPlan,
    group_by_mask_signature,
)


def _shape(plans: list[MaskGroupPlan]) -> list[tuple[tuple[str, ...], list[str]]]:
    """Reduce plans to (signature, member_names) tuples for easy assertions."""
    return [(p.signature, p.member_names) for p in plans]


def test_identical_masks_collapse_into_one_group() -> None:
    items = [
        ("A", ["m1", "m2"]),
        ("B", ["m1", "m2"]),
        ("C", ["m1", "m2"]),
    ]
    assert _shape(group_by_mask_signature(items)) == [
        (("m1", "m2"), ["A", "B", "C"]),
    ]


def test_distinct_signatures_ordered_by_first_appearance() -> None:
    items = [
        ("A", ["m1", "m2"]),
        ("B", ["m1"]),
        ("C", ["m1", "m2"]),
    ]
    assert _shape(group_by_mask_signature(items)) == [
        (("m1", "m2"), ["A", "C"]),
        (("m1",), ["B"]),
    ]


def test_signature_is_order_insensitive() -> None:
    # [m2, m1] and [m1, m2] canonicalize to the same signature -> one group.
    items = [
        ("A", ["m2", "m1"]),
        ("B", ["m1", "m2"]),
    ]
    assert _shape(group_by_mask_signature(items)) == [
        (("m1", "m2"), ["A", "B"]),
    ]


def test_duplicate_mask_names_collapse() -> None:
    items = [("A", ["m1", "m1", "m2"])]
    assert _shape(group_by_mask_signature(items)) == [
        (("m1", "m2"), ["A"]),
    ]


def test_no_mask_group_is_separate_and_last() -> None:
    # A no-mask dataset appears FIRST, but its empty-signature group must
    # still sort last, after every non-empty group.
    items = [
        ("N1", []),
        ("A", ["m1"]),
        ("N2", []),
    ]
    assert _shape(group_by_mask_signature(items)) == [
        (("m1",), ["A"]),
        ((), ["N1", "N2"]),
    ]


def test_all_no_mask_yields_single_empty_group() -> None:
    items = [("N1", []), ("N2", [])]
    assert _shape(group_by_mask_signature(items)) == [
        ((), ["N1", "N2"]),
    ]


def test_empty_input_yields_no_groups() -> None:
    assert group_by_mask_signature([]) == []


def test_single_dataset_yields_one_group() -> None:
    assert _shape(group_by_mask_signature([("A", ["m1"])])) == [
        (("m1",), ["A"]),
    ]
