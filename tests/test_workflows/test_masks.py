"""Tests for the Qt-free mask-intersection helper.

Pure logic, no Qt dependency, so these run in the local venv (unlike the
config-dialog GUI tests, which are CI-gated).
"""

from __future__ import annotations

from percell4.workflows.masks import intersect_masks


def test_common_masks_across_datasets() -> None:
    assert intersect_masks([["a", "b", "c"], ["a", "b"], ["b", "a", "d"]]) == ["a", "b"]


def test_no_common_masks_returns_empty() -> None:
    assert intersect_masks([["a", "b"], ["c", "d"]]) == []


def test_single_dataset_returns_its_masks_sorted_deduped() -> None:
    assert intersect_masks([["b", "a", "a"]]) == ["a", "b"]


def test_empty_input_returns_empty() -> None:
    assert intersect_masks([]) == []


def test_one_empty_dataset_collapses_intersection() -> None:
    assert intersect_masks([["a", "b"], []]) == []


def test_result_is_sorted_and_order_insensitive() -> None:
    assert intersect_masks([["c", "a", "b"], ["b", "c", "a"]]) == ["a", "b", "c"]
