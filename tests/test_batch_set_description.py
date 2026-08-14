"""Tests for the batch_set_description use case.

Covers the verb semantics (set / append / clear), per-dataset isolation,
dry-run classification, and the progress callback contract.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.use_cases.batch_set_description import (
    batch_set_description,
    join_description,
)
from percell4.store import DatasetStore


def _make_dataset(path: Path, description: str | None = None) -> DatasetStore:
    store = DatasetStore(path)
    store.create(metadata={"source": "test"})
    store.write_array(
        "intensity",
        np.zeros((8, 8), dtype=np.float32),
        attrs={"dims": ["H", "W"]},
    )
    if description is not None:
        store.set_description(description)
    return store


# ── join_description ──────────────────────────────────────────


def test_join_appends_below_existing_with_blank_line():
    assert join_description("HeLa p14", "2h 10uM drug") == "HeLa p14\n\n2h 10uM drug"


def test_join_onto_none_yields_addition_alone():
    assert join_description(None, "2h 10uM drug") == "2h 10uM drug"


def test_join_onto_blank_yields_addition_alone():
    assert join_description("   \n ", "2h 10uM drug") == "2h 10uM drug"


def test_join_strips_trailing_whitespace_before_separator():
    assert join_description("HeLa p14\n\n\n", "drug") == "HeLa p14\n\ndrug"


# ── Verb semantics ────────────────────────────────────────────


def test_append_keeps_original_first(tmp_path):
    """AE2: append leaves the original text first and adds below it."""
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "HeLa p14, 4% PFA")
    report = batch_set_description([p], verb="append", text="2h 10uM drug")
    assert report.total_succeeded == 1
    assert store.description == "HeLa p14, 4% PFA\n\n2h 10uM drug"


def test_set_replaces_existing(tmp_path):
    """AE3: set leaves only the new text."""
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "HeLa p14, 4% PFA")
    batch_set_description([p], verb="set", text="2h 10uM drug")
    assert store.description == "2h 10uM drug"


def test_append_onto_no_description_has_no_leading_blank_line(tmp_path):
    p = tmp_path / "dish.h5"
    store = _make_dataset(p)
    batch_set_description([p], verb="append", text="2h 10uM drug")
    assert store.description == "2h 10uM drug"


def test_clear_removes_description(tmp_path):
    """AE4: clear returns the dataset to the no-description state."""
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "HeLa p14")
    report = batch_set_description([p], verb="clear")
    assert report.total_succeeded == 1
    assert store.description is None


def test_clear_on_dataset_without_description_is_skipped(tmp_path):
    """Not a failure and not a success -- there was nothing to clear."""
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    report = batch_set_description([p], verb="clear")
    assert report.total_skipped == 1
    assert report.total_succeeded == 0
    assert report.total_failed == 0


def test_multiline_text_round_trips(tmp_path):
    p = tmp_path / "dish.h5"
    store = _make_dataset(p)
    text = "HeLa p14\nfixed 4% PFA 15min\n37 °C"
    batch_set_description([p], verb="set", text=text)
    assert store.description == text


# ── Validation ────────────────────────────────────────────────


def test_unknown_verb_raises(tmp_path):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    with pytest.raises(ValueError, match="verb must be one of"):
        batch_set_description([p], verb="replace", text="x")


@pytest.mark.parametrize("verb", ["set", "append"])
def test_set_and_append_require_text(tmp_path, verb):
    p = tmp_path / "dish.h5"
    _make_dataset(p)
    with pytest.raises(ValueError, match="requires non-empty text"):
        batch_set_description([p], verb=verb, text="   ")


# ── Per-dataset isolation ─────────────────────────────────────


def test_unreadable_file_fails_and_batch_continues(tmp_path):
    """AE5: one bad file does not stop the rest of the batch."""
    good = tmp_path / "good.h5"
    _make_dataset(good)
    bad = tmp_path / "bad.h5"
    bad.write_text("not an hdf5 file")
    missing = tmp_path / "missing.h5"

    report = batch_set_description(
        [bad, good, missing], verb="set", text="notes",
    )
    assert report.total_failed == 2
    assert report.total_succeeded == 1
    assert DatasetStore(good).description == "notes"
    statuses = {r.h5_path.name: r.status for r in report.items}
    assert statuses == {
        "bad.h5": "failed", "good.h5": "succeeded", "missing.h5": "failed",
    }


# ── Dry run ───────────────────────────────────────────────────


def test_dry_run_classifies_like_a_live_run_and_mutates_nothing(tmp_path):
    """AE5: same classifications, byte-identical files."""
    described = tmp_path / "described.h5"
    _make_dataset(described, "existing notes")
    plain = tmp_path / "plain.h5"
    _make_dataset(plain)
    bad = tmp_path / "bad.h5"
    bad.write_text("not an hdf5 file")

    paths = [described, plain, bad]
    before = {p: p.read_bytes() for p in paths}

    dry = batch_set_description(paths, verb="clear", dry_run=True)
    assert {p: p.read_bytes() for p in paths} == before

    live = batch_set_description(paths, verb="clear")
    assert [r.status for r in dry.items] == [r.status for r in live.items]


def test_dry_run_append_does_not_change_description(tmp_path):
    p = tmp_path / "dish.h5"
    store = _make_dataset(p, "HeLa p14")
    batch_set_description([p], verb="append", text="drug", dry_run=True)
    assert store.description == "HeLa p14"


# ── Progress callback ─────────────────────────────────────────


def test_progress_callback_fires_once_per_path_in_order(tmp_path):
    paths = []
    for name in ("a.h5", "b.h5", "c.h5"):
        p = tmp_path / name
        _make_dataset(p)
        paths.append(p)

    seen: list[Path] = []
    batch_set_description(
        paths, verb="set", text="notes", progress_callback=lambda r: seen.append(r.h5_path),
    )
    assert seen == paths
