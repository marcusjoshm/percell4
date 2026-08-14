"""U5/U7 tests: the dataset description in the Data tab.

The description is read-only in Dataset Info; the edit action lives in the
Dataset Management group and opens a dialog. After a save the panel shows
the text it just wrote rather than re-reading the file, because HDF5's
per-process metadata cache can serve a stale value right after a write --
see docs/solutions/logic-errors/in-session-hdf5-staleness-multi-vector-2026-04-30.md.
"""

from __future__ import annotations

import numpy as np
import pytest

from percell4.application.session import Session
from percell4.domain.dataset import DatasetHandle
from percell4.gui.description_dialog import DescriptionResult
from percell4.interfaces.gui.task_panels.data_panel import (
    DataPanel,
    _format_description_lines,
)
from percell4.model import CellDataModel
from percell4.store import DatasetStore

# ── Pure formatter unit tests (no Qt) ─────────────────────────────────


def test_format_description_lines_renders_text_in_full():
    text = "line one\nline two\nline three"
    out = _format_description_lines(text)
    for line in text.splitlines():
        assert line in out
    assert "..." not in out
    assert "…" not in out


def test_format_description_lines_says_not_set_when_absent():
    assert _format_description_lines(None) == "Description: not set"
    assert _format_description_lines("") == "Description: not set"


# ── Qt-backed integration tests ───────────────────────────────────────


def _make_panel(qtbot, tmp_path, description: str | None = None):
    session = Session()
    model = CellDataModel(session=session)

    h5_path = tmp_path / "exp.h5"
    store = DatasetStore(h5_path)
    store.create(
        metadata={
            "channel_names": ["ch00"],
            "native_shape": (32, 32),
            "creation_bin": 1,
        }
    )
    store.write_array("intensity", np.zeros((32, 32), dtype=np.uint16))
    if description is not None:
        store.set_description(description)

    handle = DatasetHandle(
        path=h5_path,
        metadata={
            "channel_names": ["ch00"],
            "segmentation_names": [],
            "mask_names": [],
            "native_shape": (32, 32),
            "creation_bin": 1,
        },
    )
    session.set_dataset(handle)

    statuses: list[str] = []
    panel = DataPanel(
        data_model=model,
        get_store=lambda: store,
        get_viewer_window=lambda: None,
        get_h5_path=lambda: str(h5_path),
        show_status=statuses.append,
    )
    qtbot.addWidget(panel)
    return panel, session, store, statuses


@pytest.fixture
def described_panel(qtbot, tmp_path):
    return _make_panel(qtbot, tmp_path, "HeLa p14, fixed 4% PFA 15min")


@pytest.fixture
def undescribed_panel(qtbot, tmp_path):
    return _make_panel(qtbot, tmp_path)


# ── U5: read-only display ─────────────────────────────────────────────


def test_info_label_shows_description(described_panel):
    panel, _session, _store, _status = described_panel
    panel.refresh_dataset_info()
    assert "HeLa p14, fixed 4% PFA 15min" in panel._info_label.text()


def test_info_label_shows_multi_paragraph_description_in_full(qtbot, tmp_path):
    """AE8: several paragraphs render whole, with no ellipsis."""
    text = (
        "HeLa p14, fixed 4% PFA 15min.\n\n"
        "Permeabilized 0.1% TX-100, blocked 1h.\n\n"
        "2h 10uM drug at 37 °C, 5% CO₂. Dish 3 had a bubble upper-left."
    )
    panel, _session, _store, _status = _make_panel(qtbot, tmp_path, text)
    panel.refresh_dataset_info()
    label_text = panel._info_label.text()
    for line in [ln for ln in text.splitlines() if ln]:
        assert line in label_text
    assert panel._info_label.wordWrap() is True


def test_info_label_says_not_set_when_no_description(undescribed_panel):
    """AE1: 'none set' is distinguishable from the feature being absent."""
    panel, _session, _store, _status = undescribed_panel
    panel.refresh_dataset_info()
    assert "Description: not set" in panel._info_label.text()


def test_clear_ui_drops_the_description(described_panel):
    panel, _session, _store, _status = described_panel
    panel.refresh_dataset_info()
    panel.clear_ui()
    assert "HeLa p14" not in panel._info_label.text()


def test_known_good_description_overrides_the_store_read(described_panel):
    """The staleness bypass: caller-supplied text wins over a fresh read."""
    panel, _session, _store, _status = described_panel
    panel.refresh_dataset_info(description="text the caller just wrote")
    assert "text the caller just wrote" in panel._info_label.text()
    panel.refresh_dataset_info(description=None)
    assert "Description: not set" in panel._info_label.text()


def test_description_read_failure_leaves_the_other_facts_intact(described_panel):
    """A failing description read must not blank the rest of the block."""
    panel, _session, store, _status = described_panel

    class _Boom:
        def __getattr__(self, name):
            if name == "description":
                raise OSError("unreadable")
            return getattr(store, name)

    panel._get_store = lambda: _Boom()
    panel.refresh_dataset_info()
    text = panel._info_label.text()
    assert "exp.h5" in text
    assert "Description: not set" in text


# ── U7: edit action wiring ────────────────────────────────────────────


def test_management_group_covers_the_dataset_not_just_layers(described_panel):
    panel, _session, _store, _status = described_panel
    assert panel._mgmt_group.title() == "Dataset Management"


def test_saving_a_description_updates_the_label_without_a_reload(
    described_panel, monkeypatch,
):
    """The write-then-display path proven in-process (KTD6)."""
    panel, _session, store, _status = described_panel
    monkeypatch.setattr(
        panel, "_prompt_for_description",
        lambda current: DescriptionResult.saved("new notes from the dialog"),
    )
    panel._on_edit_description()
    assert "new notes from the dialog" in panel._info_label.text()
    assert store.description == "new notes from the dialog"


def test_saving_a_description_updates_the_session_snapshot(
    described_panel, monkeypatch,
):
    """DatasetHandle.metadata is a snapshot; push the known-good value in."""
    panel, session, _store, _status = described_panel
    monkeypatch.setattr(
        panel, "_prompt_for_description",
        lambda current: DescriptionResult.saved("snapshot me"),
    )
    panel._on_edit_description()
    assert session.dataset.metadata["description"] == "snapshot me"


def test_clearing_from_the_dialog_reverts_to_not_set(described_panel, monkeypatch):
    """AE4: clearing leaves the dataset in the no-description state."""
    panel, session, store, _status = described_panel
    monkeypatch.setattr(
        panel, "_prompt_for_description", lambda current: DescriptionResult.cleared(),
    )
    panel._on_edit_description()
    assert store.description is None
    assert "Description: not set" in panel._info_label.text()
    assert "description" not in session.dataset.metadata


def test_cancelling_the_dialog_leaves_the_description_alone(
    described_panel, monkeypatch,
):
    panel, _session, store, _status = described_panel
    monkeypatch.setattr(
        panel, "_prompt_for_description", lambda current: DescriptionResult.cancelled(),
    )
    panel._on_edit_description()
    assert store.description == "HeLa p14, fixed 4% PFA 15min"


def test_dialog_is_prefilled_with_the_current_description(
    described_panel, monkeypatch,
):
    seen: list[str | None] = []

    def _capture(current):
        seen.append(current)
        return DescriptionResult.cancelled()

    panel, _session, _store, _status = described_panel
    monkeypatch.setattr(panel, "_prompt_for_description", _capture)
    panel._on_edit_description()
    assert seen == ["HeLa p14, fixed 4% PFA 15min"]


def test_edit_reports_and_opens_nothing_with_no_dataset_loaded(
    described_panel, monkeypatch,
):
    """R13: the action is unavailable rather than opening an empty editor."""
    panel, _session, _store, statuses = described_panel
    panel._get_store = lambda: None
    opened: list[str] = []
    monkeypatch.setattr(
        panel, "_prompt_for_description",
        lambda current: opened.append("opened") or DescriptionResult.cancelled(),
    )
    panel._on_edit_description()
    assert opened == []
    assert statuses and "no dataset" in statuses[-1].lower()


def test_write_failure_reports_and_leaves_the_displayed_text(
    described_panel, monkeypatch,
):
    panel, _session, store, statuses = described_panel
    panel.refresh_dataset_info()
    monkeypatch.setattr(
        panel, "_prompt_for_description",
        lambda current: DescriptionResult.saved("doomed"),
    )

    def _boom(_text):
        raise OSError("disk full")

    monkeypatch.setattr(store, "set_description", _boom)
    panel._on_edit_description()
    assert "HeLa p14, fixed 4% PFA 15min" in panel._info_label.text()
    assert statuses and "disk full" in statuses[-1]


def test_existing_layer_controls_still_work(described_panel):
    """Renaming the group must not disturb what it already contained."""
    panel, _session, _store, _status = described_panel
    assert panel._mgmt_seg_combo is not None
    assert panel._mgmt_mask_combo is not None
    assert panel._mgmt_chan_combo is not None
    panel.refresh_management_combos()
