"""Tests for the batch_export_phasor use case.

End-to-end against real HDF5 files written via DatasetStore; the
renderer hits the real Agg backend and writes real PNGs under
tmp_path. No mocking of the use case. The only monkeypatched seam is
``render_phasor_png`` in the two tests that need to force a render
exception or assert empty-signal propagation deterministically.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from percell4.application.phasor_render import RenderOutcome
from percell4.application.use_cases import batch_export_phasor as mod
from percell4.application.use_cases.batch_export_phasor import (
    BatchPhasorExportReport,
    batch_export_phasor,
)
from percell4.store import DatasetStore

# ── Fixture builders ────────────────────────────────────────────────────


def _gs(shape=(8, 8)):
    rng = np.random.default_rng(1)
    g = rng.uniform(0.1, 0.9, shape).astype(np.float32)
    s = rng.uniform(0.05, 0.5, shape).astype(np.float32)
    return g, s


def _make_h5(
    path: Path,
    *,
    channels: dict[str, dict] | None = None,
) -> Path:
    """Write an .h5 with per-channel phasor content.

    channels: {ch: {"filtered": bool, "decay_shape": tuple|None}}
    decay_shape None => no /decay/<ch>. Spatial dims of decay drive
    decay.sum(-1) size for the alignment check.
    """
    store = DatasetStore(path)
    store.create(metadata={})
    for ch, spec in (channels or {}).items():
        g, s = _gs()
        store.write_array(f"phasor/{ch}/g", g)
        store.write_array(f"phasor/{ch}/s", s)
        if spec.get("filtered"):
            store.write_array(f"phasor/{ch}/g_filtered", g * 0.9)
            store.write_array(f"phasor/{ch}/s_filtered", s * 0.9)
        if spec.get("g_filtered_only"):
            store.write_array(f"phasor/{ch}/g_filtered", g * 0.9)
        dshape = spec.get("decay_shape", (8, 8, 16))
        if dshape is not None:
            decay = np.ones(dshape, dtype=np.uint16)
            store.write_array(f"decay/{ch}", decay, is_decay=True)
    return path


# ── Happy paths ─────────────────────────────────────────────────────────


def test_two_channels_raw_and_filtered_four_pngs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels={
            "ch0": {"filtered": True},
            "ch1": {"filtered": True},
        },
    )

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert item.status == "succeeded"
    assert item.files_written == 4
    assert report.total_files_written == 4
    written = sorted(p.name for p in out.iterdir())
    assert written == [
        "ds_ch0_phasor.png",
        "ds_ch0_phasor_filtered.png",
        "ds_ch1_phasor.png",
        "ds_ch1_phasor_filtered.png",
    ]


def test_raw_only_channel_no_filtered_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5", channels={"ch0": {"filtered": False}}
    )

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert item.status == "succeeded"
    assert item.files_written == 1
    assert [p.name for p in out.iterdir()] == ["ds_ch0_phasor.png"]


# ── Skip / no-op paths ──────────────────────────────────────────────────


def test_no_phasor_group_skipped_no_changes(tmp_path: Path) -> None:
    h5 = tmp_path / "empty.h5"
    DatasetStore(h5).create(metadata={})

    report = batch_export_phasor([h5], output_dir=tmp_path / "out")

    (item,) = report.items
    assert item.status == "skipped_no_changes"
    assert item.files_written == 0
    assert "_dataset" in item.skipped


def test_asymmetric_filtered_cache_records_structured_skip(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels={"ch0": {"g_filtered_only": True}},
    )

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert item.status == "succeeded"
    assert item.files_written == 1  # raw only
    assert "ch0_filtered" in item.skipped
    assert "asymmetric" in item.skipped["ch0_filtered"]
    assert not (out / "ds_ch0_phasor_filtered.png").exists()


def test_asymmetric_raw_cache_g_without_s_is_skipped(
    tmp_path: Path,
) -> None:
    """phasor/ch0/g present, phasor/ch0/s absent -> channel skipped
    (asymmetric raw cache), no PNG, no error."""
    out = tmp_path / "out"
    h5 = tmp_path / "ds.h5"
    store = DatasetStore(h5)
    store.create(metadata={})
    g, _ = _gs()
    store.write_array("phasor/ch0/g", g)  # no /s

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert "ch0" in item.skipped
    assert "asymmetric" in item.skipped["ch0"]
    assert item.errors == {}
    assert item.files_written == 0
    assert not (out.exists() and any(out.iterdir()))


def test_all_channels_error_is_failed_not_skipped(tmp_path: Path) -> None:
    """Every channel hits a genuine error (stale decay) and zero files
    written -> status 'failed' and total_failed counts it (not
    'skipped_no_changes', which would undercount real failures)."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels={
            "ch0": {"decay_shape": (4, 4, 16)},  # mismatch -> error
            "ch1": {"decay_shape": (2, 2, 16)},  # mismatch -> error
        },
    )

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert item.status == "failed"
    assert set(item.errors) == {"ch0", "ch1"}
    assert item.files_written == 0
    assert report.total_failed == 1
    assert report.total_skipped == 0


def test_raw_render_failure_does_not_skip_filtered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T1: a channel with filtered maps whose RAW render raises still
    gets its filtered PNG written; the raw failure is recorded per
    output, not as a whole-channel error."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5", channels={"ch0": {"filtered": True}}
    )

    real = mod.render_phasor_png

    def flaky(g, s, *, out_path, intensity=None, title=None):
        if str(out_path).endswith("_ch0_phasor.png"):  # raw only
            raise RuntimeError("raw boom")
        return real(
            g, s, out_path=out_path, intensity=intensity, title=title
        )

    monkeypatch.setattr(mod, "render_phasor_png", flaky)

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert "ds_ch0_phasor.png" in item.errors
    assert "raw boom" in item.errors["ds_ch0_phasor.png"]
    # Filtered PNG still produced despite the raw failure.
    assert (out / "ds_ch0_phasor_filtered.png").exists()
    assert item.channels_exported == ("ds_ch0_phasor_filtered.png",)
    assert item.status == "succeeded"


def test_channel_without_decay_renders_unweighted(tmp_path: Path) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels={"ch0": {"decay_shape": None}},
    )

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert item.status == "succeeded"
    assert item.files_written == 1
    assert item.errors == {}


# ── Alignment enforcement ───────────────────────────────────────────────


def test_decay_g_shape_mismatch_is_error_not_silent(
    tmp_path: Path,
) -> None:
    """Stale phasor: decay.sum(-1) size != g size -> per-channel error,
    no PNG for that channel; a sibling aligned channel still exports."""
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels={
            "ch0": {"decay_shape": (4, 4, 16)},  # 16 != g.size 64
            "ch1": {"decay_shape": (8, 8, 16)},  # aligned
        },
    )

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert "ch0" in item.errors
    assert "stale" in item.errors["ch0"]
    assert not (out / "ds_ch0_phasor.png").exists()
    # Sibling channel unaffected.
    assert (out / "ds_ch1_phasor.png").exists()
    assert item.status == "succeeded"


# ── R9: empty-phasor signal propagation ─────────────────────────────────


def test_all_nan_phasor_recorded_in_rendered_empty(tmp_path: Path) -> None:
    out = tmp_path / "out"
    h5 = tmp_path / "ds.h5"
    store = DatasetStore(h5)
    store.create(metadata={})
    nan = np.full((8, 8), np.nan, dtype=np.float32)
    store.write_array("phasor/ch0/g", nan)
    store.write_array("phasor/ch0/s", nan)

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    assert item.status == "succeeded"  # PNG still written
    assert (out / "ds_ch0_phasor.png").exists()
    assert "ds_ch0_phasor.png" in item.rendered_empty
    assert report.total_rendered_empty == 1


# ── Error / isolation paths ─────────────────────────────────────────────


def test_missing_h5_path_is_failed_loop_continues(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    missing = tmp_path / "nope.h5"
    ok = _make_h5(tmp_path / "ok.h5", channels={"ch0": {}})

    report = batch_export_phasor([missing, ok], output_dir=out)

    miss_item, ok_item = report.items
    assert miss_item.status == "failed"
    assert miss_item.error is not None
    assert ok_item.status == "succeeded"
    assert report.total_failed == 1
    assert report.total_succeeded == 1


def test_renderer_exception_routed_to_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    h5 = _make_h5(
        tmp_path / "ds.h5",
        channels={"ch0": {}, "ch1": {}},
    )

    real = mod.render_phasor_png

    def flaky(g, s, *, out_path, intensity=None, title=None):
        if "ch0" in str(out_path):
            raise RuntimeError("boom")
        return real(
            g, s, out_path=out_path, intensity=intensity, title=title
        )

    monkeypatch.setattr(mod, "render_phasor_png", flaky)

    report = batch_export_phasor([h5], output_dir=out)

    (item,) = report.items
    # Render failures are keyed by the per-output filename (T1), not the
    # bare channel — so raw vs filtered failures stay distinguishable.
    assert "ds_ch0_phasor.png" in item.errors
    assert "boom" in item.errors["ds_ch0_phasor.png"]
    # ch1 still exported despite ch0 raising.
    assert (out / "ds_ch1_phasor.png").exists()
    assert item.status == "succeeded"


# ── Integration: callback + aggregate report ────────────────────────────


def test_progress_callback_invoked_once_per_path(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    a = _make_h5(tmp_path / "a.h5", channels={"ch0": {}})
    b = _make_h5(tmp_path / "b.h5", channels={"ch0": {}})

    seen: list = []
    batch_export_phasor(
        [a, b], output_dir=out, progress_callback=seen.append
    )

    assert len(seen) == 2
    assert all(
        isinstance(x, mod.BatchPhasorExportItemResult) for x in seen
    )


def test_mixed_batch_aggregate_properties(tmp_path: Path) -> None:
    out = tmp_path / "out"
    good = _make_h5(tmp_path / "good.h5", channels={"ch0": {}})
    empty_ds = tmp_path / "noph.h5"
    DatasetStore(empty_ds).create(metadata={})
    nan_h5 = tmp_path / "nan.h5"
    st = DatasetStore(nan_h5)
    st.create(metadata={})
    nan = np.full((8, 8), np.nan, dtype=np.float32)
    st.write_array("phasor/ch0/g", nan)
    st.write_array("phasor/ch0/s", nan)
    missing = tmp_path / "gone.h5"

    report: BatchPhasorExportReport = batch_export_phasor(
        [good, empty_ds, nan_h5, missing], output_dir=out
    )

    assert report.total_succeeded == 2  # good + nan (PNG written)
    assert report.total_skipped == 1  # no /phasor
    assert report.total_failed == 1  # missing file
    assert report.total_files_written == 2
    assert report.total_rendered_empty == 1


def test_render_outcome_enum_values_are_stable() -> None:
    # Guards the contract U2 depends on from U1.
    assert RenderOutcome.RENDERED_WITH_DATA.value == "rendered_with_data"
    assert RenderOutcome.RENDERED_EMPTY.value == "rendered_empty"
