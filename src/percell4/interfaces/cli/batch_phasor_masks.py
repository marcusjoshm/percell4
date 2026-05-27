"""CLI adapter: batch fit-ellipse + dual-threshold phasor masks across .h5 datasets.

Headless front-end for
:func:`percell4.application.use_cases.batch_fit_phasor_masks.batch_fit_phasor_masks`.

For each input ``.h5`` (or every ``.h5`` in a directory argument), the
CLI fits a GMM ellipse on the phasor cloud (above ``--t-fit``) for each
requested channel, then writes two intensity-thresholded ellipse-membership
masks: ``{channel}{--suffix-a}`` (gated by ``--t-mask-a``) and
``{channel}{--suffix-b}`` (gated by ``--t-mask-b``).

Up-front validation runs before any I/O so the user gets a clean exit-2
error rather than partial side-effects:

- **Channel intersection.** Every requested channel must be present in
  every input dataset (both in ``metadata.channel_names`` and under
  ``/decay/``). A missing channel on any dataset aborts the batch.
- **Suffix sanity.** Both suffixes must be non-empty and must differ.
- **Collision.** For each ``(dataset, channel)`` pair, neither
  ``{channel}{suffix_a}`` nor ``{channel}{suffix_b}`` may already exist
  as a channel name in that dataset.

``--dry-run`` prints the planned operations and exits 0 without
invoking the use case.

Exit codes:
    0 -- at least one channel was processed across the batch (or
         ``--dry-run`` succeeded)
    1 -- every dataset failed or was skipped (no progress made)
    2 -- up-front validation failed (no I/O performed)

Programmatic use::

    from percell4.interfaces.cli.batch_phasor_masks import main
    exit_code = main([
        "dish_1.h5", "--channels", "mNG", "mScarlet",
        "--t-fit", "20.0",
    ])
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import h5py

from percell4.application.use_cases.batch_compute_phasor import (
    BatchPhasorItemResult,
)
from percell4.application.use_cases.batch_fit_phasor_masks import (
    batch_fit_phasor_masks,
)
from percell4.interfaces.cli._batch_report import (
    print_item_status,
    resolve_paths,
)

logger = logging.getLogger(__name__)


# ── Per-dataset preflight inspection ──────────────────────────────────


def _dataset_channels(h5_path: Path) -> set[str]:
    """Return ``metadata.channel_names ∩ /decay/*`` for one .h5.

    Both sides of the intersection are normalized to ``str`` (h5py
    returns ``bytes`` for fixed-length string attrs). Missing files or
    malformed datasets surface as an empty set; the caller's intersection
    check then reports the channel as missing.
    """
    meta_names: set[str] = set()
    decay_names: set[str] = set()
    try:
        with h5py.File(h5_path, "r") as f:
            if "metadata" in f:
                raw = f["metadata"].attrs.get("channel_names", [])
                # h5py returns ndarray of bytes / str depending on storage.
                if isinstance(raw, (bytes, str)):
                    raw = [raw]
                else:
                    raw = list(raw)
                meta_names = {
                    (n.decode() if isinstance(n, bytes) else str(n))
                    for n in raw
                }
            if "decay" in f:
                decay_names = {str(k) for k in f["decay"].keys()}
    except (OSError, KeyError):
        return set()
    return meta_names & decay_names


# ── Validation ────────────────────────────────────────────────────────


def _validate(
    *,
    paths: list[Path],
    channels: list[str],
    suffix_a: str,
    suffix_b: str,
) -> str | None:
    """Run all up-front validation. Return an error message on failure,
    ``None`` on success.

    Order matters: cheap, no-I/O checks (suffix sanity) run first; the
    per-file inspection (channel intersection + collision) runs only
    after those pass.
    """
    # ── Suffix sanity (cheap, no I/O) ──
    if suffix_a == "":
        return "--suffix-a must be non-empty"
    if suffix_b == "":
        return "--suffix-b must be non-empty"
    if suffix_a == suffix_b:
        return (
            f"--suffix-a and --suffix-b must differ "
            f"(both are {suffix_a!r})"
        )

    # ── Per-dataset inspection (one open per file) ──
    # Cache each dataset's channel set so the intersection check and the
    # collision check share one read.
    channel_sets: dict[Path, set[str]] = {}
    for p in paths:
        channel_sets[p] = _dataset_channels(p)

    # ── Channel intersection ──
    for ch in channels:
        missing_from: list[Path] = [
            p for p in paths if ch not in channel_sets[p]
        ]
        if missing_from:
            names = ", ".join(p.name for p in missing_from)
            return (
                f"channel {ch!r} is missing from dataset(s): {names}"
            )

    # ── Collision check ──
    for p in paths:
        present = channel_sets[p]
        for ch in channels:
            mask_a_name = f"{ch}{suffix_a}"
            mask_b_name = f"{ch}{suffix_b}"
            if mask_a_name in present:
                return (
                    f"mask name {mask_a_name!r} already exists as a "
                    f"channel in {p.name} (dataset={p}, channel={ch}, "
                    f"suffix={suffix_a!r})"
                )
            if mask_b_name in present:
                return (
                    f"mask name {mask_b_name!r} already exists as a "
                    f"channel in {p.name} (dataset={p}, channel={ch}, "
                    f"suffix={suffix_b!r})"
                )

    return None


# ── Dry-run printer ───────────────────────────────────────────────────


def _print_dry_run_plan(
    *,
    paths: list[Path],
    channels: list[str],
    t_fit: float,
    t_mask_a: float,
    t_mask_b: float,
    suffix_a: str,
    suffix_b: str,
) -> None:
    """Print the planned operations a real run would perform."""
    print(
        f"Would process {len(paths)} datasets × {len(channels)} "
        f"channels with "
        f"t_fit={t_fit}, t_mask_a={t_mask_a}, t_mask_b={t_mask_b}, "
        f"suffix_a={suffix_a!r}, suffix_b={suffix_b!r}"
    )
    print(f"Channels: {', '.join(channels)}")
    for p in paths:
        print(f"  {p.name}: would write {len(channels) * 2} masks")


# ── Main entry point ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="percell4-batch-phasor-masks",
        description=(
            "Batch-fit a phasor ellipse + write two dual-threshold "
            "phasor masks per channel across one or more .h5 datasets.\n\n"
            "For each requested channel of each dataset, fits a GMM "
            "ellipse on the phasor cloud above --t-fit, then writes "
            "two intensity-thresholded ellipse-membership masks "
            "(--t-mask-a → suffix-a, --t-mask-b → suffix-b). When a "
            "dataset lacks pre-computed phasor maps, they are computed "
            "on the fly using the same primitives percell4-batch-phasor "
            "uses.\n\n"
            "Up-front validation: every requested channel must be "
            "present in every dataset; suffixes must be non-empty and "
            "must differ; no mask name may collide with an existing "
            "channel name in any dataset. Validation failures exit 2 "
            "without performing any I/O.\n\n"
            "Recommendation: close any open PerCell4 GUI session "
            "against the target files before running. Use --dry-run "
            "to audit the planned writes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  percell4-batch-phasor-masks dish_1.h5 dish_2.h5 \\\n"
            "      --channels mNG mScarlet\n"
            "  percell4-batch-phasor-masks /scratch/dishes/ \\\n"
            "      --channels mNG --t-fit 20.0 --dry-run\n"
            "  percell4-batch-phasor-masks *.h5 --channels DAPI \\\n"
            "      --t-mask-a 1.0 --t-mask-b 10.0 --quiet\n"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=(
            "One or more .h5 files, or directories containing .h5 "
            "files. Directories are globbed non-recursively (*.h5)."
        ),
    )
    parser.add_argument(
        "--channels",
        nargs="+",
        required=True,
        help=(
            "One or more channel names to fit. Every channel must be "
            "present (in both metadata.channel_names and /decay/) in "
            "every input dataset."
        ),
    )
    parser.add_argument(
        "--t-fit",
        type=float,
        default=10.0,
        help=(
            "Intensity threshold defining the GMM fit subset "
            "(default: 10.0)."
        ),
    )
    parser.add_argument(
        "--t-mask-a",
        type=float,
        default=0.0,
        help=(
            "Intensity threshold applied to mask-a after the "
            "ellipse-membership step (default: 0.0)."
        ),
    )
    parser.add_argument(
        "--t-mask-b",
        type=float,
        default=5.0,
        help=(
            "Intensity threshold applied to mask-b after the "
            "ellipse-membership step (default: 5.0)."
        ),
    )
    parser.add_argument(
        "--suffix-a",
        default="_phasor_1",
        help=(
            "Suffix appended to each channel name for mask-a "
            "(default: '_phasor_1')."
        ),
    )
    parser.add_argument(
        "--suffix-b",
        default="_phasor_5",
        help=(
            "Suffix appended to each channel name for mask-b "
            "(default: '_phasor_5')."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the planned operations and exit 0 without "
            "performing any phasor / mask I/O."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-channel skip / error detail lines. The "
            "per-dataset summary line and final totals always print."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    paths = resolve_paths(args.paths)
    if not paths:
        print(
            "error: no .h5 files matched the given paths", file=sys.stderr,
        )
        return 1

    # Up-front validation runs even in --dry-run so users find problems
    # before the dry-run plan is printed.
    error = _validate(
        paths=paths,
        channels=list(args.channels),
        suffix_a=args.suffix_a,
        suffix_b=args.suffix_b,
    )
    if error is not None:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_dry_run_plan(
            paths=paths,
            channels=list(args.channels),
            t_fit=args.t_fit,
            t_mask_a=args.t_mask_a,
            t_mask_b=args.t_mask_b,
            suffix_a=args.suffix_a,
            suffix_b=args.suffix_b,
        )
        return 0

    def cb(item: BatchPhasorItemResult) -> None:
        print_item_status(item, quiet=args.quiet, verb="processed")

    report = batch_fit_phasor_masks(
        paths,
        channels=list(args.channels),
        t_fit=args.t_fit,
        t_mask_a=args.t_mask_a,
        t_mask_b=args.t_mask_b,
        suffix_a=args.suffix_a,
        suffix_b=args.suffix_b,
        ensure_phasor=True,
        progress_callback=cb,
    )

    print(
        f"\nTotals: {report.total_succeeded} succeeded, "
        f"{report.total_partial} partial, "
        f"{report.total_failed} failed, "
        f"{report.total_skipped} skipped"
    )

    any_progress = any(item.processed for item in report.items)
    return 0 if any_progress else 1


if __name__ == "__main__":
    sys.exit(main())
