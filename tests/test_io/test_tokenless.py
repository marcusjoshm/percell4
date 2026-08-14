"""Tests for tokenless channel-name derivation."""

from __future__ import annotations

import re

import pytest

from percell4.domain.io.models import _MAX_PATTERN_LENGTH, TokenConfig
from percell4.domain.io.tokenless import build_channel_pattern, derive_channel_names

# The motivating flat folder: 3 datasets x 4 channels (one channel is the
# multi-underscore "SG_mask").
_PREFIXES = [
    "CellProfiler_U2OS_60min_As_3x4",
    "CellProfiler_U2OS_90min_Washout_2",
    "CellProfiler_U2OS_90min_Washout_4x4",
]
_CHANNELS = ["cells", "DNA", "G3BP1", "SG_mask"]


def _motivating_stems() -> list[str]:
    return [f"{p}_{c}" for p in _PREFIXES for c in _CHANNELS]


# ---------------------------------------------------------------------------
# derive_channel_names
# ---------------------------------------------------------------------------


def test_derive_motivating_vocabulary():
    """The 3-dataset / 4-channel set yields exactly the four channel names."""
    names, _ = derive_channel_names(_motivating_stems())
    assert set(names) == {"cells", "DNA", "G3BP1", "SG_mask"}


def test_derive_keeps_multi_underscore_channel_whole():
    """SG_mask is one channel, not 'mask' with the file orphaned into '..._SG'."""
    names, channel_of = derive_channel_names(_motivating_stems())
    assert "SG_mask" in names
    assert "mask" not in names
    mask_stem = "CellProfiler_U2OS_60min_As_3x4_SG_mask"
    assert channel_of[mask_stem] == "SG_mask"


def test_derive_single_dataset_single_channel():
    """One prefix, one channel -> that one channel."""
    names, channel_of = derive_channel_names(["Exp_A_DNA"])
    assert names == ["DNA"]
    assert channel_of["Exp_A_DNA"] == "DNA"


def test_derive_does_not_merge_equal_sized_prefixed_datasets():
    """Two equally rich datasets whose names share a prefix stay distinct.

    'Washout_2' and 'Washout_20' both have {cells, DNA}; the consistency rule's
    'richer ancestor' guard must NOT fold '..._20' into '..._2'.
    """
    stems = [
        "Exp_Washout_2_cells",
        "Exp_Washout_2_DNA",
        "Exp_Washout_20_cells",
        "Exp_Washout_20_DNA",
    ]
    names, channel_of = derive_channel_names(stems)
    assert set(names) == {"cells", "DNA"}
    # No file's channel was corrupted into a prefix-bearing name.
    assert channel_of["Exp_Washout_20_cells"] == "cells"


def test_derive_empty():
    assert derive_channel_names([]) == ([], {})


def test_derive_pattern_round_trip_reproduces_grouping():
    """Synthesized pattern, run back through re.search, reproduces the channel map
    (discovery <-> importer parity) and strips to the same group prefix."""
    stems = _motivating_stems()
    names, channel_of = derive_channel_names(stems)
    pattern = build_channel_pattern(names)
    for stem in stems:
        m = re.search(pattern, stem)
        assert m is not None, stem
        assert m.group(1) == channel_of[stem]
        # Stripping the match yields the shared dataset prefix.
        group = re.sub(pattern, "", stem)
        assert group in _PREFIXES


# ---------------------------------------------------------------------------
# build_channel_pattern
# ---------------------------------------------------------------------------


def test_pattern_longest_first_and_end_anchored():
    pattern = build_channel_pattern(["mask", "SG_mask"])
    # SG_mask (longer) must precede standalone mask in the alternation so it wins.
    assert pattern == r"_(SG_mask|mask)$"
    assert pattern.endswith("$")
    # A stem ending in _SG_mask captures SG_mask, not mask.
    m = re.search(pattern, "foo_bar_SG_mask")
    assert m is not None and m.group(1) == "SG_mask"


def test_pattern_escapes_metacharacters():
    pattern = build_channel_pattern(["C1+", "DNA"])
    m = re.search(pattern, "sample_C1+")
    assert m is not None and m.group(1) == "C1+"


def test_pattern_matches_exactly_one_alternative():
    pattern = build_channel_pattern(_CHANNELS)
    for ch in _CHANNELS:
        m = re.search(pattern, f"prefix_{ch}")
        assert m is not None and m.group(1) == ch


def test_pattern_is_valid_tokenconfig_channel():
    """The synthesized pattern satisfies TokenConfig's capture-group contract."""
    pattern = build_channel_pattern(_CHANNELS)
    cfg = TokenConfig(channel=pattern, timepoint=None, z_slice=None, tile=None)
    assert cfg.channel == pattern


def test_pattern_empty_vocabulary_raises():
    with pytest.raises(ValueError):
        build_channel_pattern([])


def test_pattern_too_large_raises_clear_error():
    huge = [f"channelname{i:03d}" for i in range(40)]
    with pytest.raises(ValueError, match="too large"):
        build_channel_pattern(huge)
    # Sanity: the guard fires before TokenConfig's own length check.
    assert len(build_channel_pattern(_CHANNELS)) <= _MAX_PATTERN_LENGTH
