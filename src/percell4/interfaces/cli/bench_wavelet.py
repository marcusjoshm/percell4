"""Benchmark the two FLIM wavelet filter algorithms on a known phantom.

Runs both ``boe_2021`` and ``jcb_2025`` on a synthetic Siemens-star
TCSPC phantom, uses a 500-frame accumulation as the high-SNR reference,
and emits quantitative + visual artefacts comparing the two algorithms:

- ``phasor_plots.png`` — 2D phasor histograms for ground truth,
  unfiltered, BOE, JCB
- ``g_maps.png`` — G-coordinate maps in the same order
- ``mse_per_frequency.png`` — MSE vs. radial spatial-frequency,
  reproducing the shape of BOE Fig. S3
- ``metrics.json`` — whole-image MSE, per-stage timings, dtcwt version,
  and percell4 git SHA (if available) for reproducibility

Usage::

    python -m percell4.interfaces.cli.bench_wavelet synthetic \\
        --out /tmp/wavelet_bench/ --seed 0

Not meant for production use on real FLIM data — real-data mode is
Phase 4 and lives elsewhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import percell4._compat  # noqa: F401 — NumPy 2.0 shims for dtcwt
import numpy as np

from percell4.domain.flim.phasor import compute_phasor
from percell4.domain.flim.synthetic import generate_spoke_tcspc
from percell4.domain.flim.wavelet import denoise_phasor

logger = logging.getLogger(__name__)


# ── Timing helper ─────────────────────────────────────────────────────

@contextmanager
def _timed(label: str, into: dict[str, float]):
    """Record elapsed wall time for a block into ``into[label]``."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        into[label] = time.perf_counter() - t0


# ── MSE metrics ───────────────────────────────────────────────────────

def _intensity_weighted_mse(
    g_filt: np.ndarray, s_filt: np.ndarray,
    g_ref: np.ndarray, s_ref: np.ndarray,
    intensity: np.ndarray,
) -> float:
    """Intensity-weighted MSE of (G, S) against a reference pair.

    Matches the paper's convention of thresholding out zero-intensity
    pixels before averaging.
    """
    mask = np.isfinite(g_filt) & np.isfinite(s_filt)
    mask &= np.isfinite(g_ref) & np.isfinite(s_ref)
    mask &= intensity > 0
    if not mask.any():
        return float("inf")
    dg = (g_filt[mask] - g_ref[mask]) ** 2
    ds = (s_filt[mask] - s_ref[mask]) ** 2
    return float(np.mean(dg + ds))


def _mse_per_radial_frequency(
    g_filt: np.ndarray, g_ref: np.ndarray,
    n_bins: int = 40,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute MSE of a G-map against a reference, binned by the
    radial spatial frequency of the error signal.

    Returns ``(freq_cycles_per_pixel, mse)`` with ``n_bins`` bins
    covering 0..0.5 cycles/pixel (Nyquist). Matches the shape of BOE
    Fig. S3.
    """
    g_filt = np.nan_to_num(g_filt)
    g_ref = np.nan_to_num(g_ref)
    err = g_filt - g_ref
    spectrum = np.fft.fft2(err)
    power = np.abs(spectrum) ** 2
    h, w = err.shape
    fy = np.fft.fftfreq(h)[:, None]  # cycles/pixel
    fx = np.fft.fftfreq(w)[None, :]
    freq_r = np.sqrt(fx ** 2 + fy ** 2)

    bin_edges = np.linspace(0.0, 0.5, n_bins + 1)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    mse = np.zeros(n_bins)
    for i in range(n_bins):
        ring = (freq_r >= bin_edges[i]) & (freq_r < bin_edges[i + 1])
        if ring.any():
            mse[i] = float(power[ring].mean() / (h * w))
    return bin_centers, mse


# ── Plotting ──────────────────────────────────────────────────────────

def _render_plots(
    out_dir: Path,
    *,
    g_ref: np.ndarray, s_ref: np.ndarray,
    g_noisy: np.ndarray, s_noisy: np.ndarray,
    results: dict[str, dict[str, np.ndarray]],
    freq: np.ndarray,
    mse_curves: dict[str, np.ndarray],
) -> None:
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    # Phasor hexbin plots in a 1×4 row: ground truth | noisy | JCB | BOE.
    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    pairs = [
        ("Ground truth", g_ref, s_ref),
        ("Unfiltered", g_noisy, s_noisy),
        ("JCB 2025", results["jcb_2025"]["g"], results["jcb_2025"]["s"]),
        ("BOE 2021", results["boe_2021"]["g"], results["boe_2021"]["s"]),
    ]
    theta = np.linspace(0, np.pi, 200)
    circle_g = 0.5 + 0.5 * np.cos(theta)
    circle_s = 0.5 * np.sin(theta)
    for ax, (title, g, s) in zip(axes, pairs):
        g_f = np.nan_to_num(np.asarray(g).ravel())
        s_f = np.nan_to_num(np.asarray(s).ravel())
        ax.hexbin(g_f, s_f, gridsize=80, bins="log", cmap="magma",
                   extent=(-0.05, 1.05, -0.05, 0.6))
        ax.plot(circle_g, circle_s, "w--", lw=0.5)
        ax.set_title(title)
        ax.set_xlim(-0.05, 1.05)
        ax.set_ylim(-0.05, 0.6)
        ax.set_xlabel("G")
        ax.set_ylabel("S")
    fig.tight_layout()
    fig.savefig(out_dir / "phasor_plots.png", dpi=120)
    plt.close(fig)

    # G-maps.
    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    vmin = float(np.nanmin(g_ref))
    vmax = float(np.nanmax(g_ref))
    for ax, (title, g, _) in zip(axes, pairs):
        im = ax.imshow(np.asarray(g), cmap="viridis", vmin=vmin, vmax=vmax)
        ax.set_title(title)
        ax.axis("off")
    fig.colorbar(im, ax=axes, shrink=0.8, label="G")
    fig.savefig(out_dir / "g_maps.png", dpi=120)
    plt.close(fig)

    # MSE vs spatial frequency.
    fig, ax = plt.subplots(figsize=(8, 4))
    for label, mse in mse_curves.items():
        ax.semilogy(freq, mse, label=label, lw=1.6)
    ax.set_xlabel("Spatial frequency (cycles/pixel)")
    ax.set_ylabel("MSE of G-map vs. ground truth (log)")
    ax.set_title("BOE vs JCB vs Unfiltered — radial-frequency MSE")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "mse_per_frequency.png", dpi=120)
    plt.close(fig)


# ── Versions ──────────────────────────────────────────────────────────

def _collect_versions() -> dict[str, str]:
    import dtcwt
    import scipy

    versions: dict[str, str] = {
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "dtcwt": getattr(dtcwt, "__version__", "unknown"),
    }
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        versions["percell4_git_sha"] = sha
    except (subprocess.SubprocessError, FileNotFoundError):
        versions["percell4_git_sha"] = "unavailable"
    return versions


# ── Synthetic-mode driver ─────────────────────────────────────────────

def run_synthetic(
    out_dir: Path,
    *,
    seed: int = 0,
    shape: tuple[int, int] = (512, 512),
    n_bins: int = 256,
    n_reference_frames: int = 500,
    filter_level: int = 9,
    skip_plots: bool = False,
) -> dict[str, Any]:
    """Run the BOE-vs-JCB comparison on a synthetic Siemens-star phantom.

    Writes artefacts into ``out_dir`` and returns the metrics dict.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}

    logger.info("Generating spoke phantom (shape=%s, n_bins=%d, n_ref_frames=%d)",
                 shape, n_bins, n_reference_frames)
    with _timed("phantom_generation", timings):
        phantom = generate_spoke_tcspc(
            seed=seed, shape=shape, n_bins=n_bins,
            n_reference_frames=n_reference_frames,
        )

    logger.info("Computing phasor for 1-frame test and %d-frame reference",
                 n_reference_frames)
    with _timed("phasor_computation", timings):
        g_noisy, s_noisy = compute_phasor(phantom.tcspc_single)
        g_ref, s_ref = compute_phasor(phantom.tcspc_reference)
    intensity_noisy = phantom.tcspc_single.sum(axis=-1).astype(np.float64)

    # Both filters get identical inputs.
    results: dict[str, dict[str, np.ndarray]] = {}
    for algo in ("boe_2021", "jcb_2025"):
        with _timed(f"filter_{algo}", timings):
            logger.info("Running %s filter (flevel=%d)", algo, filter_level)
            out = denoise_phasor(
                g_noisy.astype(np.float64),
                s_noisy.astype(np.float64),
                intensity_noisy,
                algorithm=algo,
                filter_level=filter_level,
            )
        results[algo] = {"g": out["G"], "s": out["S"]}

    # Intensity-weighted whole-image MSE against the 500-frame reference.
    whole_mse = {
        "unfiltered": _intensity_weighted_mse(
            g_noisy, s_noisy, g_ref, s_ref, intensity_noisy),
        "boe_2021": _intensity_weighted_mse(
            results["boe_2021"]["g"], results["boe_2021"]["s"],
            g_ref, s_ref, intensity_noisy),
        "jcb_2025": _intensity_weighted_mse(
            results["jcb_2025"]["g"], results["jcb_2025"]["s"],
            g_ref, s_ref, intensity_noisy),
    }

    # Per-radial-frequency MSE (matches BOE Fig. S3 shape).
    freq_bins, mse_unfilt = _mse_per_radial_frequency(g_noisy, g_ref)
    _, mse_boe = _mse_per_radial_frequency(results["boe_2021"]["g"], g_ref)
    _, mse_jcb = _mse_per_radial_frequency(results["jcb_2025"]["g"], g_ref)
    mse_curves = {
        "Unfiltered": mse_unfilt,
        "BOE 2021": mse_boe,
        "JCB 2025": mse_jcb,
    }

    # Acceptance-criterion checks (sourced from the plan).
    high_freq_slice = freq_bins >= 0.25
    boe_high_f_mean = float(mse_boe[high_freq_slice].mean())
    jcb_high_f_mean = float(mse_jcb[high_freq_slice].mean())

    metrics: dict[str, Any] = {
        "seed": seed,
        "shape": list(shape),
        "n_bins": n_bins,
        "n_reference_frames": n_reference_frames,
        "filter_level": filter_level,
        "freq_mhz": phantom.freq_mhz,
        "timings_sec": timings,
        "whole_image_gs_mse": whole_mse,
        "high_frequency_gmap_mse_mean_ge_0p25_cyc_per_px": {
            "boe_2021": boe_high_f_mean,
            "jcb_2025": jcb_high_f_mean,
            "boe_over_jcb_ratio": (
                boe_high_f_mean / jcb_high_f_mean
                if jcb_high_f_mean > 0 else float("inf")
            ),
        },
        "versions": _collect_versions(),
    }

    # Write artefacts.
    with _timed("rendering", timings):
        metrics_path = out_dir / "metrics.json"
        with metrics_path.open("w") as f:
            json.dump(metrics, f, indent=2)
        if not skip_plots:
            _render_plots(
                out_dir,
                g_ref=g_ref, s_ref=s_ref,
                g_noisy=g_noisy, s_noisy=s_noisy,
                results=results,
                freq=freq_bins,
                mse_curves=mse_curves,
            )

    # Persist the freq/MSE curves alongside the plot for scripting use.
    np.savez(
        out_dir / "mse_curves.npz",
        freq_cycles_per_pixel=freq_bins,
        mse_unfiltered=mse_unfilt,
        mse_boe_2021=mse_boe,
        mse_jcb_2025=mse_jcb,
    )
    return metrics


# ── argparse ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark FLIM wavelet filters against a synthetic "
                    "spoke phantom.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("synthetic",
                        help="Benchmark on the Siemens-star phantom.")
    s.add_argument("--out", type=Path, required=True,
                    help="Output directory; created if needed.")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--shape", type=int, nargs=2, default=[512, 512],
                    metavar=("H", "W"))
    s.add_argument("--n-bins", type=int, default=256)
    s.add_argument("--n-reference-frames", type=int, default=500,
                    help="Frames accumulated into the high-SNR reference.")
    s.add_argument("--filter-level", type=int, default=9,
                    help="DTCWT decomposition depth.")
    s.add_argument("--skip-plots", action="store_true",
                    help="Skip matplotlib rendering (metrics.json only).")

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.mode == "synthetic":
        metrics = run_synthetic(
            args.out,
            seed=args.seed,
            shape=tuple(args.shape),
            n_bins=args.n_bins,
            n_reference_frames=args.n_reference_frames,
            filter_level=args.filter_level,
            skip_plots=args.skip_plots,
        )
        mse = metrics["whole_image_gs_mse"]
        print(f"\nWrote {args.out}/\n")
        print(f"Whole-image G/S MSE vs. {args.n_reference_frames}-frame ref:")
        for k, v in mse.items():
            print(f"  {k:12s} {v:.6f}")
        hf = metrics["high_frequency_gmap_mse_mean_ge_0p25_cyc_per_px"]
        print(f"\nHigh-freq (≥0.25 c/px) G-MSE:")
        print(f"  BOE {hf['boe_2021']:.6e}  JCB {hf['jcb_2025']:.6e}  "
              f"ratio {hf['boe_over_jcb_ratio']:.2f}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
