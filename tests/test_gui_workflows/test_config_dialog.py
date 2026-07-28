"""Tests for :class:`WorkflowConfigDialog`.

These drive the dialog programmatically (setting widgets, reading
internals) rather than through the file dialogs, which are impractical
to test without GUI automation. The internal helpers ``_add_h5_paths``
and ``_add_pending`` are exercised directly to simulate user adds.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from qtpy.QtWidgets import QMessageBox

from percell4.gui.workflows.single_cell.config_dialog import (
    WorkflowConfigDialog,
    _PendingDataset,
)
from percell4.gui.workflows.single_cell.round_card import (
    METHOD_AUTO_EXTRACT,
    METHOD_GROUPED,
)
from percell4.store import DatasetStore
from percell4.workflows.models import (
    DatasetSource,
    EdgeMode,
    ThresholdAlgorithm,
)

# ── Fixtures ────────────────────────────────────────────────────────────


def _make_h5(tmp_path: Path, name: str, channels: list[str]) -> Path:
    """Create a minimal h5 file with the given channel names in metadata."""
    path = tmp_path / f"{name}.h5"
    store = DatasetStore(path)
    store.create(metadata={"channel_names": channels})
    # Also write a tiny intensity dataset so the file looks real.
    arr = np.ones((len(channels), 16, 16), dtype=np.float32)
    store.write_array("intensity", arr, attrs={"dims": ["C", "H", "W"]})
    return path


@pytest.fixture
def dialog(qtbot):
    dlg = WorkflowConfigDialog()
    qtbot.addWidget(dlg)
    return dlg


@pytest.fixture
def h5_ds1(tmp_path) -> Path:
    return _make_h5(tmp_path, "DS1", ["GFP", "RFP", "DAPI"])


@pytest.fixture
def h5_ds2(tmp_path) -> Path:
    return _make_h5(tmp_path, "DS2", ["GFP", "RFP"])


@pytest.fixture
def h5_ds3_outlier(tmp_path) -> Path:
    return _make_h5(tmp_path, "DS3", ["Cy5", "Hoechst"])


# ── Dialog construction ─────────────────────────────────────────────────


def test_dialog_initial_state(dialog):
    assert dialog.windowTitle() == "Single-cell thresholding analysis workflow"
    assert dialog._pending_datasets == []
    assert len(dialog._round_cards) == 0
    # Start disabled with empty state
    assert dialog._start_btn.isEnabled() is False
    assert dialog.workflow_config is None


def test_dialog_modal(dialog):
    assert dialog.isModal()


# ── Dataset picker ──────────────────────────────────────────────────────


def test_add_h5_file_populates_channel_names(dialog, h5_ds1):
    added, skipped = dialog._add_h5_paths([h5_ds1])
    assert added == 1
    assert skipped == []
    assert len(dialog._pending_datasets) == 1
    pd = dialog._pending_datasets[0]
    assert pd.source is DatasetSource.H5_EXISTING
    assert pd.channel_names == ["GFP", "RFP", "DAPI"]
    assert pd.h5_path == h5_ds1.resolve()


def test_add_h5_file_twice_dedupes(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    added, skipped = dialog._add_h5_paths([h5_ds1])
    assert added == 0
    assert len(skipped) == 1
    assert "duplicate" in skipped[0]
    assert len(dialog._pending_datasets) == 1


def test_add_two_different_h5_files(dialog, h5_ds1, h5_ds2):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    assert len(dialog._pending_datasets) == 2


def test_add_h5_disambiguates_display_names(dialog, tmp_path):
    # Two files with the same stem under different directories.
    sub1 = tmp_path / "a"
    sub1.mkdir()
    sub2 = tmp_path / "b"
    sub2.mkdir()
    p1 = _make_h5(sub1, "sample", ["GFP"])
    p2 = _make_h5(sub2, "sample", ["GFP"])

    dialog._add_h5_paths([p1, p2])
    names = [pd.display_name for pd in dialog._pending_datasets]
    assert names[0] == "sample"
    assert names[1] == "sample (2)"


def test_add_nonexistent_h5_is_skipped(dialog, tmp_path):
    bogus = tmp_path / "does_not_exist.h5"
    added, skipped = dialog._add_h5_paths([bogus])
    assert added == 0
    assert skipped
    assert "not a file" in skipped[0]


def test_add_h5_with_no_channel_names_metadata(dialog, tmp_path):
    """A real .h5 without channel_names metadata is still accepted, but
    lands with an empty channel list — so it will be flagged by the
    intersection check later."""
    path = tmp_path / "no_channels.h5"
    store = DatasetStore(path)
    store.create(metadata={})
    added, _ = dialog._add_h5_paths([path])
    assert added == 1
    assert dialog._pending_datasets[0].channel_names == []


def test_remove_selected_dataset(dialog, h5_ds1, h5_ds2):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    # Select the first tree row
    dialog._dataset_tree.setCurrentItem(dialog._dataset_tree.topLevelItem(0))
    dialog._on_remove_dataset()
    assert len(dialog._pending_datasets) == 1
    assert dialog._pending_datasets[0].display_name == "DS2"


# ── Cellpose group ──────────────────────────────────────────────────────


def test_cellpose_defaults(dialog):
    # Inference controls now live in the shared CellposeSettingsForm.
    s = dialog._cp_form.settings()
    assert s.model == "cpsam_v2"
    assert s.diameter == 300.0
    assert s.gpu is True
    assert s.min_size == 15
    assert s.saturation_pct == 1.0
    assert s.blur_sigma == 0.0
    # Surface-specific naming control stays on the dialog.
    assert dialog._cp_seg_name.text() == "cp_mask"


def test_cellpose_default_config_unchanged_by_extraction(dialog):
    """Characterization (R6): default form state builds the historical
    CellposeSettings plus the new blur_sigma=0.0 (which keeps batch runs
    byte-identical)."""
    from percell4.workflows.models import CellposeSettings

    assert dialog._cp_form.settings() == CellposeSettings(
        model="cpsam_v2",
        diameter=300.0,
        gpu=True,
        flow_threshold=0.4,
        cellprob_threshold=0.0,
        min_size=15,
        saturation_pct=1.0,
        blur_sigma=0.0,
    )


# ── Rounds table ────────────────────────────────────────────────────────


def test_add_round_populates_row(dialog, h5_ds1, h5_ds2):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    assert len(dialog._round_cards) == 1
    data = dialog._round_cards[0].to_dict()
    assert data["name"] == "round_1"
    assert data["channel"] in ("GFP", "RFP")  # from intersection
    assert data["metric"] == "median_intensity"
    assert data["algorithm"] == "gmm"
    assert data["gmm_max"] == 10
    assert data["sigma"] == 0.0


def test_add_round_with_no_datasets_shows_placeholder(dialog):
    dialog._on_add_round()
    ch_combo = dialog._round_cards[0]._channel
    assert ch_combo.isEnabled() is False
    assert ch_combo.currentText() == "(add datasets first)"


def test_remove_round(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    assert len(dialog._round_cards) == 2
    dialog._on_card_remove(dialog._round_cards[0])
    assert len(dialog._round_cards) == 1


def test_round_name_invalid_regex_colors_red(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    name = dialog._round_cards[0]._name
    name.setText("has space")
    assert "must match" in name.toolTip()
    assert not dialog._round_cards[0].name_is_valid()


def test_round_name_valid_regex_clears_tooltip(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    name = dialog._round_cards[0]._name
    name.setText("has space")
    assert "must match" in name.toolTip()
    name.setText("ok_name")
    assert "must match" not in name.toolTip()
    assert dialog._round_cards[0].name_is_valid()


def test_algo_toggles_enabled_spinboxes(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    algo_combo = dialog._round_cards[0]._algorithm
    gmm_spin = dialog._round_cards[0]._gmm_max
    kmeans_spin = dialog._round_cards[0]._kmeans_k

    # Default: GMM → gmm_max enabled, kmeans_k disabled
    assert gmm_spin.isEnabled() is True
    assert kmeans_spin.isEnabled() is False

    algo_combo.setCurrentText(ThresholdAlgorithm.KMEANS.value)
    assert gmm_spin.isEnabled() is False
    assert kmeans_spin.isEnabled() is True


# ── Method picker: adaptive sigma clipping (U5) ─────────────────────────


def _make_h5_with_pixel_size(tmp_path, name, channels, pixel_size_um=0.12):
    path = tmp_path / f"{name}.h5"
    store = DatasetStore(path)
    meta = {"channel_names": channels}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)
    store.write_array(
        "intensity", np.ones((len(channels), 16, 16), dtype=np.float32),
        attrs={"dims": ["C", "H", "W"]},
    )
    return path


def test_method_default_builds_legacy_round(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].adaptive_clip is None


def test_gui_matching_method_builds_auto_extract_round(dialog, h5_ds1):
    """The Adaptive Local Thresholding method builds an AutoExtractSettings round —
    the same detector the GUI panel runs — never an adaptive_clip sentinel."""
    assert METHOD_AUTO_EXTRACT == "Adaptive Local Thresholding"

    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(2.0)
    unit = dialog._round_cards[0]._size_unit
    unit.setCurrentIndex(unit.findData("px"))
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].auto_extract is not None  # the GUI's two-pass detector
    assert rounds[0].adaptive_clip is None  # cards never build the single-window one


def test_method_switch_toggles_columns_and_retains_values(dialog, h5_ds1):
    """Switching Method shows one sub-group and hides the other, retaining values."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    method = card._method
    gmm = card._gmm_max
    gmm.setValue(7)

    method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert card._alc_box.isVisibleTo(card)
    assert card._grouped_box.isHidden()
    assert gmm.value() == 7

    method.setCurrentText(METHOD_GROUPED)
    assert card._grouped_box.isVisibleTo(card)
    assert card._alc_box.isHidden()
    assert gmm.value() == 7


def test_min_particle_size_builds_into_round(dialog, h5_ds1):
    """Setting Min size + Min unit on a row builds a ThresholdingRound carrying
    the size filter — on any method (here the default Grouped Otsu)."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._min_size.setValue(25.0)
    unit = dialog._round_cards[0]._min_size_unit
    unit.setCurrentIndex(unit.findData("um2"))
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].min_particle_size == 25.0
    assert rounds[0].min_particle_size_unit == "um2"


def test_min_particle_size_defaults_to_three_px(dialog, h5_ds1):
    """A fresh round defaults to a 3 px² minimum particle area."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].min_particle_size == 3.0
    assert rounds[0].min_particle_size_unit == "px"


def test_min_particle_size_survives_row_reorder(dialog, h5_ds1):
    """Min size + unit round-trip through the read/write used by row reordering."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    dialog._round_cards[0]._min_size.setValue(12.0)
    u0 = dialog._round_cards[0]._min_size_unit
    u0.setCurrentIndex(u0.findData("um2"))
    dialog._reorder_cards(0, 1)  # the row's data moves to index 1
    moved = dialog._round_cards[1].to_dict()
    assert moved["min_particle_size"] == 12.0
    assert moved["min_particle_size_unit"] == "um2"


def test_datasets_without_pixel_size_flags_missing(dialog, tmp_path):
    with_ps = _make_h5_with_pixel_size(tmp_path, "HasPS", ["GFP"], pixel_size_um=0.12)
    no_ps = _make_h5_with_pixel_size(tmp_path, "NoPS", ["GFP"], pixel_size_um=None)
    dialog._add_h5_paths([with_ps, no_ps])
    missing = dialog._datasets_without_pixel_size(dialog._pending_datasets)
    assert missing == ["NoPS"]


# ── Method picker: auto-extraction + guided CNR (U5) ────────────────────


def test_auto_extract_method_builds_round(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(0.36)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].auto_extract is not None
    assert rounds[0].auto_extract.smallest_particle_um == 0.36
    # The other method sentinels stay clear (no mutual-exclusion trip).
    assert rounds[0].adaptive_clip is None
    assert rounds[0].puncta is None
    assert rounds[0].iterative_otsu is None


def test_auto_extract_smallest_zero_means_autodetect(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(0.0)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].auto_extract is not None
    assert rounds[0].auto_extract.smallest_particle_um is None  # 0 → auto-detect


def test_auto_extract_enables_dmin_and_sigma(dialog, h5_ds1):
    """Auto-extract shows d_min + σ (both live) and allows d_min=0 (auto-detect)."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    card._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert card._d_min.isEnabled() is True
    assert card._sigma.isEnabled() is True
    assert card._sigma.value() == 1.0
    assert card._d_min.minimum() == 0.0


def test_alc_sigma_seeded_to_one_on_entry_and_wired_to_presmooth(dialog, h5_ds1):
    """Entering the ALC method seeds σ=1.0 (from the grouped-Otsu 0); σ then controls
    the detector presmooth."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    assert card._sigma.value() == 0.0
    card._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert card._sigma.value() == 1.0
    card._sigma.setValue(2.0)
    card._d_min.setValue(0.36)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].auto_extract.presmooth_sigma_px == 2.0


def test_grouped_sigma_not_seeded(dialog, h5_ds1):
    """A Grouped-Otsu row keeps σ=0 (the seeding fires only for ALC methods)."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    sigma = dialog._round_cards[0]._sigma
    assert sigma.value() == 0.0
    assert sigma.isEnabled() is True  # σ is live for grouped (pre-threshold smoothing)


def test_cnr_checkbox_enabled_only_on_alc_rows(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    assert card._cnr_on.isEnabled() is False  # Grouped Otsu (default)
    card._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert card._cnr_on.isEnabled() is True


def test_cnr_threshold_gated_by_checkbox(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    method = dialog._round_cards[0]._method
    cnr = dialog._round_cards[0]._cnr_on
    thr = dialog._round_cards[0]._cnr_threshold
    method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert thr.isEnabled() is False  # off until the box is checked
    cnr.setChecked(True)
    assert thr.isEnabled() is True
    cnr.setChecked(False)
    assert thr.isEnabled() is False


def test_cnr_builds_settings_on_alc_round(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(0.36)
    dialog._round_cards[0]._cnr_on.setChecked(True)
    dialog._round_cards[0]._cnr_threshold.setValue(7.0)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].cnr_classify is not None
    assert rounds[0].cnr_classify.threshold == 7.0


def test_cnr_forced_gated_by_split_checkbox(dialog, h5_ds1):
    """GMM 2-pop is enabled only when CNR split is on, on an ALC row."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    method = dialog._round_cards[0]._method
    cnr = dialog._round_cards[0]._cnr_on
    forced = dialog._round_cards[0]._cnr_forced
    method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert forced.isEnabled() is False  # off until CNR split is checked
    cnr.setChecked(True)
    assert forced.isEnabled() is True
    cnr.setChecked(False)
    assert forced.isEnabled() is False


def test_cnr_forced_overrides_and_greys_threshold(dialog, h5_ds1):
    """Checking GMM 2-pop greys the CNR threshold (it is overridden)."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    cnr = dialog._round_cards[0]._cnr_on
    thr = dialog._round_cards[0]._cnr_threshold
    forced = dialog._round_cards[0]._cnr_forced
    cnr.setChecked(True)
    assert thr.isEnabled() is True
    forced.setChecked(True)
    assert thr.isEnabled() is False  # overridden → greyed
    forced.setChecked(False)
    assert thr.isEnabled() is True


def test_cnr_forced_builds_forced_settings(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(0.36)
    dialog._round_cards[0]._cnr_on.setChecked(True)
    dialog._round_cards[0]._cnr_forced.setChecked(True)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].cnr_classify is not None
    assert rounds[0].cnr_classify.forced is True


def test_cnr_forced_cleared_when_split_unchecked(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    card._method.setCurrentText(METHOD_AUTO_EXTRACT)
    card._cnr_on.setChecked(True)
    card._cnr_forced.setChecked(True)
    card._cnr_on.setChecked(False)
    assert card._cnr_forced.isChecked() is False


def test_cnr_cleared_when_switching_to_grouped(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    card._method.setCurrentText(METHOD_AUTO_EXTRACT)
    card._cnr_on.setChecked(True)
    card._method.setCurrentText(METHOD_GROUPED)
    assert card._cnr_on.isChecked() is False
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].cnr_classify is None


def test_round_row_swap_preserves_auto_extract_and_cnr(dialog, h5_ds1):
    """The new Smallest / CNR fields survive a row reorder (read/write symmetry)."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(0.36)
    dialog._round_cards[0]._cnr_on.setChecked(True)
    dialog._round_cards[0]._cnr_threshold.setValue(7.0)
    dialog._round_cards[0]._cnr_forced.setChecked(True)

    dialog._reorder_cards(0, 1)

    assert (
        dialog._round_cards[1]._method.currentText()
        == METHOD_AUTO_EXTRACT
    )
    assert dialog._round_cards[1]._d_min.value() == 0.36
    assert dialog._round_cards[1]._cnr_on.isChecked() is True
    assert dialog._round_cards[1]._cnr_threshold.value() == 7.0
    assert dialog._round_cards[1]._cnr_forced.isChecked() is True


# ── d_min / Smallest px-µm Unit column (U10) ────────────────────────────


def _set_unit(dialog, row, code):
    combo = dialog._round_cards[row]._size_unit
    combo.setCurrentIndex(combo.findData(code))


def test_smallest_defaults_to_two_px(dialog, h5_ds1):
    """R: the Smallest Particle Diameter defaults to 2 px (value + unit)."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].auto_extract.smallest_particle_um == 2.0
    assert rounds[0].auto_extract.smallest_particle_unit == "px"


def test_size_unit_shown_only_on_alc_rows(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    card = dialog._round_cards[0]
    assert card._alc_box.isHidden()  # Grouped Otsu: ALC fields (unit) hidden
    card._method.setCurrentText(METHOD_AUTO_EXTRACT)
    assert card._alc_box.isVisibleTo(card)
    assert card._size_unit.isEnabled() is True


def test_auto_extract_px_unit_builds_round(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(3.0)
    _set_unit(dialog, 0, "px")
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[0].auto_extract.smallest_particle_um == 3.0
    assert rounds[0].auto_extract.smallest_particle_unit == "px"


def test_px_unit_round_not_flagged_by_pixel_size_preflight(dialog, tmp_path):
    """A px-unit auto-extract round on a dataset with NO pixel size builds a config
    (the µm-only pre-flight must not fire)."""
    no_ps = _make_h5_with_pixel_size(tmp_path, "NoPS", ["GFP"], pixel_size_um=None)
    dialog._add_h5_paths([no_ps])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(3.0)
    _set_unit(dialog, 0, "px")
    dialog._output_edit.setText(str(tmp_path / "runs"))
    cfg = dialog._try_build_config()
    assert cfg is not None  # px round NOT blocked by the µm-only pixel-size pre-flight
    assert cfg.thresholding_rounds[0].auto_extract.smallest_particle_unit == "px"


def test_um_unit_round_flagged_by_pixel_size_preflight(dialog, tmp_path, monkeypatch):
    """A µm-unit auto-extract round on a dataset with NO pixel size IS blocked."""
    no_ps = _make_h5_with_pixel_size(tmp_path, "NoPS", ["GFP"], pixel_size_um=None)
    dialog._add_h5_paths([no_ps])
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    dialog._round_cards[0]._d_min.setValue(0.4)
    _set_unit(dialog, 0, "um")
    dialog._output_edit.setText(str(tmp_path / "runs"))
    warnings = []
    monkeypatch.setattr(dialog, "_warn", lambda msg, *a, **k: warnings.append(msg))
    cfg = dialog._try_build_config()
    assert cfg is None
    assert any("pixel size" in w for w in warnings)


def test_size_unit_survives_row_swap(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    dialog._round_cards[0]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    _set_unit(dialog, 0, "px")
    dialog._reorder_cards(0, 1)
    moved = dialog._round_cards[1]._size_unit
    assert moved.currentData() == "px"


def test_size_unit_survives_method_toggle_and_swap(dialog, h5_ds1):
    """Unit=px on an ALC card survives toggling to Grouped Otsu (hides the ALC group),
    a reorder, and toggling back to ALC."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    method0 = dialog._round_cards[0]._method
    method0.setCurrentText(METHOD_AUTO_EXTRACT)
    _set_unit(dialog, 0, "px")
    method0.setCurrentText(METHOD_GROUPED)
    dialog._reorder_cards(0, 1)
    dialog._round_cards[1]._method.setCurrentText(METHOD_AUTO_EXTRACT)
    rounds = dialog._rounds_from_cards(dialog._current_intersection())
    assert rounds[1].auto_extract is not None
    assert rounds[1].auto_extract.smallest_particle_unit == "px"


# ── Column picker ───────────────────────────────────────────────────────


def test_seg_channel_combo_populates_from_intersection(dialog, h5_ds1, h5_ds2):
    """Seg channel combo should show GFP and RFP (the intersection)."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    combo = dialog._cp_seg_channel
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "GFP" in items
    assert "RFP" in items
    # DAPI is NOT in the intersection (only in DS1)
    assert "DAPI" not in items
    assert combo.isEnabled()


def test_seg_channel_combo_placeholder_without_datasets(dialog):
    combo = dialog._cp_seg_channel
    assert not combo.isEnabled()
    assert combo.currentText() == "(add datasets first)"


def test_csv_export_selection_persists(dialog, h5_ds1, h5_ds2):
    """Setting channel + metric selections updates the summary label."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._selected_csv_channels = {"GFP"}
    dialog._selected_csv_metrics = {"mean_intensity", "area"}
    dialog._update_csv_summary()
    text = dialog._csv_summary_label.text()
    assert "1 channel" in text
    assert "2 metric" in text


def test_csv_export_prunes_invalid_channels(dialog, h5_ds1, h5_ds2):
    """If datasets change and a channel drops out of the intersection,
    the selection set is pruned."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._selected_csv_channels = {"GFP", "DAPI"}  # DAPI not in intersection
    dialog._refresh_column_picker()
    # DAPI should be pruned because it's not in the intersection
    assert "DAPI" not in dialog._selected_csv_channels
    assert "GFP" in dialog._selected_csv_channels


def test_build_selected_csv_columns_cross_product(dialog, h5_ds1, h5_ds2):
    """The cross-product builder should produce the expected column names."""
    from percell4.workflows.models import ThresholdAlgorithm, ThresholdingRound

    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._selected_csv_channels = {"GFP"}
    dialog._selected_csv_metrics = {"mean_intensity"}

    rounds = [
        ThresholdingRound(
            name="R1",
            channel="GFP",
            metric="mean_intensity",
            algorithm=ThresholdAlgorithm.GMM,
        )
    ]
    cols = dialog._build_selected_csv_columns(["GFP", "RFP"], rounds)

    # Whole-cell: GFP_mean_intensity (only GFP selected, not RFP)
    assert "GFP_mean_intensity" in cols
    assert "RFP_mean_intensity" not in cols
    # Group column
    assert "group_R1" in cols
    # Per-round inside columns only — the "_out_<round>" variants are
    # intentionally NOT emitted in this workflow (iteration-3 feedback).
    assert "GFP_mean_intensity_in_R1" in cols
    assert "GFP_mean_intensity_out_R1" not in cols
    assert not any("_out_" in c for c in cols)
    # RFP was NOT selected so its in column should be absent too
    assert "RFP_mean_intensity_in_R1" not in cols


# ── Start button / accept ───────────────────────────────────────────────


def test_start_disabled_without_datasets(dialog):
    dialog._on_add_round()  # round added but no dataset
    # After add_round, _update_start_enabled runs
    assert dialog._start_btn.isEnabled() is False


def test_start_disabled_without_rounds(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    assert dialog._start_btn.isEnabled() is False


def test_start_enabled_with_datasets_and_rounds(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    assert dialog._start_btn.isEnabled() is True


def test_accept_without_output_folder_warns(dialog, h5_ds1, tmp_path):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._output_edit.setText("")
    with patch.object(QMessageBox, "warning") as warn_mock:
        dialog._on_start_clicked()
    warn_mock.assert_called_once()
    assert "output parent" in warn_mock.call_args[0][2].lower()


def test_accept_with_valid_config_builds_workflow_config(
    dialog, h5_ds1, h5_ds2, tmp_path
):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))

    dialog._on_start_clicked()

    cfg = dialog.workflow_config
    assert cfg is not None
    assert len(cfg.datasets) == 2
    assert len(cfg.thresholding_rounds) == 1
    assert cfg.output_parent == tmp_path / "runs"
    assert cfg.cellpose.model == "cpsam_v2"
    # Seg channel is auto-selected from the first channel in the intersection
    assert cfg.seg_channel_name in ("GFP", "RFP")


def test_accept_with_outlier_dataset_prompts_user(
    dialog, h5_ds1, h5_ds2, h5_ds3_outlier, tmp_path
):
    """DS3 has zero channel overlap with DS1/DS2 — intersection is empty,
    so validation shows a warning box and refuses to accept."""
    dialog._add_h5_paths([h5_ds1, h5_ds2, h5_ds3_outlier])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))

    with patch.object(QMessageBox, "exec_", return_value=QMessageBox.Cancel):
        dialog._on_start_clicked()

    assert dialog.workflow_config is None  # dialog did NOT accept


def test_accept_with_invalid_round_name_warns(dialog, h5_ds1, tmp_path):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    name_item = dialog._round_cards[0]._name
    name_item.setText("has space")
    dialog._output_edit.setText(str(tmp_path / "runs"))

    with patch.object(QMessageBox, "warning") as warn_mock:
        dialog._on_start_clicked()
    warn_mock.assert_called_once()
    assert dialog.workflow_config is None


def test_accept_saves_output_parent_to_qsettings(
    dialog, h5_ds1, tmp_path
):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    target = tmp_path / "saved_runs"
    dialog._output_edit.setText(str(target))
    dialog._on_start_clicked()

    from qtpy.QtCore import QSettings
    qs = QSettings("LeeLabPerCell4", "PerCell4")
    assert qs.value("single_cell_threshold_workflow/output_parent", "", type=str) == str(target)


# ── _PendingDataset helper ──────────────────────────────────────────────


def test_pending_dataset_dedupe_key_differs_for_h5_vs_tiff(tmp_path):
    p = tmp_path / "x.h5"
    p.write_bytes(b"")
    h5 = _PendingDataset(
        display_name="x",
        source=DatasetSource.H5_EXISTING,
        h5_path=p,
        channel_names=[],
    )
    tiff = _PendingDataset(
        display_name="x",
        source=DatasetSource.TIFF_PENDING,
        h5_path=p,
        channel_names=[],
        compress_plan={"source_dir": "/tmp", "files": []},
    )
    assert h5.dedupe_key() != tiff.dedupe_key()


def test_pending_dataset_to_entry_round_trips_channels(tmp_path):
    p = tmp_path / "x.h5"
    p.write_bytes(b"")
    pd = _PendingDataset(
        display_name="x",
        source=DatasetSource.H5_EXISTING,
        h5_path=p,
        channel_names=["GFP", "RFP"],
    )
    entry = pd.to_entry()
    assert entry.name == "x"
    assert entry.channel_names == ["GFP", "RFP"]
    assert entry.source is DatasetSource.H5_EXISTING


# ── U3: Edge-mode selector ─────────────────────────────────────────────


def test_edge_mode_combo_defaults_to_size_normalized_cohort(dialog):
    """Default edge mode is the size-normalized cohort (workflow's
    primary phase-separation use case)."""
    assert (
        dialog._edge_mode.currentData()
        is EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT
    )


def test_edge_mode_combo_has_three_options(dialog):
    """U3: all three EdgeMode values are exposed in the combo."""
    values = [
        dialog._edge_mode.itemData(i)
        for i in range(dialog._edge_mode.count())
    ]
    assert set(values) == {
        EdgeMode.EXCLUDE,
        EdgeMode.INCLUDE_AS_NORMAL,
        EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT,
    }


def test_edge_mode_carried_into_built_config(dialog, h5_ds1, tmp_path):
    """U3: chosen edge_mode propagates through _try_build_config to WorkflowConfig."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    # Set combo to size-normalized cohort
    target = EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT
    idx = next(
        i
        for i in range(dialog._edge_mode.count())
        if dialog._edge_mode.itemData(i) is target
    )
    dialog._edge_mode.setCurrentIndex(idx)

    dialog._on_start_clicked()

    cfg = dialog.workflow_config
    assert cfg is not None
    assert cfg.edge_mode is EdgeMode.INCLUDE_AS_SIZE_NORMALIZED_COHORT


# ── U3: Dilute sub-panel ───────────────────────────────────────────────


def _dilute_group(dialog):
    """The dilute group is the QGroupBox that holds _dilute_mask_name."""
    return dialog._dilute_mask_name.parent()


def test_dilute_group_unchecked_by_default(dialog):
    """Dilute generation is OFF by default.

    It is an interactive per-dataset phase, so defaulting it on made every run
    pause for it unless the researcher remembered to untick it. The mask name
    stays pre-filled so ticking the group is all that is needed — an empty name
    is a required field and would block Start.
    """
    group = _dilute_group(dialog)
    assert group.isCheckable() is True
    assert group.isChecked() is False
    assert dialog._dilute_mask_name.text() == "dilute"
    # Default field values per the requested config.
    assert dialog._dilute_dilation_px.value() == 5
    assert dialog._dilute_metric.currentText() == "median_intensity"
    assert dialog._dilute_gmm_max.value() == 10
    assert dialog._dilute_sigma.value() == 0.0


def test_dilute_disabled_produces_none_dilute_settings(dialog, h5_ds1, tmp_path):
    """Unchecking the dilute group → cfg.dilute_settings is None."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    _dilute_group(dialog).setChecked(False)  # explicitly disable
    dialog._on_start_clicked()
    cfg = dialog.workflow_config
    assert cfg is not None
    assert cfg.dilute_settings is None


def test_dilute_enabled_builds_full_settings(dialog, h5_ds1, h5_ds2, tmp_path):
    """U3: dilute group checked + filled → cfg.dilute_settings is populated."""
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    # Group is checked by default; just confirm the fields.
    _dilute_group(dialog).setChecked(True)
    dialog._dilute_mask_name.setText("dilute")
    dialog._dilute_dilation_px.setValue(5)
    # The channel combo is populated by the dataset add → seg/dilute refresh
    # Pick whatever is at index 0 (should be GFP from the intersection).
    dialog._dilute_channel.setCurrentIndex(0)

    dialog._on_start_clicked()
    cfg = dialog.workflow_config
    assert cfg is not None, "dialog should have accepted"
    ds = cfg.dilute_settings
    assert ds is not None
    assert ds.mask_name == "dilute"
    assert ds.dilation_radius_px == 5
    assert ds.channel in ("GFP", "RFP")
    # Metric default is median_intensity now.
    assert ds.metric == "median_intensity"
    assert ds.algorithm is ThresholdAlgorithm.GMM


def test_dilute_enabled_empty_name_warns(dialog, h5_ds1, tmp_path):
    """U3: dilute group checked + empty mask name → validation warning."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    _dilute_group(dialog).setChecked(True)
    dialog._dilute_mask_name.setText("")  # empty

    with patch.object(QMessageBox, "warning") as warn_mock:
        dialog._on_start_clicked()

    warn_mock.assert_called()
    msg = " ".join(str(a) for a in warn_mock.call_args[0]).lower()
    assert "dilute" in msg and ("name" in msg or "required" in msg)
    assert dialog.workflow_config is None


def test_dilute_name_collision_with_round_name_warns(
    dialog, h5_ds1, tmp_path
):
    """U3 / AE4: dilute mask_name matching a thresholding round name → warning."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    # Set the round's name to something specific.
    round_name_item = dialog._round_cards[0]._name
    round_name_item.setText("puncta_bright")
    dialog._output_edit.setText(str(tmp_path / "runs"))

    _dilute_group(dialog).setChecked(True)
    dialog._dilute_mask_name.setText("puncta_bright")  # collision
    dialog._dilute_channel.setCurrentIndex(0)

    with patch.object(QMessageBox, "warning") as warn_mock:
        dialog._on_start_clicked()

    warn_mock.assert_called()
    msg = " ".join(str(a) for a in warn_mock.call_args[0]).lower()
    assert "conflict" in msg or "puncta_bright" in msg
    assert dialog.workflow_config is None


def test_particle_metrics_round_trip_into_csv_columns(dialog, h5_ds1, tmp_path):
    """U7 / Configure CSV Export: selected particle metrics produce
    <round>_<metric> and <round>_<channel>_<metric> columns."""

    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    # Set a meaningful round name we can spot in the output.
    dialog._round_cards[0]._name.setText("puncta")
    dialog._output_edit.setText(str(tmp_path / "runs"))

    # Pre-select one channel + one whole-cell metric so the picker has
    # at least one channel for the per-channel particle columns.
    dialog._selected_csv_channels = {"GFP"}
    dialog._selected_csv_metrics = {"mean_intensity"}
    # Pick one per-cell and three per-channel particle metrics (the
    # set the user specifically named in iteration-3 feedback: min,
    # max, integrated intensity).
    dialog._selected_csv_particle_per_cell = {"particle_count"}
    dialog._selected_csv_particle_per_channel = {
        "particle_min_intensity",
        "particle_max_intensity",
        "particle_integrated_intensity",
    }

    intersected = ["GFP", "RFP", "DAPI"]
    rounds = dialog._rounds_from_cards(intersected)
    cols = dialog._build_selected_csv_columns(intersected, rounds)

    # Per-cell particle column: <round>_<metric>
    assert "puncta_particle_count" in cols
    # Per-channel particle columns: <round>_<channel>_<metric>
    assert "puncta_GFP_particle_min_intensity" in cols
    assert "puncta_GFP_particle_max_intensity" in cols
    assert "puncta_GFP_particle_integrated_intensity" in cols
    # Not selected → not in the list
    assert "puncta_total_particle_area" not in cols
    assert "puncta_GFP_particle_mean_intensity" not in cols


def test_particle_per_channel_metric_set_matches_builtin_metrics(dialog):
    """U7: the per-channel particle metric set covers every BUILTIN_METRICS
    intensity metric (area is intentionally excluded — it's a per-cell
    quantity rolled up via particle_count / mean_particle_area)."""
    from percell4.domain.measure.metrics import BUILTIN_METRICS
    from percell4.gui.workflows.single_cell.config_dialog import (
        _PARTICLE_PER_CHANNEL_METRICS,
    )

    expected = {
        f"particle_{m}" for m in BUILTIN_METRICS.keys() if m != "area"
    }
    assert set(_PARTICLE_PER_CHANNEL_METRICS) == expected


def test_particle_metrics_not_added_when_unselected(dialog, h5_ds1, tmp_path):
    """U7: with no particle metrics selected, no particle columns are emitted."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    dialog._selected_csv_channels = {"GFP"}
    dialog._selected_csv_metrics = {"mean_intensity"}
    # Explicitly clear the particle defaults for this test.
    dialog._selected_csv_particle_per_cell = set()
    dialog._selected_csv_particle_per_channel = set()

    intersected = ["GFP", "RFP", "DAPI"]
    rounds = dialog._rounds_from_cards(intersected)
    cols = dialog._build_selected_csv_columns(intersected, rounds)
    assert not any("particle" in c for c in cols)


def test_csv_picker_default_selections(dialog, h5_ds1):
    """The CSV picker pre-seeds the requested default metric + particle set,
    and channels auto-select to the full intersection."""
    # Default metric / particle selections (before any picker interaction).
    assert dialog._selected_csv_metrics == {
        "area",
        "integrated_intensity",
        "mean_intensity",
    }
    assert dialog._selected_csv_particle_per_cell == {
        "particle_count",
        "total_particle_area",
    }
    assert dialog._selected_csv_particle_per_channel == {
        "particle_mean_intensity",
    }
    # Channels auto-select to the intersection once datasets are added.
    dialog._add_h5_paths([h5_ds1])
    assert dialog._selected_csv_channels == {"GFP", "RFP", "DAPI"}


def test_picker_emits_area_um2_siblings(dialog, h5_ds1, tmp_path):
    """Every area-style column the user picks gets an `<col>_um2` sibling.
    The CSV writer's `c in df.columns` guard drops siblings whose value
    doesn't exist; this just means CSVs include _um2 when measure_one
    produced it."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._round_cards[0]._name.setText("puncta")
    dialog._output_edit.setText(str(tmp_path / "runs"))

    dialog._selected_csv_channels = {"GFP"}
    # 'area' is in _CORE_OPTIONAL_COLUMNS so it's always emitted.
    # Also pick the per-cell particle area metrics.
    dialog._selected_csv_metrics = {"mean_intensity", "area"}
    dialog._selected_csv_particle_per_cell = {
        "total_particle_area",
        "mean_particle_area",
        "max_particle_area",
    }

    intersected = ["GFP", "RFP", "DAPI"]
    rounds = dialog._rounds_from_cards(intersected)
    cols = dialog._build_selected_csv_columns(intersected, rounds)

    # Core cell area + sibling
    assert "area" in cols
    assert "area_um2" in cols
    # Whole-cell area metric × channel
    assert "GFP_area" in cols
    assert "GFP_area_um2" in cols
    # Per-round inside area (from the `area` metric × round)
    assert "GFP_area_in_puncta" in cols
    assert "GFP_area_in_puncta_um2" in cols
    # Per-cell particle area aggregates × round
    assert "puncta_total_particle_area" in cols
    assert "puncta_total_particle_area_um2" in cols
    assert "puncta_mean_particle_area_um2" in cols
    assert "puncta_max_particle_area_um2" in cols
    # Non-area columns get NO um2 sibling
    assert "GFP_mean_intensity" in cols
    assert "GFP_mean_intensity_um2" not in cols


def test_no_pixel_size_override_field(dialog):
    """Pixel size is sourced from /metadata.pixel_size_um (TIFF tags) only —
    the dialog exposes no override field."""
    assert not hasattr(dialog, "_cp_pixel_size")


def test_dialog_remains_scroll_wrap_compliant(dialog):
    """U3: dialog still uses wrap_in_scroll — adding sections did not regress
    the dialog-scroll compliance pattern (per docs/solutions/ui-bugs/
    dialog-scroll-when-tall.md)."""
    from qtpy.QtWidgets import QScrollArea

    # The outer layout's first widget (above btn_bar) should be a
    # QScrollArea per wrap_in_scroll.
    scroll_areas = dialog.findChildren(QScrollArea)
    assert len(scroll_areas) >= 1


def test_run_seg_qc_checkbox_default_checked(dialog, h5_ds1, h5_ds2, tmp_path):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    assert dialog._run_seg_qc.isChecked() is True
    dialog._on_start_clicked()
    cfg = dialog.workflow_config
    assert cfg is not None
    assert cfg.run_seg_qc_on_existing is True


def test_run_seg_qc_checkbox_unchecked_flows_to_config(
    dialog, h5_ds1, h5_ds2, tmp_path
):
    dialog._add_h5_paths([h5_ds1, h5_ds2])
    dialog._on_add_round()
    dialog._output_edit.setText(str(tmp_path / "runs"))
    dialog._run_seg_qc.setChecked(False)
    dialog._on_start_clicked()
    cfg = dialog.workflow_config
    assert cfg is not None
    assert cfg.run_seg_qc_on_existing is False


# ── Card list: reorder / remove / empty-state / boundary (U4) ────────────


def test_reorder_moves_round_and_preserves_all_fields(dialog, h5_ds1):
    """▼ on the first card swaps order and preserves every field of both rounds."""
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    c0 = dialog._round_cards[0]
    c0._name.setText("first")
    c0._method.setCurrentText(METHOD_AUTO_EXTRACT)
    c0._d_min.setValue(0.5)
    dialog._round_cards[1]._name.setText("second")

    dialog._on_card_move_down(c0)  # first ↓

    names = [c.to_dict()["name"] for c in dialog._round_cards]
    assert names == ["second", "first"]
    moved = dialog._round_cards[1].to_dict()
    assert moved["name"] == "first"
    assert moved["method"] == METHOD_AUTO_EXTRACT
    assert moved["d_min_um"] == 0.5


def test_remove_middle_card(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    for _ in range(3):
        dialog._on_add_round()
    for i, nm in enumerate(("a", "b", "c")):
        dialog._round_cards[i]._name.setText(nm)
    dialog._on_card_remove(dialog._round_cards[1])  # remove "b"
    assert [c.to_dict()["name"] for c in dialog._round_cards] == ["a", "c"]


def test_boundary_move_buttons_disabled(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    dialog._on_add_round()
    dialog._on_add_round()
    first, last = dialog._round_cards[0], dialog._round_cards[1]
    assert first._up_btn.isEnabled() is False   # top card: ▲ disabled
    assert first._down_btn.isEnabled() is True
    assert last._down_btn.isEnabled() is False  # bottom card: ▼ disabled
    assert last._up_btn.isEnabled() is True


def test_empty_state_and_zero_round_start_gate(dialog, h5_ds1):
    dialog._add_h5_paths([h5_ds1])
    # No rounds yet: placeholder visible, Start disabled.
    assert dialog._rounds_empty_label.isVisibleTo(dialog._rounds_container)
    assert dialog._start_btn.isEnabled() is False
    dialog._on_add_round()
    assert dialog._rounds_empty_label.isHidden()
    assert dialog._start_btn.isEnabled() is True
    # Remove the last card: placeholder returns, Start disabled again.
    dialog._on_card_remove(dialog._round_cards[0])
    assert dialog._rounds_empty_label.isVisibleTo(dialog._rounds_container)
    assert dialog._start_btn.isEnabled() is False


def test_grouped_otsu_um2_min_size_flagged_by_preflight(dialog, tmp_path, monkeypatch):
    """R7 regression guard: a Grouped Otsu round with a µm² Min. Particle Area on a
    dataset lacking a pixel size is blocked — the µm²-Min-size pre-flight fires for
    ANY method, not just ALC."""
    no_ps = _make_h5_with_pixel_size(tmp_path, "NoPS", ["GFP"], pixel_size_um=None)
    dialog._add_h5_paths([no_ps])
    dialog._on_add_round()  # default method = Grouped Otsu
    card = dialog._round_cards[0]
    card._min_size.setValue(10.0)
    card._min_size_unit.setCurrentIndex(card._min_size_unit.findData("um2"))
    dialog._output_edit.setText(str(tmp_path / "runs"))
    warnings = []
    monkeypatch.setattr(dialog, "_warn", lambda msg, *a, **k: warnings.append(msg))
    cfg = dialog._try_build_config()
    assert cfg is None  # blocked despite being a Grouped Otsu round
    assert any("pixel size" in w for w in warnings)
