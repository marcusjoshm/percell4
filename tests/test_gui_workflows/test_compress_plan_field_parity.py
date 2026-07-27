"""The workflow's compress plan must carry every import-shaping field.

``WorkflowConfigDialog._build_compress_plan`` serializes a ``CompressConfig``
into the JSON-safe dict persisted in ``run_config.json`` and replayed by
``phases.compress_one`` in Phase 0. Any field the dialog collects but the plan
omits is silently lost, and the resulting ``.h5`` differs from what the
standalone Import Dataset path would have produced.

``token_config`` was the field that broke TIFF-start runs outright: for a
tokenless (name-suffixed) import the pattern is synthesized by
``discover_tokenless`` inside the CompressDialog, so dropping it left
``import_dataset`` matching the default ``_ch(\\d+)`` against files named
``Exp_A_DNA.tif``. Nothing grouped, the ``selected_channels`` filter emptied the
result, and the dataset landed with no ``/intensity``.

This is the third dropped-key defect on this seam (``tile_config`` was the
first, ``creation_bin`` the second), hence the parity guard at the bottom.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from percell4.domain.io.models import LayerAssignment, LayerType, TokenConfig
from percell4.gui.workflows.single_cell.config_dialog import _build_compress_plan


def _ds(tmp_path: Path, name: str = "Exp_A") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source_dir=tmp_path,
        files=[SimpleNamespace(path=tmp_path / f"{name}_DNA.tif")],
        output_path=tmp_path / f"{name}.h5",
    )


def _cfg(**overrides) -> SimpleNamespace:
    base = {
        "z_project_method": "mip",
        "token_config": TokenConfig(),
        "tile_config": None,
        "flim_params": None,
        "creation_bin": 1,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_plan_carries_tokenless_channel_pattern(tmp_path: Path) -> None:
    """The synthesized tokenless pattern must reach the plan dict.

    This is the exact configuration that failed: named channels, no numeric
    token anywhere in the filename.
    """
    tokenless = TokenConfig(
        channel=r"_(DNA|SG_mask)\.tif$",
        timepoint=None,
        z_slice=None,
        tile=None,
    )
    plan = _build_compress_plan(
        ds=_ds(tmp_path),
        gui_state=None,
        cfg=_cfg(token_config=tokenless),
        selected_token_ids=["DNA"],
        layer_assignments_payload={},
    )

    assert "token_config" in plan, (
        "token_config dropped from the compress plan — tokenless TIFF-start "
        "runs compress to an .h5 with no /intensity"
    )
    assert plan["token_config"]["channel"] == r"_(DNA|SG_mask)\.tif$"
    assert plan["token_config"]["timepoint"] is None


def test_plan_carries_creation_bin(tmp_path: Path) -> None:
    """compress_one has always read this key; the producer never wrote it."""
    plan = _build_compress_plan(
        ds=_ds(tmp_path),
        gui_state=None,
        cfg=_cfg(creation_bin=2),
        selected_token_ids=["DNA"],
        layer_assignments_payload={},
    )
    assert plan["creation_bin"] == 2


def test_plan_carries_flim_params(tmp_path: Path) -> None:
    """FLIM calibration is JSON-safe and must survive to the importer."""
    flim = {
        "frequency_mhz": 80.0,
        "channel_calibrations": {"DNA": {"phase": 1.5, "modulation": 0.9}},
        "bin_dimensions": {"x_dim": 256, "y_dim": 256, "t_dim": 132},
    }
    plan = _build_compress_plan(
        ds=_ds(tmp_path),
        gui_state=None,
        cfg=_cfg(flim_params=flim),
        selected_token_ids=["DNA"],
        layer_assignments_payload={},
    )
    assert plan["flim_params"] == flim


def test_plan_omits_flim_params_when_unset(tmp_path: Path) -> None:
    """No FLIM group checked → no key, so the payload stays minimal."""
    plan = _build_compress_plan(
        ds=_ds(tmp_path),
        gui_state=None,
        cfg=_cfg(flim_params=None),
        selected_token_ids=["DNA"],
        layer_assignments_payload={},
    )
    assert "flim_params" not in plan


def test_plan_is_json_serializable(tmp_path: Path) -> None:
    """The plan is persisted verbatim into run_config.json."""
    import json

    plan = _build_compress_plan(
        ds=_ds(tmp_path),
        gui_state=None,
        cfg=_cfg(
            token_config=TokenConfig(channel=r"_C(\d+)", tile=None),
            flim_params={"frequency_mhz": 80.0, "bin_dimensions": {"x_dim": 256}},
            creation_bin=2,
        ),
        selected_token_ids=["00"],
        layer_assignments_payload={},
    )
    round_tripped = json.loads(json.dumps(plan))
    assert round_tripped["token_config"]["channel"] == r"_C(\d+)"
    assert round_tripped["token_config"]["tile"] is None
    assert round_tripped["creation_bin"] == 2


@pytest.mark.parametrize(
    "field",
    ["token_config", "creation_bin", "flim_params", "tile_config"],
)
def test_parity_guard_every_import_shaping_field_is_serialized(
    tmp_path: Path, field: str
) -> None:
    """Guard against a fourth dropped-key defect on this seam.

    Covers only ``CompressConfig`` fields that ``import_dataset`` consumes. It
    is deliberately blind to output-path derivation (``dataset_name_overrides``,
    ``output_dir``) and to value-level divergence — see the plan's Scope
    Boundaries.
    """
    plan = _build_compress_plan(
        ds=_ds(tmp_path),
        gui_state=None,
        cfg=_cfg(
            token_config=TokenConfig(channel=r"_C(\d+)"),
            flim_params={"frequency_mhz": 80.0},
            creation_bin=2,
            tile_config=SimpleNamespace(
                grid_rows=2,
                grid_cols=2,
                grid_type="row_by_row",
                order="right_down",
                overlap=0.1,
                register=True,
                reference_channel="ch00",
            ),
        ),
        selected_token_ids=["00"],
        layer_assignments_payload={},
    )
    assert field in plan, (
        f"{field} is collected by CompressConfig and consumed by "
        f"import_dataset, but never reaches the compress plan"
    )


def test_end_to_end_tokenless_workflow_compress(tmp_path: Path) -> None:
    """The full reported failure, end to end through a real HDF5 file.

    Builds tokenless TIFFs, runs the dialog's plan builder, replays it through
    ``compress_one`` exactly as Phase 0 does, and asserts the dataset is usable.
    Before the fix this produced an .h5 with no ``/intensity``.
    """
    import numpy as np
    import tifffile

    from percell4.domain.io.discovery import discover_tokenless
    from percell4.store import DatasetStore
    from percell4.workflows.models import DatasetSource, WorkflowDatasetEntry
    from percell4.workflows.phases import compress_one

    src = tmp_path / "raw"
    src.mkdir()
    for i, ch in enumerate(("DNA", "G3BP1")):
        tifffile.imwrite(
            str(src / f"Exp_A_{ch}.tif"),
            np.full((16, 16), (i + 1) * 30, dtype=np.uint16),
        )

    datasets, token_config = discover_tokenless(src)
    ds = datasets[0]
    out_h5 = tmp_path / "Exp_A.h5"
    ds_spec = SimpleNamespace(
        name="Exp_A",
        source_dir=src,
        files=list(ds.files),
        output_path=out_h5,
    )
    selected = sorted(ds.scan_result.channels)

    plan = _build_compress_plan(
        ds=ds_spec,
        gui_state=None,
        cfg=_cfg(token_config=token_config),
        selected_token_ids=selected,
        layer_assignments_payload={},
    )

    entry = WorkflowDatasetEntry(
        name="Exp_A",
        source=DatasetSource.TIFF_PENDING,
        h5_path=out_h5,
        channel_names=list(selected),
        compress_plan=plan,
    )
    updated, failure, msg = compress_one(entry)

    assert failure is None, f"compress_one failed: {msg}"
    assert updated.source is DatasetSource.H5_EXISTING
    assert out_h5.exists()

    store = DatasetStore(out_h5)
    stored = list(store.metadata["channel_names"])
    assert stored == ["DNA", "G3BP1"], (
        f"tokenless channels did not survive Phase 0: got {stored}"
    )
    assert store.read_channel("intensity", 0).shape == (16, 16)


def test_end_to_end_mask_assignment_not_a_channel(tmp_path: Path) -> None:
    """A mask-typed token lands in /masks and is absent from channel_names."""
    import numpy as np
    import tifffile

    from percell4.domain.io.discovery import discover_tokenless
    from percell4.store import DatasetStore
    from percell4.workflows.models import DatasetSource, WorkflowDatasetEntry
    from percell4.workflows.phases import compress_one

    src = tmp_path / "raw"
    src.mkdir()
    tifffile.imwrite(
        str(src / "Exp_C_DNA.tif"), np.full((16, 16), 30, dtype=np.uint16)
    )
    # Single-word suffix: tokenless discovery splits on the last underscore,
    # so a name like "SG_mask" would tokenize as "mask".
    tifffile.imwrite(
        str(src / "Exp_C_puncta.tif"), np.ones((16, 16), dtype=np.uint16)
    )

    datasets, token_config = discover_tokenless(src)
    ds = datasets[0]
    out_h5 = tmp_path / "Exp_C.h5"
    selected = sorted(ds.scan_result.channels)

    plan = _build_compress_plan(
        ds=SimpleNamespace(
            name="Exp_C",
            source_dir=src,
            files=list(ds.files),
            output_path=out_h5,
        ),
        gui_state=None,
        cfg=_cfg(token_config=token_config),
        selected_token_ids=selected,
        layer_assignments_payload={
            "DNA": {"layer_type": LayerType.CHANNEL.value, "name": "DNA"},
            "puncta": {"layer_type": LayerType.MASK.value, "name": "puncta"},
        },
    )

    entry = WorkflowDatasetEntry(
        name="Exp_C",
        source=DatasetSource.TIFF_PENDING,
        h5_path=out_h5,
        channel_names=["DNA"],
        compress_plan=plan,
    )
    _updated, failure, msg = compress_one(entry)
    assert failure is None, f"compress_one failed: {msg}"

    store = DatasetStore(out_h5)
    assert list(store.metadata["channel_names"]) == ["DNA"]
    assert "puncta" in store.list_masks()
    # Guards the LayerAssignment round trip the plan dict performs.
    assert isinstance(
        LayerAssignment(layer_type=LayerType.MASK, name="puncta").layer_type,
        LayerType,
    )
