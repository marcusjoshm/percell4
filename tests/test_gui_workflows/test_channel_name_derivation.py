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


def test_mask_and_segmentation_assignments_are_not_channels():
    """A token assigned as mask/segmentation is not a channel.

    ``import_dataset`` routes those layers into ``/masks`` and ``/labels`` and
    never appends them to ``/metadata.channel_names``. Returning them here
    offered a mask as a selectable channel in the rounds and Cellpose combos,
    and poisoned ``intersect_channels`` with a name no dataset reports.
    """
    from percell4.domain.io.models import LayerType

    override = {
        "DNA": SimpleNamespace(name="DNA", layer_type=LayerType.CHANNEL),
        "SG_mask": SimpleNamespace(name="SG_mask", layer_type=LayerType.MASK),
        "cells": SimpleNamespace(name="cells", layer_type=LayerType.SEGMENTATION),
    }
    names = _derive_tiff_pending_channel_names(
        ["DNA", "SG_mask", "cells"], override
    )
    assert names == ["DNA"]


def test_assignment_without_layer_type_defaults_to_channel():
    """Back-compat: an override carrying only a name is still a channel."""
    override = {"00": SimpleNamespace(name="EU")}
    assert _derive_tiff_pending_channel_names(["00"], override) == ["EU"]


def test_all_tokens_assigned_non_channel_yields_no_names():
    """Every token routed away from /intensity → no derivable channels.

    The dialog treats an empty result as "nothing to add", which is the
    correct outcome rather than offering phantom channels.
    """
    from percell4.domain.io.models import LayerType

    override = {
        "a": SimpleNamespace(name="a", layer_type=LayerType.MASK),
        "b": SimpleNamespace(name="b", layer_type=LayerType.SEGMENTATION),
    }
    assert _derive_tiff_pending_channel_names(["a", "b"], override) == []


def test_producer_consumer_contract_excludes_mask_layers(tmp_path):
    """The contract holds when some tokens are assigned to /masks.

    Asserting the full derivation against ``channel_names`` would fail even on
    a correct implementation, because mask-typed tokens land in ``/masks``.
    """
    import numpy as np
    import tifffile

    from percell4.adapters.importer import import_dataset
    from percell4.domain.io.discovery import discover_tokenless
    from percell4.domain.io.models import LayerAssignment, LayerType
    from percell4.store import DatasetStore

    src = tmp_path / "raw"
    src.mkdir()
    # Single-word suffixes: tokenless discovery splits on the last underscore,
    # so a name like "SG_mask" would tokenize as "mask".
    for i, ch in enumerate(("DNA", "puncta")):
        tifffile.imwrite(
            str(src / f"Exp_B_{ch}.tif"),
            np.full((16, 16), (i + 1) * 30, dtype=np.uint16),
        )

    datasets, token_config = discover_tokenless(src)
    ds = datasets[0]
    assignments = {
        "DNA": LayerAssignment(layer_type=LayerType.CHANNEL, name="DNA"),
        "puncta": LayerAssignment(layer_type=LayerType.MASK, name="puncta"),
    }
    h5_path = tmp_path / "Exp_B.h5"
    import_dataset(
        src,
        h5_path,
        token_config=token_config,
        files=list(ds.files),
        layer_assignments=assignments,
    )

    store = DatasetStore(h5_path)
    derived = _derive_tiff_pending_channel_names(
        sorted(ds.scan_result.channels), assignments
    )
    assert derived == list(store.metadata["channel_names"]) == ["DNA"]
    assert "puncta" in store.list_masks()


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
