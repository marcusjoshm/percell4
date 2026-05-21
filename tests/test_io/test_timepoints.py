"""Tests for the canonical timepoint token <-> index <-> name helper."""

from __future__ import annotations

import pytest

from percell4.domain.io.timepoints import (
    count_timepoints,
    ordered_timepoint_tokens,
    parse_timepoint_token,
    timepoint_label,
)


def test_timepoint_label_zero_padded():
    assert timepoint_label(0) == "t00"
    assert timepoint_label(7) == "t07"
    assert timepoint_label(12) == "t12"


def test_timepoint_label_negative_raises():
    with pytest.raises(ValueError, match=">= 0"):
        timepoint_label(-1)


def test_parse_timepoint_token_ignores_leading_zeros():
    assert parse_timepoint_token("00") == 0
    assert parse_timepoint_token("01") == 1
    assert parse_timepoint_token("10") == 10


def test_round_trip_token_to_index_to_label():
    # label(parse(token)) reproduces a zero-padded canonical name; the
    # index round-trips exactly.
    for i in range(0, 15):
        assert parse_timepoint_token(timepoint_label(i)[1:]) == i


def test_ordered_timepoint_tokens_numeric_not_lexical():
    # _t2 must precede _t10 — lexical sorting would put "10" before "2".
    assert ordered_timepoint_tokens({"10", "2", "1"}) == ["1", "2", "10"]


def test_ordered_timepoint_tokens_dedupes_and_preserves_strings():
    # Duplicate tokens collapse; original string form (zero-padding) is kept.
    assert ordered_timepoint_tokens(["00", "01", "01", "10"]) == ["00", "01", "10"]


def test_count_timepoints_empty_is_one():
    # No _t token anywhere => single, non-time-lapse acquisition.
    assert count_timepoints([]) == 1
    assert count_timepoints(set()) == 1


def test_count_timepoints_distinct():
    assert count_timepoints({"00", "01", "02"}) == 3
    assert count_timepoints(["00", "00", "01"]) == 2
