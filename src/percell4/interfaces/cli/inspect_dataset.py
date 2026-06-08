"""CLI: print metadata and layer inventory for one or more .h5 datasets.

A read-only triage tool. For each dataset it prints the file size, the
``/metadata`` block (channels, resolution, pixel size, timepoints), and
every layer group (intensity, segmentations, masks, groups, tracks) with
each array's name, shape, and dtype.

Shapes and dtypes are read from HDF5 metadata only — never by decoding
the array — so inspecting a multi-gigabyte stack is fast (see
``docs/solutions/logic-errors/large-file-load-metadata-read-full-decode-2026-06-07.md``).

Usage:
    percell4-inspect dish_1.h5 dish_2.h5
    percell4-inspect /scratch/dishes/            # every *.h5 in the dir
    percell4-inspect dish_1.h5 --json            # machine-readable output

Exit codes:
    0 -- at least one dataset was inspected successfully
    1 -- every input failed to open / inspect

Programmatic use:
    from percell4.interfaces.cli.inspect_dataset import main
    exit_code = main(["dish_1.h5", "--json"])
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from percell4.interfaces.cli._batch_report import resolve_paths

# Root HDF5 groups that hold named per-layer arrays, in display order.
# "intensity" is a single root dataset, handled separately.
_LAYER_GROUPS = ("labels", "masks", "groups", "tracks")


def _human_size(n_bytes: int) -> str:
    """Human-readable file size (B / KB / MB / GB), base-1024."""
    size = float(n_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_pixel_size(value: Any) -> str:
    """Format pixel_size_um as a scalar or (y, x) pair, '—' when unknown."""
    if value is None:
        return "—"
    try:
        seq = list(value)  # anisotropic (y, x)
        return "(" + ", ".join(f"{float(v):.4g}" for v in seq) + ") µm/px"
    except TypeError:
        return f"{float(value):.4g} µm/px"


def _fmt_resolution(native_shape: Any) -> str:
    if not native_shape:
        return "—"
    return "×".join(str(int(x)) for x in native_shape) + " px"


def _layer_rows(store, kind: str, names: list[str]) -> list[dict[str, Any]]:
    """Build (name, shape, dtype) rows for a layer group, no array decode."""
    rows: list[dict[str, Any]] = []
    for name in names:
        path = f"{kind}/{name}"
        try:
            shape = store.array_shape(path)
            dtype = str(store.array_dtype(path))
        except KeyError:
            # A nested group rather than a dataset (shouldn't normally
            # happen for these layers); record it without shape/dtype.
            shape, dtype = None, "—"
        rows.append({"name": name, "shape": tuple(shape) if shape is not None else None, "dtype": dtype})
    return rows


def _inspect(path: Path) -> dict[str, Any]:
    """Gather the full inventory for one dataset. Raises on open failure."""
    from percell4.store import DatasetStore

    store = DatasetStore(path)
    meta = store.metadata
    mask_names = store.list_masks()
    mask_set = set(mask_names)
    # A name under /labels that is also a mask is reported as a mask
    # (masks can shadow label names); segmentations are the difference.
    seg_names = [n for n in store.list_labels() if n not in mask_set]

    intensity: dict[str, Any] | None = None
    if store.array_exists("intensity"):
        intensity = {
            "shape": tuple(store.array_shape("intensity")),
            "dtype": str(store.array_dtype("intensity")),
        }

    return {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "metadata": {
            "channel_names": meta.get("channel_names"),
            "native_shape": (
                list(meta["native_shape"]) if meta.get("native_shape") else None
            ),
            "pixel_size_um": meta.get("pixel_size_um"),
            "n_timepoints": meta.get("n_timepoints"),
            "creation_bin": meta.get("creation_bin"),
            "source": meta.get("source"),
        },
        "intensity": intensity,
        "segmentations": _layer_rows(store, "labels", seg_names),
        "masks": _layer_rows(store, "masks", mask_names),
        "groups": _layer_rows(store, "groups", store.list_groups("groups")),
        "tracks": _layer_rows(store, "tracks", store.list_groups("tracks")),
    }


def _print_layer_block(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"  {title}:")
    if not rows:
        print("    —")
        return
    width = max(len(r["name"]) for r in rows)
    for r in rows:
        shape = "" if r["shape"] is None else str(r["shape"])
        print(f"    {r['name']:<{width}}  {shape:<18} {r['dtype']}")


def _print_human(info: dict[str, Any]) -> None:
    m = info["metadata"]
    print("=" * 70)
    print(f"File:        {info['file']}")
    print(f"Size:        {_human_size(info['size_bytes'])}")
    print(f"Resolution:  {_fmt_resolution(m['native_shape'])}")
    print(f"Pixel size:  {_fmt_pixel_size(m['pixel_size_um'])}")
    print(f"Timepoints:  {m['n_timepoints'] if m['n_timepoints'] is not None else '—'}")
    channels = m["channel_names"]
    print(f"Channels:    {', '.join(channels) if channels else '—'}")
    print(f"Created bin: {m['creation_bin'] if m['creation_bin'] is not None else '—'}")
    if m.get("source"):
        print(f"Source:      {m['source']}")
    print("  Layers")
    if info["intensity"] is not None:
        i = info["intensity"]
        print(f"  Intensity:\n    intensity  {str(i['shape']):<18} {i['dtype']}")
    else:
        print("  Intensity:\n    —")
    _print_layer_block("Segmentations", info["segmentations"])
    _print_layer_block("Masks", info["masks"])
    _print_layer_block("Groups", info["groups"])
    _print_layer_block("Tracks", info["tracks"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="percell4-inspect",
        description=(
            "Print metadata and the layer inventory (intensity, segmentations, "
            "masks, groups, tracks) for one or more PerCell4 .h5 datasets. "
            "Read-only; shapes/dtypes are read without decoding arrays."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "datasets",
        nargs="+",
        help="One or more .h5 files, or directories (every *.h5 within, non-recursive).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON array of per-dataset records instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    paths = resolve_paths(args.datasets)
    if not paths:
        print("No .h5 datasets found in the given paths.", file=sys.stderr)
        return 1

    records: list[dict[str, Any]] = []
    n_ok = 0
    for path in paths:
        try:
            info = _inspect(path)
        except Exception as e:  # corrupt / non-HDF5 / unreadable
            print(f"[error] {path}: {e}", file=sys.stderr)
            continue
        n_ok += 1
        if args.json:
            records.append(info)
        else:
            _print_human(info)

    if args.json:
        print(json.dumps(records, indent=2, default=str))

    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
