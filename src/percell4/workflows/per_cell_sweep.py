"""Per-cell ``(window, k)`` contact-sheet sweep for Adaptive Local Clipping.

The whole-image :mod:`percell4.workflows.window_k_sweep` showed that no single
``(window, k)`` is right for *every* cell — low-contrast/hazy cells need a higher
``k`` and larger window than crisp, dark-background cells. This module turns that
observation into per-cell labelled data: for **each Cellpose cell instance** it
runs the same cell-restricted sweep on a crop of that cell and renders a
**contact sheet** (a montage of the grid, mask overlaid on the cell), so the user
can eyeball one cell at a time and record the best ``(window, k)`` per cell.

It is **read-only** — it writes PNG contact sheets + CSV index/label-template
files to an output directory and never mutates the ``.h5``. The numeric core
(:func:`sweep_one_cell`) is pure (numpy/scipy/skimage) and matplotlib-free;
rendering is isolated in :func:`render_contact_sheet`.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from percell4.domain.measure.puncta_pipeline import (
    DEFAULT_SCALE_RANGE,
    compute_seeds,
    detect_two_pass,
)
from percell4.domain.measure.thresholding import apply_gaussian_smoothing
from percell4.workflows.window_k_sweep import (
    DEFAULT_KS,
    DEFAULT_WINDOWS,
    FixedSettings,
    base_settings,
    settings_with,
)

if TYPE_CHECKING:
    from percell4.store import DatasetStore

__all__ = [
    "DEFAULT_KS",
    "DEFAULT_WINDOWS",
    "CellSweep",
    "CellIndexRow",
    "PerCellReport",
    "normalize_grid",
    "select_cell_ids",
    "sweep_one_cell",
    "compute_display_range",
    "render_contact_sheet",
    "write_index_and_template",
    "run_per_cell_sweep",
]


@dataclass
class CellSweep:
    """One cell's crop, its mask group, and the full ``(window, k)`` mask grid."""

    cell_id: int
    bbox: tuple[int, int, int, int]  # (rmin, cmin, rmax, cmax) in full-frame coords
    centroid: tuple[float, float]  # (y, x) in full-frame coords
    area_px: int
    crop: np.ndarray  # grayscale cell-crop (float32)
    cell_mask: np.ndarray  # bool group within the crop (labels == cell_id)
    masks: dict[tuple[int, float], np.ndarray]  # (window, k) -> {0,1} uint8 crop mask
    stats: dict[tuple[int, float], tuple[int, int]]  # (window, k) -> (count, positive_px)


@dataclass
class CellIndexRow:
    """A row of the cells.csv index (locate each cell + its sheet)."""

    cell_id: int
    centroid_y: float
    centroid_x: float
    bbox: tuple[int, int, int, int]
    area_px: int
    sheet: str


@dataclass
class PerCellReport:
    """Summary of a per-cell sweep run."""

    dataset: str
    out_dir: str
    windows: tuple[int, ...]
    ks: tuple[float, ...]
    rows: list[CellIndexRow] = field(default_factory=list)
    display_range: tuple[float, float] | None = None
    failure: str | None = None


def normalize_grid(
    windows: Sequence[int], ks: Sequence[float]
) -> tuple[tuple[int, ...], tuple[float, ...]]:
    """Force windows odd (the detector does) and floats for ``k``; de-duplicate.

    De-duplication keeps the montage and the mask dict keys consistent when two
    requested windows collapse to the same odd value (e.g. ``30`` and ``31``).
    """
    seen_w: list[int] = []
    for w in windows:
        ow = int(w) | 1
        if ow not in seen_w:
            seen_w.append(ow)
    seen_k: list[float] = []
    for k in ks:
        fk = float(k)
        if fk not in seen_k:
            seen_k.append(fk)
    return tuple(seen_w), tuple(seen_k)


def select_cell_ids(
    labels: np.ndarray, *, min_cell_px: int = 1, max_cells: int | None = None
) -> list[int]:
    """Cell ids to sweep: ``labels`` instances with area >= ``min_cell_px``.

    When ``max_cells`` is set, the largest cells are kept (by area), but the
    returned list is ordered by ascending ``cell_id`` for stable, readable output.
    """
    lab = np.asarray(labels)
    ids = [int(v) for v in np.unique(lab) if int(v) > 0]
    sized = [(cid, int((lab == cid).sum())) for cid in ids]
    sized = [(cid, a) for cid, a in sized if a >= int(min_cell_px)]
    if max_cells is not None and len(sized) > int(max_cells):
        sized.sort(key=lambda ca: ca[1], reverse=True)
        sized = sized[: int(max_cells)]
    return sorted(cid for cid, _a in sized)


def sweep_one_cell(
    image: np.ndarray,
    labels: np.ndarray,
    cell_id: int,
    windows: Sequence[int],
    ks: Sequence[float],
    fixed: FixedSettings,
    *,
    padding: int = 8,
) -> CellSweep:
    """Run the cell-restricted ``(window, k)`` grid on a crop of one cell.

    Crops ``image`` to the cell's bounding box (padded by ``padding`` px), builds
    the cell ``group = labels == cell_id`` within the crop, computes smoothing +
    pass-1 seeds **once**, then detects at every grid point reusing the seeds.
    Pure — no store, no matplotlib. ``windows`` / ``ks`` should already be
    normalized via :func:`normalize_grid`.
    """
    lab = np.asarray(labels)
    img = np.asarray(image, dtype=np.float32)
    ys, xs = np.where(lab == cell_id)
    if ys.size == 0:
        raise ValueError(f"cell_id {cell_id} not present in labels")
    h, w_full = lab.shape
    rmin = max(0, int(ys.min()) - padding)
    cmin = max(0, int(xs.min()) - padding)
    rmax = min(h, int(ys.max()) + 1 + padding)
    cmax = min(w_full, int(xs.max()) + 1 + padding)

    crop = img[rmin:rmax, cmin:cmax]
    cell_mask = lab[rmin:rmax, cmin:cmax] == cell_id
    area_px = int(cell_mask.sum())
    centroid = (float(ys.mean()), float(xs.mean()))

    smoothed = apply_gaussian_smoothing(crop, fixed.gaussian_sigma)
    base = base_settings(fixed)
    scale_range = base.spot_scale_prior or DEFAULT_SCALE_RANGE
    seeds = compute_seeds(smoothed, cell_mask, base, scale_range)

    from skimage import measure

    masks: dict[tuple[int, float], np.ndarray] = {}
    stats: dict[tuple[int, float], tuple[int, int]] = {}
    for win in windows:
        for k in ks:
            mask = np.asarray(
                detect_two_pass(smoothed, cell_mask, settings_with(fixed, win, k), seeds=seeds)
            ).astype(np.uint8)
            masks[(int(win), float(k))] = mask
            pos = int(mask.sum())
            count = int(measure.label(mask > 0).max())
            stats[(int(win), float(k))] = (count, pos)

    return CellSweep(
        cell_id=int(cell_id),
        bbox=(rmin, cmin, rmax, cmax),
        centroid=centroid,
        area_px=area_px,
        crop=crop,
        cell_mask=cell_mask,
        masks=masks,
        stats=stats,
    )


def compute_display_range(
    image: np.ndarray,
    labels: np.ndarray,
    *,
    low_pct: float = 1.0,
    high_pct: float = 99.5,
    vmin: float | None = None,
    vmax: float | None = None,
) -> tuple[float, float]:
    """A single grayscale display range for the **whole image**, used by every crop.

    The range is taken from all in-cell pixels (``labels > 0``) across the entire
    image — not per crop — so every cell and every tile shares one consistent
    intensity mapping, matching a fixed napari contrast. Bright granules above
    ``high_pct`` clip to white, leaving the dilute/condensed-phase mid-tones the
    full range. ``vmin`` / ``vmax`` override the percentiles when given.
    """
    img = np.asarray(image, dtype=np.float32)
    inside = img[np.asarray(labels) > 0]
    if inside.size == 0:
        inside = img.ravel()
    lo = float(vmin) if vmin is not None else float(np.percentile(inside, low_pct))
    hi = float(vmax) if vmax is not None else float(np.percentile(inside, high_pct))
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def render_contact_sheet(
    cell: CellSweep,
    windows: Sequence[int],
    ks: Sequence[float],
    out_path: Path,
    *,
    vmin: float,
    vmax: float,
) -> None:
    """Render a per-cell montage PNG: each tile is **channel-only | channel+mask**.

    Rows = windows; each ``k`` becomes a side-by-side pair of panels — the raw
    grayscale crop on the left (no overlay, for reference) and the same crop with
    that tile's mask painted **opaque** yellow on the right. Both panels use the
    shared ``(vmin, vmax)`` display range so the grayscale is identical and
    comparable across every cell. Uses the Agg backend (headless).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    crop = np.asarray(cell.crop, dtype=np.float32)
    nrows, ncols = len(windows), len(ks) * 2
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(2.1 * ncols, 2.3 * nrows), squeeze=False
    )
    for r, win in enumerate(windows):
        for c, k in enumerate(ks):
            ax_raw = axes[r][2 * c]
            ax_mask = axes[r][2 * c + 1]
            for ax in (ax_raw, ax_mask):
                ax.imshow(crop, cmap="gray", vmin=vmin, vmax=vmax, interpolation="nearest")
                ax.set_xticks([])
                ax.set_yticks([])
            mask = cell.masks.get((int(win), float(k)))
            if mask is not None and mask.any():
                overlay = np.zeros((*mask.shape, 4), dtype=np.float32)
                overlay[mask > 0] = (1.0, 1.0, 0.0, 1.0)  # opaque yellow
                ax_mask.imshow(overlay, interpolation="nearest")
            count, pos = cell.stats.get((int(win), float(k)), (0, 0))
            ax_raw.set_title(f"w{win} k{k:.1f} — raw", fontsize=7)
            ax_mask.set_title(f"w{win} k{k:.1f} — mask\n{count} obj / {pos}px", fontsize=7)
    fig.suptitle(
        f"cell {cell.cell_id}  (area {cell.area_px:,} px, centroid "
        f"y={cell.centroid[0]:.0f} x={cell.centroid[1]:.0f})  "
        f"[display {vmin:.0f}–{vmax:.0f}]",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def write_index_and_template(rows: Sequence[CellIndexRow], out_dir: Path) -> tuple[Path, Path]:
    """Write ``cells.csv`` (index) and a blank ``labels_template.csv``.

    ``cells.csv`` locates every cell + its sheet. The blank pick form is written
    to ``labels_template.csv`` (always refreshed), and copied to ``labels.csv``
    **only when ``labels.csv`` does not already exist** — so re-running the sweep
    into a directory that already holds a hand-filled ``labels.csv`` never
    overwrites the user's work. Returns ``(cells_path, labels_path)`` where
    ``labels_path`` is the existing ``labels.csv`` if present, else the freshly
    created one.
    """
    cells_path = out_dir / "cells.csv"
    with cells_path.open("w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f)
        wtr.writerow(["cell_id", "centroid_y", "centroid_x", "bbox", "area_px", "sheet"])
        for r in rows:
            wtr.writerow(
                [
                    r.cell_id,
                    f"{r.centroid_y:.1f}",
                    f"{r.centroid_x:.1f}",
                    "|".join(str(v) for v in r.bbox),
                    r.area_px,
                    r.sheet,
                ]
            )

    header = ["cell_id", "best_window", "best_k", "none_acceptable", "notes"]
    template_path = out_dir / "labels_template.csv"
    with template_path.open("w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f)
        wtr.writerow(header)
        for r in rows:
            wtr.writerow([r.cell_id, "", "", "", ""])

    labels_path = out_dir / "labels.csv"
    if not labels_path.exists():  # never clobber a hand-filled labels.csv
        with labels_path.open("w", newline="", encoding="utf-8") as f:
            wtr = csv.writer(f)
            wtr.writerow(header)
            for r in rows:
                wtr.writerow([r.cell_id, "", "", "", ""])
    return cells_path, labels_path


def run_per_cell_sweep(
    store: DatasetStore,
    channel: str,
    segmentation: str,
    windows: Sequence[int],
    ks: Sequence[float],
    fixed: FixedSettings,
    out_dir: Path,
    *,
    padding: int = 8,
    min_cell_px: int = 50,
    max_cells: int | None = None,
    display_low_pct: float = 1.0,
    display_high_pct: float = 99.5,
    display_min: float | None = None,
    display_max: float | None = None,
) -> PerCellReport:
    """Sweep every cell of one dataset and render a contact sheet per cell.

    Loads ``channel`` + the ``segmentation`` instance labels, computes **one**
    grayscale display range over all in-cell pixels of the whole image (so every
    cell/tile shares the same intensity mapping), sweeps each cell via
    :func:`sweep_one_cell`, renders its contact sheet into ``out_dir``, and writes
    ``cells.csv`` + ``labels.csv``. Read-only with respect to the ``.h5``. A load
    error is captured into ``PerCellReport.failure`` (no raise).
    """
    dataset = Path(str(store.path)).stem
    nwindows, nks = normalize_grid(windows, ks)
    report = PerCellReport(dataset=dataset, out_dir=str(out_dir), windows=nwindows, ks=nks)
    try:
        from percell4.workflows.phases import _channel_index

        idx = _channel_index(store, channel)
        image = np.asarray(store.read_channel("intensity", idx), dtype=np.float32)
        labels = np.asarray(store.read_labels(segmentation))
        cell_ids = select_cell_ids(labels, min_cell_px=min_cell_px, max_cells=max_cells)

        vmin, vmax = compute_display_range(
            image,
            labels,
            low_pct=display_low_pct,
            high_pct=display_high_pct,
            vmin=display_min,
            vmax=display_max,
        )
        report.display_range = (vmin, vmax)

        out_dir.mkdir(parents=True, exist_ok=True)
        for cid in cell_ids:
            cell = sweep_one_cell(image, labels, cid, nwindows, nks, fixed, padding=padding)
            sheet = f"cell{cid:03d}_contactsheet.png"
            render_contact_sheet(cell, nwindows, nks, out_dir / sheet, vmin=vmin, vmax=vmax)
            report.rows.append(
                CellIndexRow(
                    cell_id=cid,
                    centroid_y=cell.centroid[0],
                    centroid_x=cell.centroid[1],
                    bbox=cell.bbox,
                    area_px=cell.area_px,
                    sheet=sheet,
                )
            )
        write_index_and_template(report.rows, out_dir)
    except Exception as exc:  # noqa: BLE001 — per-dataset isolation, recorded not raised
        report.rows = []
        report.failure = f"{type(exc).__name__}: {exc}"
    return report
