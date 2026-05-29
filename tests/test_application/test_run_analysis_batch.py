"""Tests for the analysis batch runner (U6)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from percell4.application.analysis import (
    BatchAnalysisItemResult,
    BatchAnalysisReport,
    batch_run_analysis,
)
from percell4.application.analysis import registry as registry_mod
from percell4.application.analysis.modules.per_particle_donut import (  # noqa: F401
    PerParticleDonut,
)
from percell4.store import DatasetStore


@pytest.fixture(autouse=True)
def _reregister_analysis() -> Iterator[None]:
    """Re-register PerParticleDonut after registry-clearing fixtures elsewhere.

    Other test files clear ``_REGISTRY`` in their own autouse fixtures; if
    they run before this file in the same session, the import-time decorator
    in ``application.analysis.modules.per_particle_donut`` will have fired
    once and the class will be gone. Reload the module to re-register.
    """
    if "per_particle_donut" not in registry_mod._REGISTRY:
        import importlib

        import percell4.application.analysis.modules.per_particle_donut as mod

        importlib.reload(mod)
    yield


# ── Synthetic h5 builders ────────────────────────────────────────────


def _toy_pbody_arrays(
    shape: tuple[int, int] = (32, 32),
    centers: tuple[tuple[int, int], ...] = ((8, 8), (24, 24)),
    radius: int = 3,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from skimage.draw import disk

    h, w = shape
    cap = np.full((h, w), 1000.0, dtype=np.float64)
    pnorm = np.full((h, w), 1200.0, dtype=np.float64)
    mask = np.zeros((h, w), dtype=np.uint8)
    for r, c in centers:
        rr, cc = disk((r, c), radius, shape=shape)
        cap[rr, cc] += 6000.0
        pnorm[rr, cc] += 4500.0
        mask[rr, cc] = 1
    return cap.astype(np.float32), pnorm.astype(np.float32), mask


def _build_pbody_h5(
    path: Path,
    *,
    cap: np.ndarray,
    pnorm: np.ndarray,
    pbody_mask: np.ndarray,
) -> None:
    intensity = np.stack([cap, pnorm], axis=0).astype(np.float32)
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": ["Cap", "pnorm"]})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})
    store.write_array("masks/pbody", pbody_mask.astype(np.uint8))


def _build_cap_only_h5(path: Path, *, cap: np.ndarray) -> None:
    """Dataset missing pnorm + masks — will fail group_requirement."""
    intensity = cap[np.newaxis, :, :].astype(np.float32)
    store = DatasetStore(path)
    store.create(metadata={"source": "test", "channel_names": ["Cap"]})
    store.write_array("intensity", intensity, attrs={"dims": ["C", "H", "W"]})


def _layer_map_pbody() -> dict[str, str]:
    return {"cap": "Cap", "pbody_mask": "pbody", "pnorm": "pnorm"}


# ── Happy path: 2 datasets, both succeed ────────────────────────────


def test_happy_path_two_datasets(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    _build_pbody_h5(a, cap=cap, pnorm=pnorm, pbody_mask=mask)
    _build_pbody_h5(b, cap=cap, pnorm=pnorm, pbody_mask=mask)

    out_parent = tmp_path / "out"

    report = batch_run_analysis(
        "per_particle_donut",
        [a, b],
        lambda _path: _layer_map_pbody(),
        output_parent=out_parent,
        preset="m7g-cap-v1",
    )

    assert isinstance(report, BatchAnalysisReport)
    assert len(report.items) == 2
    assert report.succeeded_count == 2
    assert report.failed_count == 0
    assert report.cancelled is False

    folder = report.run_folder
    assert folder.exists()
    assert folder.parent == out_parent
    assert folder.name.startswith("analysis_run_")

    combined = folder / "combined_pbody_table.csv"
    assert combined.exists()
    df = pd.read_csv(combined)
    assert df.columns[0] == "dataset"
    assert set(df["dataset"]) == {"a", "b"}

    per_a = folder / "per_dataset" / "a_pbody_table.csv"
    per_b = folder / "per_dataset" / "b_pbody_table.csv"
    assert per_a.exists()
    assert per_b.exists()

    config = json.loads((folder / "run_config.json").read_text())
    assert config["analysis_name"] == "per_particle_donut"
    assert config["preset_name"] == "m7g-cap-v1"
    assert config["preset_values"] is not None
    assert config["status"] == "completed"
    assert config["completed_dataset_count"] == 2
    assert len(config["dataset_paths"]) == 2
    assert all(Path(p).is_absolute() for p in config["dataset_paths"])


# ── Happy path: export_donuts writes masks back ────────────────────


def test_export_donuts_writes_mask_into_h5(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    h5 = tmp_path / "a.h5"
    _build_pbody_h5(h5, cap=cap, pnorm=pnorm, pbody_mask=mask)

    report = batch_run_analysis(
        "per_particle_donut",
        [h5],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        params={"export_donuts": True},
    )
    assert report.succeeded_count == 1
    item = report.items[0]
    assert "pbody_donut_mask" in item.produced_outputs

    store = DatasetStore(h5)
    assert "pbody_donut_mask" in store.list_masks()
    arr = store.read_mask("pbody_donut_mask")
    assert arr.dtype == np.uint8
    assert arr.shape == cap.shape


# ── Per-dataset failure isolates ────────────────────────────────────


def test_per_dataset_failure_isolates(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    bad = tmp_path / "bad.h5"
    good = tmp_path / "good.h5"
    _build_cap_only_h5(bad, cap=cap)  # no pbody_mask, no pnorm
    _build_pbody_h5(good, cap=cap, pnorm=pnorm, pbody_mask=mask)

    report = batch_run_analysis(
        "per_particle_donut",
        [bad, good],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
    )
    assert report.failed_count == 1
    assert report.succeeded_count == 1
    failed = next(i for i in report.items if i.status == "failed")
    assert failed.h5_path == bad
    assert failed.error is not None

    combined = report.run_folder / "combined_pbody_table.csv"
    df = pd.read_csv(combined)
    assert set(df["dataset"]) == {"good"}


# ── All datasets fail → no CSVs, run_config still written ──────────


def test_all_datasets_fail(tmp_path: Path) -> None:
    cap, _, _ = _toy_pbody_arrays()
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    _build_cap_only_h5(a, cap=cap)
    _build_cap_only_h5(b, cap=cap)

    report = batch_run_analysis(
        "per_particle_donut",
        [a, b],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
    )
    assert report.failed_count == 2
    assert report.succeeded_count == 0

    assert not (report.run_folder / "combined_pbody_table.csv").exists()
    assert (report.run_folder / "run_config.json").exists()
    config = json.loads((report.run_folder / "run_config.json").read_text())
    assert config["status"] == "completed"
    assert all(r["status"] == "failed" for r in config["results"])


# ── Cancel mid-run ──────────────────────────────────────────────────


def test_cancel_mid_run(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    c = tmp_path / "c.h5"
    for p in (a, b, c):
        _build_pbody_h5(p, cap=cap, pnorm=pnorm, pbody_mask=mask)

    calls = {"n": 0}

    def cancel_after_one() -> bool:
        # First call (before dataset 0): False. Second call (before dataset 1): True.
        n = calls["n"]
        calls["n"] += 1
        return n >= 1

    report = batch_run_analysis(
        "per_particle_donut",
        [a, b, c],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
        cancel_check=cancel_after_one,
    )
    assert report.cancelled is True
    assert len(report.items) == 1
    assert report.items[0].status == "succeeded"
    assert report.items[0].h5_path == a

    config = json.loads((report.run_folder / "run_config.json").read_text())
    assert config["status"] == "cancelled"
    assert config["completed_dataset_count"] == 1


# ── Progress callback fires once per dataset ───────────────────────


def test_progress_callback_fires_once_per_dataset(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    _build_pbody_h5(a, cap=cap, pnorm=pnorm, pbody_mask=mask)
    _build_pbody_h5(b, cap=cap, pnorm=pnorm, pbody_mask=mask)

    captured: list[tuple[BatchAnalysisItemResult, int, int]] = []

    def on_progress(item: BatchAnalysisItemResult, idx: int, total: int) -> None:
        captured.append((item, idx, total))

    batch_run_analysis(
        "per_particle_donut",
        [a, b],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
        progress_callback=on_progress,
    )
    assert [c[1] for c in captured] == [0, 1]
    assert all(c[2] == 2 for c in captured)
    assert all(c[0].status == "succeeded" for c in captured)


# ── Empty h5_paths is a usage error ────────────────────────────────


def test_empty_h5_paths_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one h5_path"):
        batch_run_analysis(
            "per_particle_donut",
            [],
            lambda _path: _layer_map_pbody(),
            output_parent=tmp_path / "out",
            preset="m7g-cap-v1",
        )


# ── layer_map_resolver raises → per-dataset failure ────────────────


def test_layer_map_resolver_failure_isolates(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    h5 = tmp_path / "a.h5"
    _build_pbody_h5(h5, cap=cap, pnorm=pnorm, pbody_mask=mask)

    def bad_resolver(_path: Path) -> dict[str, str]:
        raise RuntimeError("dialog state corrupt")

    report = batch_run_analysis(
        "per_particle_donut",
        [h5],
        bad_resolver,
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
    )
    assert report.failed_count == 1
    err = report.items[0].error
    assert err is not None
    assert "layer_map_resolver raised" in err


# ── run_config.json layer_map captures the per-dataset map ─────────


def test_run_config_records_per_dataset_layer_map(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    h5 = tmp_path / "a.h5"
    _build_pbody_h5(h5, cap=cap, pnorm=pnorm, pbody_mask=mask)

    report = batch_run_analysis(
        "per_particle_donut",
        [h5],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
    )
    config = json.loads((report.run_folder / "run_config.json").read_text())
    assert config["layer_map_per_dataset"] == {"a": _layer_map_pbody()}


# ── dataset_column_label override (U1) ─────────────────────────────


def test_dataset_column_label_defaults_to_dataset(tmp_path: Path) -> None:
    """Default label is 'dataset' — the unchanged contract for all analyses."""
    cap, pnorm, mask = _toy_pbody_arrays()
    h5 = tmp_path / "a.h5"
    _build_pbody_h5(h5, cap=cap, pnorm=pnorm, pbody_mask=mask)

    report = batch_run_analysis(
        "per_particle_donut",
        [h5],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
    )
    combined = pd.read_csv(report.run_folder / "combined_pbody_table.csv")
    assert combined.columns[0] == "dataset"
    assert "group" not in combined.columns


def test_dataset_column_label_override_renames_id_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An analysis overriding dataset_column_label gets that name as the id
    column in both combined + per-dataset CSVs, and the per-dataset filename
    stem is still derived correctly from the renamed column."""
    monkeypatch.setattr(PerParticleDonut, "dataset_column_label", "group")

    cap, pnorm, mask = _toy_pbody_arrays()
    a = tmp_path / "a.h5"
    b = tmp_path / "b.h5"
    _build_pbody_h5(a, cap=cap, pnorm=pnorm, pbody_mask=mask)
    _build_pbody_h5(b, cap=cap, pnorm=pnorm, pbody_mask=mask)

    report = batch_run_analysis(
        "per_particle_donut",
        [a, b],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        preset="m7g-cap-v1",
    )

    combined = pd.read_csv(report.run_folder / "combined_pbody_table.csv")
    assert combined.columns[0] == "group"
    assert "dataset" not in combined.columns
    assert set(combined["group"]) == {"a", "b"}

    # Per-dataset filenames still use the .h5 stem (read from 'group').
    assert (report.run_folder / "per_dataset" / "a_pbody_table.csv").exists()
    assert (report.run_folder / "per_dataset" / "b_pbody_table.csv").exists()
    per_a = pd.read_csv(
        report.run_folder / "per_dataset" / "a_pbody_table.csv"
    )
    assert per_a.columns[0] == "group"
    assert set(per_a["group"]) == {"a"}


# ── Params override mode (no preset) ───────────────────────────────


def test_params_only_no_preset(tmp_path: Path) -> None:
    cap, pnorm, mask = _toy_pbody_arrays()
    h5 = tmp_path / "a.h5"
    _build_pbody_h5(h5, cap=cap, pnorm=pnorm, pbody_mask=mask)

    report = batch_run_analysis(
        "per_particle_donut",
        [h5],
        lambda _path: _layer_map_pbody(),
        output_parent=tmp_path / "out",
        params={"buffer": 3, "donut": 4},
    )
    assert report.succeeded_count == 1
    config = json.loads((report.run_folder / "run_config.json").read_text())
    assert config["preset_name"] is None
    assert config["preset_values"] is None
    assert config["params"]["buffer"] == 3
    assert config["params"]["donut"] == 4
