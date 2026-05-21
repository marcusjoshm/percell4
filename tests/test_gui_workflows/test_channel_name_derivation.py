"""Regression test for the tiff_pending channel-name derivation in
WorkflowConfigDialog.

Bug: when the compress dialog returned ``layer_assignments == {}`` (the
user never renamed channels), the workflow config dialog stored
``channel_names = ["00", "01", "02"]`` (raw tokens) on the
``WorkflowDatasetEntry`` and fed those into the thresholding-round
channel dropdown. Meanwhile ``import_dataset`` writes
``/metadata.channel_names = ["ch00", "ch01", "ch02"]`` to the HDF5. At
``threshold_compute`` time the lookup ``_channel_index(store, "02")``
raised ``KeyError: channel '02' not in dataset; available: ['ch00',
'ch01', 'ch02']`` and every dataset failed after segmentation already
ran.

Fix: when no LayerAssignment override exists, mirror the importer's
``f"ch{ch_key}"`` default — keeping the workflow side and the HDF5
side in sync.
"""

from __future__ import annotations

from types import SimpleNamespace

from percell4.gui.workflows.single_cell.config_dialog import (
    _derive_tiff_pending_channel_names,
)


def test_empty_layer_assignments_falls_back_to_ch_prefixed_tokens():
    """Reproduces the failed run: layer_assignments={} → channel names
    must be ``ch00``-prefixed to match what import_dataset writes."""
    names = _derive_tiff_pending_channel_names(["00", "01", "02"], {})
    assert names == ["ch00", "ch01", "ch02"]


def test_layer_assignment_override_wins():
    """When the user renamed a channel in the compress dialog, that name
    is canonical — not the ch-prefix default."""
    override = {
        "00": SimpleNamespace(name="EU"),
        "01": SimpleNamespace(name="mNG"),
    }
    names = _derive_tiff_pending_channel_names(["00", "01"], override)
    assert names == ["EU", "mNG"]


def test_partial_overrides_mix_with_ch_defaults():
    """Channels with an override keep their custom name; others get the
    ch-prefixed default."""
    override = {"01": SimpleNamespace(name="mNG")}
    names = _derive_tiff_pending_channel_names(["00", "01", "02"], override)
    assert names == ["ch00", "mNG", "ch02"]


def test_empty_name_in_override_falls_back_to_ch_default():
    """A LayerAssignment with an empty .name (user cleared the field)
    should not produce a blank channel name in the dataset entry."""
    override = {"00": SimpleNamespace(name="")}
    names = _derive_tiff_pending_channel_names(["00"], override)
    assert names == ["ch00"]


def test_none_override_treated_as_missing():
    """A None in the layer_assignments dict (defensive) falls back to
    the ch-prefix default rather than raising."""
    override = {"00": None}
    names = _derive_tiff_pending_channel_names(["00", "01"], override)
    assert names == ["ch00", "ch01"]


def test_empty_token_list_returns_empty():
    """No selected channels → no derived names. Doesn't crash."""
    assert _derive_tiff_pending_channel_names([], {}) == []
