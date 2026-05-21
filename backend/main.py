"""PerCell4 FastAPI sidecar — minimal stub.

Spawned by the Tauri shell as a sidecar process. Speaks HTTP+WebSocket
on 127.0.0.1:${PERCELL4_PORT} (default 8765). The React frontend talks
to this process; it does not import anything from `percell4` core yet
— that wiring is the next branch.

Run standalone for UI dev:

    python -m backend.main

Or build the sidecar binary for Tauri bundling:

    pip install pyinstaller
    pyinstaller --onefile --name percell4-backend backend/main.py
    cp dist/percell4-backend desktop/src-tauri/binaries/percell4-backend-<target-triple>
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# When running from source (`python -m backend.main`), the `percell4`
# package lives at `<repo>/src/percell4/`. Inject it into sys.path so
# the imports below resolve. The PyInstaller bundle gets the same
# resolution via `--paths src` at build time, so this is a no-op there.
_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

PORT = int(os.environ.get("PERCELL4_PORT", "8765"))

app = FastAPI(title="PerCell4 Backend", version="0.1.0")

# Tauri WebView loads the bundle from a `tauri://` or `file://` origin;
# CORS open to localhost is sufficient for the sidecar pattern.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Dataset metadata ────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/load_image")
def load_image(payload: dict[str, Any]) -> dict[str, Any]:
    """Read real dataset metadata from an HDF5 file via percell4 core.

    Delegates to ``percell4.store.DatasetStore`` + the
    ``_build_handle_metadata`` adapter to return the same dict shape the
    PyQt5 app uses. Output is normalized to JSON-safe primitives
    (channel/seg/mask names → str, shape → list[int], frequency →
    float | None).
    """
    path = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    # Imports are local so a missing percell4 package only breaks this
    # endpoint, not the whole sidecar.
    from percell4.adapters.hdf5_store import _build_handle_metadata
    from percell4.store import DatasetStore

    store = DatasetStore(path)
    if not store.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")

    try:
        md = _build_handle_metadata(store)
    except Exception as e:  # noqa: BLE001 — surface as 400 with message
        raise HTTPException(
            status_code=400, detail=f"failed to read {path}: {e}",
        ) from e

    channel_names = [str(n) for n in md.get("channel_names", [])]
    seg_names = [str(n) for n in md.get("segmentation_names", [])]
    mask_names = [str(n) for n in md.get("mask_names", [])]

    # Synthesize (C, H, W) — the React `DatasetMeta.shape` consumer wants
    # a triple. Native shape comes from metadata as (H, W); prepend C.
    native_shape = md.get("native_shape")
    if native_shape is not None:
        h, w = int(native_shape[0]), int(native_shape[1])
        shape: list[int] | None = [len(channel_names) or 1, h, w]
    else:
        shape = None

    freq_raw = md.get("flim_frequency_mhz")
    freq: float | None = float(freq_raw) if freq_raw is not None else None

    return {
        "path": str(Path(path).resolve()),
        "shape": shape,
        "channel_names": channel_names,
        "segmentation_names": seg_names,
        "mask_names": mask_names,
        "flim_frequency_mhz": freq,
        "creation_bin": int(md.get("creation_bin", 1)),
    }


@app.post("/measurements")
def measurements(payload: dict[str, Any]) -> dict[str, Any]:
    """Run per-cell measurement against the active dataset/seg/mask.

    Stateless: each request supplies the path + active selectors so the
    sidecar can be killed and restarted without the frontend losing
    context. Delegates to `percell4.application.use_cases.MeasureCells`,
    which writes the resulting DataFrame back to the .h5 file (matches
    the existing PyQt5 app behavior).

    Request body:
        path:         absolute .h5 path
        segmentation: active segmentation name (required)
        mask:         active mask name (optional)
        view_bin:     downsample factor 1..16 (default 1)
        metrics:      list of metric names; default = all BUILTIN_METRICS

    Returns:
        n_cells, columns, rows (DataFrame.to_dict(orient="records"))
    """
    path = payload.get("path")
    seg_name = payload.get("segmentation")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not seg_name:
        raise HTTPException(status_code=400, detail="segmentation is required")

    from pathlib import Path as _Path

    from percell4.adapters.hdf5_store import (
        Hdf5DatasetRepository,
        _build_handle_metadata,
    )
    from percell4.application.session import Session
    from percell4.application.use_cases.measure_cells import MeasureCells
    from percell4.domain.dataset import DatasetHandle
    from percell4.domain.errors import (
        NoDatasetError,
        NoMaskError,
        NoSegmentationError,
    )
    from percell4.domain.measure.metrics import BUILTIN_METRICS
    from percell4.store import DatasetStore

    store = DatasetStore(path)
    if not store.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")

    try:
        handle = DatasetHandle(
            path=_Path(path), metadata=_build_handle_metadata(store),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"failed to open {path}: {e}",
        ) from e

    session = Session()
    session.set_dataset(handle)
    session.set_active_segmentation(seg_name)
    mask_name = payload.get("mask")
    if mask_name:
        session.set_active_mask(mask_name)
    view_bin = int(payload.get("view_bin", 1))
    session.set_active_bin(view_bin)

    metrics = payload.get("metrics") or list(BUILTIN_METRICS.keys())
    # Reject unknown metric names early — MeasureCells would raise
    # later, but the message is friendlier here.
    unknown = [m for m in metrics if m not in BUILTIN_METRICS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown metric(s): {unknown}; "
            f"valid: {sorted(BUILTIN_METRICS.keys())}",
        )

    try:
        df = MeasureCells(Hdf5DatasetRepository(), session).execute(
            metrics=metrics,
        )
    except (NoDatasetError, NoSegmentationError, NoMaskError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"measurement failed: {e}",
        ) from e

    # JSON-safe DataFrame: rows as plain dicts, numpy ints/floats coerced
    # to native types via `df.to_dict(orient="records")` after astype.
    return {
        "n_cells": int(len(df)),
        "columns": list(df.columns),
        "rows": df.to_dict(orient="records"),
    }


@app.post("/channel_image")
def channel_image(payload: dict[str, Any]) -> Response:
    """Return a single channel as an auto-contrasted grayscale PNG.

    This is the per-channel raster the React viewer renders inside the
    Tauri WebView. napari can't run in the WebView (Qt + OpenGL); this
    endpoint is the equivalent: read the channel from /intensity in
    the .h5, normalize to 8-bit via percentile contrast, encode as PNG.

    Multi-channel composites + label/mask overlays + zoom/pan are
    deferred — this proves the wire shape for image data.

    Request body:
        path:           absolute .h5 path (required)
        channel:        channel name to render (required)
        view_bin:       optional downsample k, default 1
        contrast_low:   percentile (0..100), default 1
        contrast_high:  percentile (0..100), default 99

    Response: image/png bytes (HTTP 200 with binary body, not JSON).
    """
    path = payload.get("path")
    channel = payload.get("channel")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not channel:
        raise HTTPException(status_code=400, detail="channel is required")

    view_bin = int(payload.get("view_bin", 1))
    contrast_low = float(payload.get("contrast_low", 1.0))
    contrast_high = float(payload.get("contrast_high", 99.0))

    import io

    import numpy as np
    from PIL import Image

    from percell4.store import DatasetStore

    store = DatasetStore(path)
    if not store.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")

    # Read just the requested channel slice. Multi-channel files store
    # /intensity as (C, H, W); single-channel as (H, W). Channel index
    # comes from the dataset's metadata-declared channel_names order.
    try:
        meta = store.metadata
        channel_names = list(meta.get("channel_names", []))
        if channel not in channel_names:
            raise HTTPException(
                status_code=400,
                detail=f"channel '{channel}' not in dataset; "
                f"valid: {channel_names}",
            )
        idx = channel_names.index(channel)
        with store.open_read() as s:
            intensity = s.read_array("intensity", view_bin=view_bin)
        if intensity.ndim == 3:
            image_2d = intensity[idx]
        elif intensity.ndim == 2:
            image_2d = intensity
        else:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported /intensity shape {intensity.shape}",
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"failed to read channel: {e}",
        ) from e

    # Auto-contrast: clip to percentile band, rescale to [0, 255].
    arr = image_2d.astype(np.float32, copy=False)
    lo, hi = np.percentile(arr, [contrast_low, contrast_high])
    if hi <= lo:
        # Flat image — emit black so nothing accidentally looks bright.
        scaled = np.zeros_like(arr, dtype=np.uint8)
    else:
        scaled = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(scaled, mode="L").save(buf, format="PNG", optimize=False)
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/labels_image")
def labels_image(payload: dict[str, Any]) -> Response:
    """Return a colorized PNG of the active segmentation labels.

    Companion to /channel_image: produces the overlay the React viewer
    stacks over the channel raster. Each unique cell ID gets a distinct
    color from a fixed 20-color palette indexed by `id % 20`; pixels
    with label 0 (background) are fully transparent so CSS opacity
    blending in the WebView works.

    Request body:
        path:         absolute .h5 path (required)
        segmentation: label layer name in /labels/ (required)
        view_bin:     optional downsample factor, default 1

    Response: image/png (RGBA) bytes.
    """
    path = payload.get("path")
    seg_name = payload.get("segmentation")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not seg_name:
        raise HTTPException(status_code=400, detail="segmentation is required")

    view_bin = int(payload.get("view_bin", 1))

    import io

    import numpy as np
    from PIL import Image

    from percell4.store import DatasetStore

    store = DatasetStore(path)
    if not store.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")

    try:
        with store.open_read() as s:
            labels = s.read_labels(seg_name, view_bin=view_bin)
    except KeyError as e:
        raise HTTPException(
            status_code=404, detail=f"segmentation '{seg_name}' not found",
        ) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"failed to read labels: {e}",
        ) from e

    # 20-color perceptually-distinct palette (R, G, B, A). Background
    # gets alpha=0; cells get full alpha and CSS-side opacity blends
    # the overlay over the channel image.
    PALETTE = np.array(
        [
            [0xE7, 0x4C, 0x3C, 255],  # red
            [0x2E, 0xCC, 0x71, 255],  # green
            [0x34, 0x98, 0xDB, 255],  # blue
            [0xF1, 0xC4, 0x0F, 255],  # yellow
            [0x9B, 0x59, 0xB6, 255],  # purple
            [0x1A, 0xBC, 0x9C, 255],  # teal
            [0xE6, 0x7E, 0x22, 255],  # orange
            [0xEC, 0x40, 0x7A, 255],  # pink
            [0x16, 0xA0, 0x85, 255],  # dark teal
            [0xF3, 0x9C, 0x12, 255],  # amber
            [0x8E, 0x44, 0xAD, 255],  # dark purple
            [0x27, 0xAE, 0x60, 255],  # dark green
            [0x29, 0x80, 0xB9, 255],  # dark blue
            [0xC0, 0x39, 0x2B, 255],  # dark red
            [0xD3, 0x54, 0x00, 255],  # dark orange
            [0x73, 0xC6, 0xB6, 255],  # mint
            [0xF5, 0xB7, 0xB1, 255],  # light pink
            [0xA9, 0xCC, 0xE3, 255],  # light blue
            [0xFA, 0xD7, 0xA0, 255],  # light orange
            [0xD2, 0xB4, 0xDE, 255],  # light purple
        ],
        dtype=np.uint8,
    )

    h, w = labels.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    nonzero = labels > 0
    if nonzero.any():
        color_idx = (labels[nonzero].astype(np.int64) % len(PALETTE))
        rgba[nonzero] = PALETTE[color_idx]
    # Background pixels already (0, 0, 0, 0) — transparent.

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=False)
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/phasor/histogram")
def phasor_histogram(payload: dict[str, Any]) -> dict[str, Any]:
    """Stub: phasor density points (g, s) for a channel."""
    random.seed(11)
    n = 4000
    pts = [
        [
            max(0.0, min(1.0, 0.5 + random.gauss(0, 0.12))),
            max(0.0, min(0.5, 0.32 + random.gauss(0, 0.08))),
        ]
        for _ in range(n)
    ]
    return {"channel": payload.get("channel", "NADH"), "harmonic": payload.get("harmonic", 1), "g_s": pts}


# ── Long-running tasks: progress over WebSocket ─────────────────────


class TaskBus:
    """Single in-memory broadcaster for task progress events."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def emit(self, event: dict[str, Any]) -> None:
        msg = json.dumps(event)
        dead = []
        for ws in self._clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


bus = TaskBus()


@app.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    await bus.connect(ws)
    try:
        await ws.send_text(json.dumps({"type": "ready"}))
        while True:
            # Drain anything the frontend sends so the socket stays open;
            # the frontend is consumer-only right now.
            await ws.receive_text()
    except WebSocketDisconnect:
        bus.disconnect(ws)


async def _simulate_task(task_id: str, label: str, duration_ms: int) -> None:
    """Tick fake progress events at ~16 fps for `duration_ms`, then finish."""
    await bus.emit({"type": "task_started", "task_id": task_id, "label": label})
    steps = max(1, duration_ms // 60)
    for i in range(1, steps + 1):
        await asyncio.sleep(0.06)
        await bus.emit({
            "type": "task_progress",
            "task_id": task_id,
            "progress": i / steps,
        })
    await bus.emit({
        "type": "task_finished",
        "task_id": task_id,
        "success": True,
        "message": f"{label} — complete",
    })


@app.post("/cellpose")
async def cellpose(payload: dict[str, Any]) -> dict[str, str]:
    """Run Cellpose against a real .h5 channel and save the result.

    Fires an async task that broadcasts progress over /events and
    returns the task_id immediately. The actual inference runs in a
    thread pool via asyncio.to_thread so the event loop can keep
    serving WS broadcasts.

    Request body:
        path:               absolute .h5 path
        channel:            channel name to segment (required)
        model:              cellpose model name (default "cyto3")
        diameter:           pixels, None for auto-detect
        gpu:                bool
        remove_edge_cells:  bool, default True
        name:               optional explicit segmentation name; if
                            omitted SegmentCells synthesizes one from
                            the cell count.

    Errors are reported via the task_finished event with success=False,
    not by HTTP status — the HTTP call only validates inputs.
    """
    path = payload.get("path")
    channel = payload.get("channel")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if not channel:
        raise HTTPException(status_code=400, detail="channel is required")

    task_id = "cellpose"
    asyncio.create_task(_run_cellpose_task(task_id, payload))
    return {"task_id": task_id}


async def _run_cellpose_task(task_id: str, payload: dict[str, Any]) -> None:
    """Drive the full read → infer → finalize sequence with progress events."""
    from pathlib import Path as _Path

    path = payload["path"]
    channel = payload["channel"]
    model_type = payload.get("model", "cyto3")
    diameter = payload.get("diameter") or None
    gpu = bool(payload.get("gpu", False))
    remove_edge_cells = bool(payload.get("remove_edge_cells", True))
    name = payload.get("name")

    label = f"Cellpose [{model_type}, d={diameter or 'auto'}]"
    await bus.emit({"type": "task_started", "task_id": task_id, "label": label})

    try:
        from percell4.adapters.cellpose import run_cellpose
        from percell4.adapters.hdf5_store import (
            Hdf5DatasetRepository,
            _build_handle_metadata,
        )
        from percell4.application.session import Session
        from percell4.application.use_cases.segment_cells import SegmentCells
        from percell4.domain.dataset import DatasetHandle
        from percell4.store import DatasetStore

        # ── Phase 1: open dataset, pick the channel ────────────────
        await bus.emit({"type": "task_progress", "task_id": task_id, "progress": 0.05})

        store = DatasetStore(path)
        if not store.exists():
            raise FileNotFoundError(f"file not found: {path}")
        handle = DatasetHandle(
            path=_Path(path), metadata=_build_handle_metadata(store),
        )
        channel_names = list(handle.metadata.get("channel_names", []))
        if channel not in channel_names:
            raise ValueError(
                f"channel '{channel}' not in dataset; valid: {channel_names}",
            )
        idx = channel_names.index(channel)

        # Load the channel image. Multi-channel files are (C, H, W);
        # single-channel files may be (H, W) directly.
        with store.open_read() as s:
            intensity = s.read_array("intensity")
        if intensity.ndim == 3:
            image_2d = intensity[idx]
        elif intensity.ndim == 2:
            image_2d = intensity
        else:
            raise ValueError(
                f"unsupported /intensity shape {intensity.shape}; "
                "expected (C,H,W) or (H,W)",
            )

        # ── Phase 2: cellpose inference (slow; can take seconds) ──
        await bus.emit({"type": "task_progress", "task_id": task_id, "progress": 0.2})
        raw_masks = await asyncio.to_thread(
            run_cellpose,
            image_2d,
            model_type=model_type,
            diameter=diameter,
            gpu=gpu,
        )

        # ── Phase 3: finalize via SegmentCells ────────────────────
        await bus.emit({"type": "task_progress", "task_id": task_id, "progress": 0.92})
        session = Session()
        session.set_dataset(handle)
        result = SegmentCells(Hdf5DatasetRepository(), session).finalize(
            raw_masks,
            remove_edge_cells=remove_edge_cells,
            name=name,
        )

        await bus.emit({"type": "task_progress", "task_id": task_id, "progress": 1.0})
        if result.n_cells == 0:
            message = (
                f"Cellpose: 0 cells in channel '{channel}' (saved as "
                f"'{result.seg_name}'). Try a different channel, adjust "
                f"diameter, or check the image."
            )
        else:
            message = (
                f"Cellpose: {result.n_cells} cells saved as "
                f"'{result.seg_name}' ({result.edge_removed} edge, "
                f"{result.small_removed} small removed)"
            )
        await bus.emit({
            "type": "task_finished",
            "task_id": task_id,
            "success": True,
            "message": message,
            "extra": {"new_segmentation": result.seg_name},
        })
    except Exception as e:  # noqa: BLE001
        await bus.emit({
            "type": "task_finished",
            "task_id": task_id,
            "success": False,
            "message": f"Cellpose failed: {e}",
        })


@app.post("/phasor/compute")
async def compute_phasor(payload: dict[str, Any]) -> dict[str, str]:
    h = payload.get("harmonic", 1)
    asyncio.create_task(_simulate_task("phasor", f"Computing phasor (h={h})", 2400))
    return {"task_id": "phasor"}


@app.post("/wavelet")
async def wavelet(payload: dict[str, Any]) -> dict[str, str]:
    lvl = payload.get("level", 9)
    asyncio.create_task(_simulate_task("wavelet", f"Wavelet filter (lvl {lvl})", 1800))
    return {"task_id": "wavelet"}


@app.post("/workflow/{name}")
async def workflow(name: str, payload: dict[str, Any]) -> dict[str, str]:
    task_id = f"wf:{name}"
    asyncio.create_task(_simulate_task(task_id, f"Workflow '{name}'", 6000))
    return {"task_id": task_id}


def main() -> None:
    """Sidecar entry point. PyInstaller picks this up via `backend/main.py`."""
    print(f"[backend] PerCell4 FastAPI sidecar listening on 127.0.0.1:{PORT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
