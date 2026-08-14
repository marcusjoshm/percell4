"""Tests for the FLIM-FRET dataset eligibility / pre-screening helper."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from percell4.application.use_cases.flim_fret_discovery import (
    DatasetCandidate,
    discover_flim_fret_candidates,
    list_lifetime_channel_names,
    validate_pair_layers,
)
from percell4.store import DatasetStore
from percell4.workflows.models import FlimFretPair

# ── Fixture builder ─────────────────────────────────────────


def _make_h5(
    path: Path,
    *,
    channel_names: list[str],
    mask_names: list[str] = (),
    label_names: list[str] = (),
    intensity_shape: tuple[int, ...] | None = None,
) -> Path:
    """Build a minimal but realistic FLIM-FRET-shaped ``.h5`` fixture.

    ``intensity_shape`` defaults to ``(C, H, W)`` with ``H=W=4`` and
    ``C=len(channel_names)``. Pass an explicit shape (e.g. 4D ``(T, C, H,
    W)``) to test time-lapse rejection.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if intensity_shape is None:
        intensity_shape = (len(channel_names), 4, 4)
    with h5py.File(path, "w") as f:
        meta = f.create_group("metadata")
        meta.attrs["channel_names"] = channel_names
        f.create_dataset(
            "intensity",
            data=np.ones(intensity_shape, dtype=np.float32),
        )
        if mask_names:
            masks_grp = f.create_group("masks")
            for name in mask_names:
                masks_grp.create_dataset(
                    name, data=np.ones((4, 4), dtype=np.uint8)
                )
        if label_names:
            labels_grp = f.create_group("labels")
            for name in label_names:
                labels_grp.create_dataset(
                    name, data=np.ones((4, 4), dtype=np.int32)
                )
    return path


# ── discover_flim_fret_candidates ───────────────────────────


def test_discovery_returns_empty_for_empty_folder(tmp_path):
    assert discover_flim_fret_candidates(tmp_path, single_cell=False) == []


def test_discovery_ignores_non_h5_files(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "data.h5.bak").write_text("backup")
    assert discover_flim_fret_candidates(tmp_path, single_cell=False) == []


def test_discovery_admits_qualifying_dataset(tmp_path):
    _make_h5(
        tmp_path / "good.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert len(out) == 1
    assert out[0].qualifies is True
    assert out[0].reasons == []


def test_discovery_rejects_missing_mask_suffix(tmp_path):
    _make_h5(
        tmp_path / "no_mask.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["phasor_ch0_1_phasor"],  # has phasor but no _mask
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert len(out) == 1
    assert out[0].qualifies is False
    assert "no /masks/<*>_mask" in out[0].reasons


def test_discovery_rejects_missing_phasor_suffix(tmp_path):
    _make_h5(
        tmp_path / "no_phasor.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["cells_mask"],  # has _mask but no _phasor
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert "no /masks/<*>_phasor" in out[0].reasons


def test_discovery_rejects_missing_lifetime_channel(tmp_path):
    _make_h5(
        tmp_path / "no_lifetime.h5",
        channel_names=["ch0", "ch1"],  # no _lifetime suffix
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert "no /intensity/*_lifetime channel" in out[0].reasons


def test_discovery_rejects_no_masks_group_at_all(tmp_path):
    _make_h5(
        tmp_path / "bare.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=[],
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert "no /masks/<*>_mask" in out[0].reasons
    assert "no /masks/<*>_phasor" in out[0].reasons


def test_discovery_rejects_time_lapse_intensity(tmp_path):
    _make_h5(
        tmp_path / "tlapse.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        intensity_shape=(3, 2, 4, 4),  # (T, C, H, W)
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert "time-lapse /intensity unsupported" in out[0].reasons


def test_discovery_single_cell_requires_labels(tmp_path):
    _make_h5(
        tmp_path / "no_labels.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        label_names=[],
    )
    out_wf = discover_flim_fret_candidates(tmp_path, single_cell=False)
    out_sc = discover_flim_fret_candidates(tmp_path, single_cell=True)
    assert out_wf[0].qualifies is True
    assert out_sc[0].qualifies is False
    assert "no /labels/* (required for single-cell)" in out_sc[0].reasons


def test_discovery_single_cell_accepts_dataset_with_labels(tmp_path):
    _make_h5(
        tmp_path / "good_sc.h5",
        channel_names=["ch0", "ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        label_names=["cellpose_qc"],
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=True)
    assert out[0].qualifies is True


def test_discovery_returns_dataset_candidate_dataclass(tmp_path):
    _make_h5(
        tmp_path / "good.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert isinstance(out[0], DatasetCandidate)


def test_discovery_captures_open_failure_without_raising(tmp_path):
    # Empty file: h5py will refuse to open it. Discovery must capture the
    # error in reasons, not bubble it up.
    (tmp_path / "broken.h5").write_text("")
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert len(out) == 1
    assert out[0].qualifies is False
    assert any(r.startswith("open failed:") for r in out[0].reasons)


def test_discovery_results_are_sorted_by_path(tmp_path):
    _make_h5(
        tmp_path / "z.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    _make_h5(
        tmp_path / "a.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    out = discover_flim_fret_candidates(tmp_path, single_cell=False)
    assert [p.path.name for p in out] == ["a.h5", "z.h5"]


# ── list_lifetime_channel_names ─────────────────────────────


def test_list_lifetime_channel_names_filters_by_suffix(tmp_path):
    path = _make_h5(
        tmp_path / "ds.h5",
        channel_names=[
            "ch0",
            "ch0_unfiltered_lifetime",
            "ch0_median_lifetime",
            "ch0_wavelet_lifetime",
        ],
        mask_names=["cells_mask"],
    )
    store = DatasetStore(path)
    assert list_lifetime_channel_names(store) == [
        "ch0_unfiltered_lifetime",
        "ch0_median_lifetime",
        "ch0_wavelet_lifetime",
    ]


def test_list_lifetime_channel_names_returns_empty_when_none(tmp_path):
    path = _make_h5(
        tmp_path / "ds.h5",
        channel_names=["ch0", "ch1"],
        mask_names=[],
    )
    store = DatasetStore(path)
    assert list_lifetime_channel_names(store) == []


# ── validate_pair_layers ────────────────────────────────────


def _pair(tmp_path: Path, **overrides) -> FlimFretPair:
    donor = _make_h5(
        tmp_path / "donor.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        label_names=["cellpose_qc"],
    )
    da = _make_h5(
        tmp_path / "da.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        label_names=["cellpose_qc"],
    )
    defaults = {
        "name": "pair_1",
        "donor_h5": donor,
        "da_h5": da,
        "donor_mask": "cells_mask",
        "donor_phasor": "phasor_ch0_1_phasor",
        "donor_lifetime": "ch0_unfiltered_lifetime",
        "da_mask": "cells_mask",
        "da_phasor": "phasor_ch0_1_phasor",
        "da_lifetime": "ch0_unfiltered_lifetime",
    }
    defaults.update(overrides)
    return FlimFretPair(**defaults)


def test_validate_returns_empty_for_valid_pair(tmp_path):
    pair = _pair(tmp_path)
    assert validate_pair_layers(pair, single_cell=False) == []


def test_validate_flags_missing_donor_mask(tmp_path):
    pair = _pair(tmp_path, donor_mask="not_there_mask")
    reasons = validate_pair_layers(pair, single_cell=False)
    assert any("missing donor mask 'not_there_mask'" in r for r in reasons)


def test_validate_flags_missing_da_phasor(tmp_path):
    pair = _pair(tmp_path, da_phasor="not_there_phasor")
    reasons = validate_pair_layers(pair, single_cell=False)
    assert any("missing DA phasor mask 'not_there_phasor'" in r for r in reasons)


def test_validate_flags_missing_lifetime_channel_name(tmp_path):
    pair = _pair(tmp_path, donor_lifetime="not_in_channel_names_lifetime")
    reasons = validate_pair_layers(pair, single_cell=False)
    assert any(
        "missing donor lifetime channel 'not_in_channel_names_lifetime'" in r
        for r in reasons
    )


def test_validate_flags_time_lapse_at_runtime(tmp_path):
    # Build a donor whose /intensity is 4D — simulates "re-imported as
    # time-lapse between dialog accept and Start".
    donor = _make_h5(
        tmp_path / "donor.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
        intensity_shape=(2, 1, 4, 4),
    )
    da = _make_h5(
        tmp_path / "da.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    pair = FlimFretPair(
        name="pair_1",
        donor_h5=donor,
        da_h5=da,
        donor_mask="cells_mask",
        donor_phasor="phasor_ch0_1_phasor",
        donor_lifetime="ch0_unfiltered_lifetime",
        da_mask="cells_mask",
        da_phasor="phasor_ch0_1_phasor",
        da_lifetime="ch0_unfiltered_lifetime",
    )
    reasons = validate_pair_layers(pair, single_cell=False)
    assert any(
        "donor /intensity is time-lapse (unsupported)" in r for r in reasons
    )


def test_validate_single_cell_requires_segmentation_present(tmp_path):
    pair = _pair(
        tmp_path,
        donor_segmentation="not_there",
        da_segmentation="cellpose_qc",
    )
    reasons = validate_pair_layers(pair, single_cell=True)
    assert any("missing donor segmentation 'not_there'" in r for r in reasons)


def test_validate_captures_open_failure_per_side(tmp_path):
    # Donor exists, DA does not.
    donor = _make_h5(
        tmp_path / "donor.h5",
        channel_names=["ch0_unfiltered_lifetime"],
        mask_names=["cells_mask", "phasor_ch0_1_phasor"],
    )
    pair = FlimFretPair(
        name="pair_1",
        donor_h5=donor,
        da_h5=tmp_path / "does_not_exist.h5",
        donor_mask="cells_mask",
        donor_phasor="phasor_ch0_1_phasor",
        donor_lifetime="ch0_unfiltered_lifetime",
        da_mask="cells_mask",
        da_phasor="phasor_ch0_1_phasor",
        da_lifetime="ch0_unfiltered_lifetime",
    )
    reasons = validate_pair_layers(pair, single_cell=False)
    assert any("DA dataset open failed" in r for r in reasons)


def test_validate_with_segmentation_none_in_whole_field_mode(tmp_path):
    # When single_cell=False the segmentation field is None and ignored.
    pair = _pair(tmp_path)  # both segmentations default to None
    assert pair.donor_segmentation is None
    assert validate_pair_layers(pair, single_cell=False) == []
