"""Regression tests: compress_one must forward every import-shaping field.

The single-cell workflow serializes a ``compress_plan`` dict in the config
dialog and rebuilds the ``import_dataset`` call from it in Phase 0. Any field
``CompressConfig`` carries but the plan dict omits is silently lost.

``token_config`` was the one that mattered: dropping it made
``import_dataset`` fall back to ``TokenConfig()`` (``channel=r"_ch(\\d+)"``), so
a tokenless named-channel import — where ``discover_tokenless`` synthesizes the
pattern inside ``CompressDialog`` — parsed to zero channel groups and wrote an
``.h5`` with no ``/intensity`` and empty ``channel_names``. The run then failed
minutes later in an unrelated phase.

``creation_bin`` was read by ``compress_one`` but never written by the dialog,
so the binning spinbox was silently ignored on the workflow path.

Companion to ``test_phases_compress_tile_config.py``, which covers the same
class of dropped-key defect for ``tile_config``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from percell4.workflows.models import (
    DatasetSource,
    WorkflowDatasetEntry,
)
from percell4.workflows.phases import compress_one


def _entry(tmp_path: Path, **plan_extra) -> WorkflowDatasetEntry:
    """A TIFF_PENDING entry whose plan carries the always-present keys."""
    output_h5 = tmp_path / "output.h5"
    plan = {
        "source_dir": str(tmp_path),
        "files": [str(tmp_path / "fake.tif")],
        "output_path": str(output_h5),
        "z_project_method": "mip",
        "selected_channels": ["DNA"],
        "layer_assignments": {},
    }
    plan.update(plan_extra)
    return WorkflowDatasetEntry(
        name="tokenless_dataset",
        source=DatasetSource.TIFF_PENDING,
        h5_path=output_h5,
        channel_names=["DNA"],
        compress_plan=plan,
    )


def test_compress_one_forwards_tokenless_token_config(tmp_path: Path) -> None:
    """A synthesized tokenless channel pattern must reach import_dataset.

    This is the defect that broke TIFF-start runs: without the pattern the
    importer matches ``_ch(\\d+)`` against files named ``..._DNA.tif`` and
    groups nothing.
    """
    entry = _entry(
        tmp_path,
        token_config={
            "channel": r"_(DNA|SG_mask)\.tif$",
            "timepoint": None,
            "z_slice": None,
            "tile": None,
        },
    )

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    assert mock_import.called, "import_dataset was not called"
    tc = mock_import.call_args.kwargs.get("token_config")
    assert tc is not None, (
        "token_config dropped between compress_plan and import_dataset — "
        "tokenless and custom-regex imports silently produce an empty .h5"
    )
    assert tc.channel == r"_(DNA|SG_mask)\.tif$"
    assert tc.timepoint is None
    assert tc.z_slice is None
    assert tc.tile is None


def test_compress_one_forwards_custom_channel_regex(tmp_path: Path) -> None:
    """A user-edited channel regex survives the plan-dict round trip."""
    entry = _entry(
        tmp_path,
        selected_channels=["01"],
        token_config={
            "channel": r"_C(\d+)",
            "timepoint": r"_t(\d+)",
            "z_slice": r"_z(\d+)",
            "tile": r"_s(\d+)",
        },
    )

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    tc = mock_import.call_args.kwargs["token_config"]
    assert tc.channel == r"_C(\d+)"
    assert tc.timepoint == r"_t(\d+)"


def test_compress_one_forwards_creation_bin(tmp_path: Path) -> None:
    """The dialog's binning spinbox must not be silently ignored."""
    entry = _entry(tmp_path, creation_bin=2)

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    assert mock_import.call_args.kwargs["creation_bin"] == 2


def test_compress_one_forwards_flim_params(tmp_path: Path) -> None:
    """FLIM calibration must survive to the importer, or phasor data is lost."""
    flim = {
        "frequency_mhz": 80.0,
        "channel_calibrations": {"DNA": {"phase": 1.5, "modulation": 0.9}},
        "bin_dimensions": {
            "x_dim": 256,
            "y_dim": 256,
            "t_dim": 132,
            "dtype": "uint16",
            "dim_order": "xyt",
            "header_bytes": 4,
        },
    }
    entry = _entry(tmp_path, flim_params=flim)

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    assert mock_import.call_args.kwargs["flim_params"] == flim


def test_compress_one_legacy_plan_dict_keeps_current_defaults(
    tmp_path: Path,
) -> None:
    """Back-compat: a pre-change run_config.json plan must still load.

    A plan dict written before these keys existed has no ``token_config``,
    ``creation_bin``, or ``flim_params``. It must reconstruct to exactly
    today's behavior rather than raising.
    """
    entry = _entry(tmp_path)

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        _updated, failure, msg = compress_one(entry)

    assert failure is None, f"legacy plan dict failed to compress: {msg}"
    kwargs = mock_import.call_args.kwargs
    assert kwargs.get("token_config") is None
    assert kwargs["creation_bin"] == 1
    assert kwargs.get("flim_params") is None


def test_compress_one_token_config_with_all_tokens_disabled(
    tmp_path: Path,
) -> None:
    """A fully-disabled TokenConfig round-trips as None, not the string 'None'.

    Serializing ``None`` as a string would make the importer compile ``"None"``
    as a regex and match nothing, which is the silent-empty-.h5 failure again.
    """
    entry = _entry(
        tmp_path,
        token_config={
            "channel": None,
            "timepoint": None,
            "z_slice": None,
            "tile": None,
        },
    )

    with patch("percell4.adapters.importer.import_dataset") as mock_import:
        compress_one(entry)

    tc = mock_import.call_args.kwargs["token_config"]
    assert tc.channel is None
    assert tc.timepoint is None
    assert tc.z_slice is None
    assert tc.tile is None
