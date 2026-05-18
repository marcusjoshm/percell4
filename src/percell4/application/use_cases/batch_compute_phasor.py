"""Use case: batch compute phasor + apply wavelet across many ``.h5`` datasets.

Thin orchestration around :class:`ComputePhasor` and :class:`ApplyWavelet`.
Per dataset, the orchestrator:

1. Opens the ``.h5`` via :class:`Hdf5DatasetRepository` and builds a fresh
   :class:`Session` with the resulting handle.
2. Enumerates channels under ``/decay/*`` via ``store.list_groups("decay")``.
3. For each channel, runs two pre-flight skip checks:
   - **Existing phasor:** ``/phasor/<ch>/g`` present and ``overwrite=False``
     → skip with a structured reason.
   - **Missing calibration:** any of ``flim_cal_phase_<ch>``,
     ``flim_cal_mod_<ch>``, ``flim_frequency_mhz`` absent from
     ``/metadata`` → skip with a structured reason.
4. Otherwise calls
   :meth:`ComputePhasor.execute` (writes ``/phasor/<ch>/g`` and ``/s``)
   then :meth:`ApplyWavelet.execute` (writes ``g_filtered``,
   ``s_filtered``, ``lifetime_filtered``).
5. Collects per-channel results into a :class:`BatchPhasorItemResult`,
   classifies the dataset's overall status, fires the
   ``progress_callback`` (if provided), and continues.

``view_bin=1`` is passed explicitly at every ``ComputePhasor.execute``
and ``ApplyWavelet.execute`` call site -- batch runs are headless and
never see a session-level view bin, but the explicit kwarg makes the
orchestrator's posture grep-visible (per
``docs/solutions/integration-issues/phasor-view-bin-not-forwarded-from-gui-callers-2026-05-18.md``).

The orchestrator is pure-Python and Session-free at the boundary -- it
constructs a Session per dataset internally but never exposes Qt or
``CellDataModel``. Per-channel failures isolate; per-dataset failures
isolate; the loop continues. The returned :class:`BatchPhasorReport`
carries truthful partial state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from percell4.adapters.hdf5_store import Hdf5DatasetRepository
from percell4.application.session import Session
from percell4.application.use_cases.apply_wavelet import ApplyWavelet
from percell4.application.use_cases.compute_phasor import ComputePhasor
from percell4.store import DatasetStore

logger = logging.getLogger(__name__)


# ── Result + report dataclasses ─────────────────────────────────────────


ITEM_STATUSES: tuple[str, ...] = (
    "succeeded",
    "partial",
    "skipped_no_changes",
    "failed",
)


@dataclass(frozen=True)
class BatchPhasorItemResult:
    """Outcome of one dataset in a batch run.

    ``processed`` lists channel names that landed both
    ``/phasor/<ch>/{g, s}`` and the wavelet outputs.

    ``skipped`` and ``errors`` are channel-keyed dictionaries -- the same
    channel cannot appear in both. ``error`` (singular) is the
    dataset-level failure message when the run could not even reach the
    channel loop (file missing, intensity missing, etc.).
    """

    h5_path: Path
    status: str  # one of ITEM_STATUSES
    processed: tuple[str, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class BatchPhasorReport:
    """Aggregated result of a batch phasor + wavelet run."""

    items: tuple[BatchPhasorItemResult, ...] = ()

    @property
    def total_succeeded(self) -> int:
        return sum(1 for r in self.items if r.status == "succeeded")

    @property
    def total_partial(self) -> int:
        return sum(1 for r in self.items if r.status == "partial")

    @property
    def total_failed(self) -> int:
        return sum(1 for r in self.items if r.status == "failed")

    @property
    def total_skipped(self) -> int:
        return sum(1 for r in self.items if r.status == "skipped_no_changes")


# ── Pre-flight helpers ──────────────────────────────────────────────────


def _missing_calibration_reason(
    metadata: dict, channel: str
) -> str | None:
    """Return a human-readable reason if calibration is incomplete.

    Required keys for a calibrated phasor + wavelet:
      * ``flim_cal_phase_<channel>``
      * ``flim_cal_mod_<channel>``
      * ``flim_frequency_mhz``

    Returns ``None`` when all three are present, else the first missing
    key wrapped in a "missing calibration (<key>)" string.
    """
    for key in (
        f"flim_cal_phase_{channel}",
        f"flim_cal_mod_{channel}",
        "flim_frequency_mhz",
    ):
        if key not in metadata:
            return f"missing calibration ({key})"
    return None


def _phasor_exists(store: DatasetStore, channel: str) -> bool:
    """Return True if ``/phasor/<channel>/g`` is on disk."""
    import h5py

    try:
        with h5py.File(store.path, "r") as f:
            return f"phasor/{channel}/g" in f
    except OSError:
        return False


# ── Main entry point ────────────────────────────────────────────────────


def batch_compute_phasor(
    h5_paths: list[Path],
    *,
    filter_level: int = 9,
    overwrite: bool = False,
    progress_callback: Callable[[BatchPhasorItemResult], None] | None = None,
) -> BatchPhasorReport:
    """Compute phasor + apply wavelet across ``h5_paths``.

    Iterates each ``.h5`` and, for every channel under ``/decay/*``,
    runs ComputePhasor then ApplyWavelet at native resolution
    (``view_bin=1``). Per-channel and per-dataset failures isolate;
    the loop continues.

    Args:
        h5_paths: Datasets to process. Each must already have at least
            one ``/decay/<ch>`` and corresponding ``/metadata``
            calibration attrs (``flim_cal_phase_<ch>``,
            ``flim_cal_mod_<ch>``, ``flim_frequency_mhz``). Datasets
            missing these are reported as skipped, not as failures.
        filter_level: Wavelet filter level (1..9). Same value applied
            to every channel of every dataset.
        overwrite: If False (default), channels with an existing
            ``/phasor/<ch>/g`` are skipped. If True, recompute and
            overwrite. ``ComputePhasor`` itself already invalidates
            stale ``g_filtered`` / ``s_filtered`` / ``lifetime_filtered``
            on recompute, so this flag is sufficient.
        progress_callback: Invoked once per dataset after its
            :class:`BatchPhasorItemResult` is classified.

    Returns:
        A :class:`BatchPhasorReport` with one
        :class:`BatchPhasorItemResult` per input path.
    """
    if not (1 <= filter_level <= 9):
        raise ValueError(
            f"filter_level must be in [1, 9], got {filter_level}"
        )

    repo = Hdf5DatasetRepository()
    results: list[BatchPhasorItemResult] = []

    for h5_path in h5_paths:
        result = _process_one_dataset(
            h5_path=h5_path,
            repo=repo,
            filter_level=filter_level,
            overwrite=overwrite,
        )
        results.append(result)
        if progress_callback is not None:
            progress_callback(result)

    return BatchPhasorReport(items=tuple(results))


def _process_one_dataset(
    *,
    h5_path: Path,
    repo: Hdf5DatasetRepository,
    filter_level: int,
    overwrite: bool,
) -> BatchPhasorItemResult:
    """Run phasor + wavelet for every channel of one dataset.

    Catches dataset-level exceptions (missing file, bad HDF5) and
    per-channel exceptions independently. Returns a classified
    :class:`BatchPhasorItemResult`.
    """
    try:
        handle = repo.open(h5_path)
    except Exception as exc:  # noqa: BLE001 — orchestrator never raises per-item
        return BatchPhasorItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"open failed: {exc}",
        )

    session = Session()
    session.set_dataset(handle)
    store = DatasetStore(h5_path)

    try:
        channels = store.list_groups("decay")
    except Exception as exc:  # noqa: BLE001
        return BatchPhasorItemResult(
            h5_path=h5_path,
            status="failed",
            error=f"decay enumeration failed: {exc}",
        )

    if not channels:
        return BatchPhasorItemResult(
            h5_path=h5_path,
            status="skipped_no_changes",
            skipped={"_dataset": "no decay channels"},
        )

    metadata = repo.read_metadata(handle)

    processed: list[str] = []
    skipped: dict[str, str] = {}
    errors: dict[str, str] = {}

    phasor_uc = ComputePhasor(repo, session)
    wavelet_uc = ApplyWavelet(repo, session)

    for channel in channels:
        # Pre-flight skip: existing phasor + no overwrite.
        if not overwrite and _phasor_exists(store, channel):
            skipped[channel] = "phasor exists (use --overwrite to recompute)"
            continue

        # Pre-flight skip: missing calibration.
        missing = _missing_calibration_reason(metadata, channel)
        if missing is not None:
            skipped[channel] = missing
            continue

        # Run compute + wavelet. Either step can fail per channel.
        try:
            phasor_uc.execute(channel=channel, harmonic=1, view_bin=1)
        except Exception as exc:  # noqa: BLE001 — per-channel isolation
            errors[channel] = f"compute_phasor: {exc}"
            continue

        try:
            wavelet_uc.execute(
                channel=channel, filter_level=filter_level, view_bin=1,
            )
        except Exception as exc:  # noqa: BLE001 — per-channel isolation
            # /phasor/<ch>/g + s landed but g_filtered didn't. Record
            # the wavelet failure; the channel is in a half-written
            # state. User can rerun with --overwrite to retry.
            errors[channel] = f"apply_wavelet: {exc}"
            continue

        processed.append(channel)

    status = _classify_status(
        channels=channels, processed=processed, errors=errors, skipped=skipped,
    )

    return BatchPhasorItemResult(
        h5_path=h5_path,
        status=status,
        processed=tuple(processed),
        skipped=skipped,
        errors=errors,
    )


def _classify_status(
    *,
    channels: list[str],
    processed: list[str],
    errors: dict[str, str],
    skipped: dict[str, str],
) -> str:
    """Pick the right ITEM_STATUSES value for one dataset's outcome."""
    if not processed and not errors:
        # Everything was skipped (or there were no channels, but the
        # caller already short-circuited that case).
        return "skipped_no_changes"
    if not processed and errors:
        # Every attempted channel failed.
        return "failed"
    if processed and not (errors or skipped):
        # Clean run -- every channel landed.
        return "succeeded"
    # Mixed outcome.
    return "partial"
