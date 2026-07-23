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


def test_name_tokens_fall_back_to_verbatim_names():
    """Tokenless name tokens with no override stay verbatim — no 'ch' prefix."""
    names = _derive_tiff_pending_channel_names(["DNA", "G3BP1", "SG_mask"], {})
    assert names == ["DNA", "G3BP1", "SG_mask"]


def test_name_token_override_still_wins():
    override = {"DNA": SimpleNamespace(name="GFP")}
    names = _derive_tiff_pending_channel_names(["DNA", "G3BP1"], override)
    assert names == ["GFP", "G3BP1"]


def test_producer_consumer_contract_name_tokens(tmp_path):
    """The consumer's derived names equal what the importer actually stored in
    /metadata.channel_names — the byte-for-byte producer/consumer contract."""
    import numpy as np
    import tifffile

    from percell4.adapters.importer import import_dataset
    from percell4.domain.io.discovery import discover_tokenless
    from percell4.store import DatasetStore

    src = tmp_path / "raw"
    src.mkdir()
    for i, ch in enumerate(("cells", "DNA", "G3BP1", "SG_mask")):
        tifffile.imwrite(
            str(src / f"Exp_A_{ch}.tif"),
            np.full((16, 16), (i + 1) * 30, dtype=np.uint16),
        )

    datasets, token_config = discover_tokenless(src)
    ds = datasets[0]
    h5_path = tmp_path / "Exp_A.h5"
    import_dataset(src, h5_path, token_config=token_config, files=list(ds.files))

    stored = DatasetStore(h5_path).metadata["channel_names"]
    # Consumer is fed the same token ids (in the importer's sorted order),
    # unrenamed. It must reproduce the stored names exactly.
    derived = _derive_tiff_pending_channel_names(sorted(ds.scan_result.channels), {})
    assert derived == list(stored)


def test_producer_consumer_contract_numeric_tokens(tmp_path):
    """R8: the contract also holds byte-for-byte for numeric chXX tokens."""
    import numpy as np
    import tifffile

    from percell4.adapters.importer import import_dataset
    from percell4.store import DatasetStore

    src = tmp_path / "raw"
    src.mkdir()
    for ch in range(2):
        tifffile.imwrite(
            str(src / f"img_ch{ch:02d}.tif"),
            np.full((16, 16), ch * 20, dtype=np.uint16),
        )
    h5_path = tmp_path / "out.h5"
    import_dataset(src, h5_path)

    stored = DatasetStore(h5_path).metadata["channel_names"]
    derived = _derive_tiff_pending_channel_names(["00", "01"], {})
    assert derived == list(stored) == ["ch00", "ch01"]
