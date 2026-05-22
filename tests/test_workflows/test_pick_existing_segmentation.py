"""pick_existing_segmentation selection rule (U13, pure)."""

from __future__ import annotations

from percell4.workflows.phases import pick_existing_segmentation


def test_empty_returns_none():
    assert pick_existing_segmentation([]) is None


def test_single_segmentation_picked():
    assert pick_existing_segmentation(["cellpose"]) == "cellpose"


def test_prefers_tracked_layer():
    assert (
        pick_existing_segmentation(["cellpose", "cellpose_tracked"])
        == "cellpose_tracked"
    )


def test_prefers_tracked_regardless_of_order():
    assert (
        pick_existing_segmentation(["zzz_tracked", "aaa", "manual"])
        == "zzz_tracked"
    )


def test_multiple_untracked_picks_lexicographically_first():
    assert (
        pick_existing_segmentation(["cellpose", "cellpose_bin3", "manual"])
        == "cellpose"
    )


def test_multiple_tracked_picks_first_sorted():
    assert (
        pick_existing_segmentation(["b_tracked", "a_tracked"]) == "a_tracked"
    )
