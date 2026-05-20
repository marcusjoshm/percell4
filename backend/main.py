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
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
    """Stub: return fake dataset metadata for any path."""
    return {
        "path": payload.get("path", "experiment_0824_HeLa.h5"),
        "shape": [4, 1024, 1024],
        "channel_names": ["DAPI", "GFP", "Cy5", "NADH"],
        "segmentation_names": ["dapi_seg", "nuclei_v2"],
        "mask_names": ["thresh_488", "particles"],
        "flim_frequency_mhz": 80.0,
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
