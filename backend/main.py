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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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


@app.get("/measurements")
def measurements() -> dict[str, Any]:
    """Stub: 312 fake cells with 8 numeric metrics."""
    random.seed(7)
    rows = []
    for i in range(312):
        area = 200 + random.random() * 900
        mean_ch1 = 400 + random.random() * 1400
        rows.append({
            "label": i + 1,
            "area_px": round(area, 1),
            "mean_DAPI": round(mean_ch1, 1),
            "mean_GFP": round(200 + random.random() * 1000, 1),
            "mean_NADH": round(410 + random.random() * 240, 1),
            "phasor_g_NADH": round(0.45 + random.random() * 0.08, 3),
            "phasor_s_NADH": round(0.32 + random.random() * 0.05, 3),
            "eccentricity": round(0.2 + random.random() * 0.7, 3),
            "integrated": round(area * mean_ch1, 0),
        })
    return {"rows": rows, "n_cells": len(rows)}


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
    model = payload.get("model", "cyto3")
    asyncio.create_task(_simulate_task("cellpose", f"Cellpose [{model}]", 3200))
    return {"task_id": "cellpose"}


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
