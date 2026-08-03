"""Post-compress validation gate for freshly imported datasets.

``import_dataset`` does not raise when no source file matches the channel
token pattern. It writes an ``.h5`` with ``channel_names == []``,
``n_channels == 0`` and no ``/intensity``, and ``compress_one`` reports
success. Without a gate the run continued against that empty dataset: the
segmentation-channel lookup logged "falling back to 0" and the real failure
surfaced minutes later in an unrelated phase.

The gate must be no *stricter* than what later phases already require — it
moves an existing failure earlier, it must never reject a dataset that would
otherwise have worked.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.store import DatasetStore
from percell4.workflows.models import (
    AdaptiveClipSettings,
    AutoExtractSettings,
    ThresholdAlgorithm,
    ThresholdingRound,
)
from percell4.workflows.phases import (
    config_needs_pixel_size,
    validate_compressed_dataset,
)


def _store(
    path: Path,
    channels: list[str] | None = None,
    *,
    pixel_size_um: float | None = None,
    with_intensity: bool = True,
) -> DatasetStore:
    store = DatasetStore(path)
    meta: dict = {"channel_names": list(channels or [])}
    if pixel_size_um is not None:
        meta["pixel_size_um"] = pixel_size_um
    store.create(metadata=meta)
    if with_intensity and channels:
        store.write_array(
            "intensity",
            np.zeros((len(channels), 8, 8), dtype=np.float32),
            attrs={"dims": ["C", "H", "W"]},
        )
    return store


def _round(name="R1", channel="GFP", **kw) -> ThresholdingRound:
    base = dict(
        name=name,
        channel=channel,
        metric="mean_intensity",
        algorithm=ThresholdAlgorithm.KMEANS,
        kmeans_n_clusters=2,
    )
    base.update(kw)
    return ThresholdingRound(**base)


# ── Happy path ──────────────────────────────────────────────────────────


def test_usable_dataset_passes(tmp_path: Path) -> None:
    store = _store(tmp_path / "ok.h5", ["GFP", "RFP"])
    assert (
        validate_compressed_dataset(
            store, seg_channel_name="GFP", round_channels=["RFP"]
        )
        is None
    )


def test_no_named_channels_required_passes(tmp_path: Path) -> None:
    """An empty seg name and no rounds still passes when channels exist."""
    store = _store(tmp_path / "ok.h5", ["GFP"])
    assert validate_compressed_dataset(store) is None


# ── The reported failure ────────────────────────────────────────────────


def test_empty_channel_names_is_rejected(tmp_path: Path) -> None:
    """The silent-empty-.h5 state must fail here, not five phases later."""
    store = _store(tmp_path / "empty.h5", [], with_intensity=False)

    problem = validate_compressed_dataset(store, seg_channel_name="GFP")

    assert problem is not None
    assert "no channels" in problem
    # The message must point at the actual cause the researcher can act on.
    assert "naming pattern" in problem


def test_missing_segmentation_channel_is_named(tmp_path: Path) -> None:
    store = _store(tmp_path / "ds.h5", ["RFP"])

    problem = validate_compressed_dataset(store, seg_channel_name="GFP")

    assert problem is not None
    assert "'GFP'" in problem
    assert "segmentation channel" in problem
    assert "['RFP']" in problem


def test_missing_round_channel_is_named(tmp_path: Path) -> None:
    store = _store(tmp_path / "ds.h5", ["GFP"])

    problem = validate_compressed_dataset(
        store, seg_channel_name="GFP", round_channels=["RFP"]
    )

    assert problem is not None
    assert "round channel 'RFP'" in problem


def test_duplicate_round_channels_reported_once(tmp_path: Path) -> None:
    store = _store(tmp_path / "ds.h5", ["GFP"])

    problem = validate_compressed_dataset(
        store, round_channels=["RFP", "RFP", "RFP"]
    )

    assert problem is not None
    assert problem.count("'RFP'") == 1


def test_unreadable_store_is_a_dataset_failure_not_a_raise(
    tmp_path: Path,
) -> None:
    """A corrupt/missing file must return a message, never propagate.

    A raising handler terminates the whole run under BaseWorkflowRunner,
    turning a one-dataset problem into a batch-wide abort.
    """
    problem = validate_compressed_dataset(DatasetStore(tmp_path / "nope.h5"))

    assert problem is not None
    assert "cannot read" in problem


# ── Pixel size: conditional, never defaulted ────────────────────────────


def test_pixel_size_required_only_when_a_round_uses_microns(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "ds.h5", ["GFP"])  # no pixel_size_um

    assert validate_compressed_dataset(store, needs_pixel_size=False) is None

    problem = validate_compressed_dataset(store, needs_pixel_size=True)
    assert problem is not None
    assert "µm" in problem


def test_present_pixel_size_satisfies_the_check(tmp_path: Path) -> None:
    store = _store(tmp_path / "ds.h5", ["GFP"], pixel_size_um=0.120369)
    assert validate_compressed_dataset(store, needs_pixel_size=True) is None


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_pixel_size_is_rejected(tmp_path: Path, bad: float) -> None:
    """Never accept a degenerate pixel size and never default to 1."""
    store = _store(tmp_path / f"ds{bad}.h5", ["GFP"], pixel_size_um=bad)
    assert validate_compressed_dataset(store, needs_pixel_size=True) is not None


# ── needs_pixel_size predicate ──────────────────────────────────────────


def test_px_only_rounds_need_no_pixel_size() -> None:
    rounds = [
        _round(),
        _round(name="R2", min_particle_size=5, min_particle_size_unit="px"),
        _round(
            name="R3",
            adaptive_clip=AdaptiveClipSettings(d_min_um=3.0, d_min_unit="px"),
        ),
    ]
    assert config_needs_pixel_size(rounds) is False


def test_micron_adaptive_clip_needs_pixel_size() -> None:
    rounds = [
        _round(
            adaptive_clip=AdaptiveClipSettings(d_min_um=0.8, d_min_unit="um"),
        )
    ]
    assert config_needs_pixel_size(rounds) is True


def test_micron_squared_min_size_needs_pixel_size() -> None:
    rounds = [_round(min_particle_size=0.5, min_particle_size_unit="um2")]
    assert config_needs_pixel_size(rounds) is True


def test_auto_extract_micron_override_needs_pixel_size() -> None:
    rounds = [
        _round(
            auto_extract=AutoExtractSettings(
                smallest_particle_um=0.4, smallest_particle_unit="um"
            )
        )
    ]
    assert config_needs_pixel_size(rounds) is True


def test_auto_extract_autodetect_needs_no_pixel_size() -> None:
    """Auto-detect is px-native — requiring a pixel size would over-reject."""
    rounds = [_round(auto_extract=AutoExtractSettings())]
    assert config_needs_pixel_size(rounds) is False


def test_empty_round_list_needs_no_pixel_size() -> None:
    assert config_needs_pixel_size([]) is False
