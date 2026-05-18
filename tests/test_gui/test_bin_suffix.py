"""Tests for src/percell4/gui/_bin_suffix.py."""

from __future__ import annotations

import pytest

from percell4.gui._bin_suffix import bin_suffix


def test_k_equals_one_returns_unchanged():
    assert bin_suffix("cellpose", 1) == "cellpose"


def test_k_greater_than_one_appends_suffix():
    assert bin_suffix("cellpose", 3) == "cellpose_bin3"
    assert bin_suffix("nuclei", 16) == "nuclei_bin16"


def test_idempotent_strip_and_reapply_same_k():
    """Passing an already-suffixed name with the same k returns the name
    unchanged -- the strip step prevents _bin3_bin3 accretion."""
    assert bin_suffix("cellpose_bin3", 3) == "cellpose_bin3"


def test_idempotent_strip_and_reapply_different_k():
    """Re-applying with a different k strips the old suffix first."""
    assert bin_suffix("cellpose_bin3", 5) == "cellpose_bin5"
    assert bin_suffix("cellpose_bin5", 2) == "cellpose_bin2"


def test_strip_with_k_equals_one_returns_base():
    """At k=1 a trailing suffix is stripped (going from binned view to
    native view in the prompt seed)."""
    assert bin_suffix("cellpose_bin3", 1) == "cellpose"


def test_middle_bin_word_unaffected():
    """The strip regex is anchored to the END of the name; the word
    'bin' elsewhere is not touched."""
    assert bin_suffix("my_bin_name", 3) == "my_bin_name_bin3"
    assert bin_suffix("binary_cellpose", 2) == "binary_cellpose_bin2"


def test_rejects_zero_k():
    with pytest.raises(ValueError):
        bin_suffix("cellpose", 0)


def test_rejects_negative_k():
    with pytest.raises(ValueError):
        bin_suffix("cellpose", -1)


def test_rejects_non_int_k():
    with pytest.raises(ValueError):
        bin_suffix("cellpose", 2.5)


def test_rejects_bool_k():
    """``True`` is technically an int in Python but never a valid bin."""
    with pytest.raises(ValueError):
        bin_suffix("cellpose", True)


def test_empty_name_with_k_one():
    assert bin_suffix("", 1) == ""


def test_empty_name_with_k_greater_one():
    assert bin_suffix("", 3) == "_bin3"  # caller's responsibility to pass a sane base
