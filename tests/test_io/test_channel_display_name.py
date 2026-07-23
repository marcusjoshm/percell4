"""Tests for the canonical channel display/storage name helper."""

from __future__ import annotations

import pytest

from percell4.domain.io.naming import channel_display_name


@pytest.mark.parametrize(
    "token,expected",
    [
        ("", "ch0"),       # single unnamed channel
        ("00", "ch00"),    # numeric legacy form, zero-padded
        ("0", "ch0"),
        ("1", "ch1"),
        ("01", "ch01"),
        ("12", "ch12"),
    ],
)
def test_numeric_tokens_keep_ch_prefix(token, expected):
    """Numeric/empty tokens are byte-identical to the historical f'ch{token}'."""
    assert channel_display_name(token) == expected


@pytest.mark.parametrize(
    "token",
    ["DNA", "cells", "G3BP1", "SG_mask", "GFP", "MixedCase", "with_underscore"],
)
def test_name_tokens_returned_verbatim(token):
    """Name tokens are returned unchanged — no 'ch' prefix, no case change."""
    assert channel_display_name(token) == token


def test_all_digit_name_token_documented_behavior():
    """A purely-numeric name token gets the ch prefix (documented limitation)."""
    assert channel_display_name("488") == "ch488"
