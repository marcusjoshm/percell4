# PerCell4 backend (FastAPI sidecar)

Spawned by the Tauri desktop shell on app start. Speaks HTTP + WebSocket
on `127.0.0.1:8765` (override with `PERCELL4_PORT`). The React frontend
in `desktop/src/percell/` talks to this process.

This is a **stub** right now — endpoints return fake data shaped like the
real Python core, so the UI is fully demoable end-to-end without any
real analysis happening. Replacing the stubs with calls into the
existing `src/percell4/` modules is the next branch of work.

## Run standalone (for UI dev, no Tauri)

```bash
# From repo root, with .venv activated
pip install -r backend/requirements.txt
python -m backend.main
```

Then visit `http://127.0.0.1:8765/health`.

## Build sidecar binary (for Tauri bundling)

Tauri requires the sidecar to be a single self-contained executable
named with the host's target-triple suffix.

```bash
pip install pyinstaller
pyinstaller --onefile --name percell4-backend backend/main.py

# Move the binary next to where Tauri expects it.
mkdir -p desktop/src-tauri/binaries
TRIPLE=$(rustc -vV | sed -n 's/host: //p')   # e.g. aarch64-apple-darwin
mv dist/percell4-backend desktop/src-tauri/binaries/percell4-backend-$TRIPLE
```

Then `cd desktop && npx tauri dev` will spawn the sidecar automatically.

## Endpoints (current stubs)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | Liveness probe |
| POST | `/load_image` | Dataset metadata |
| GET  | `/measurements` | Per-cell measurements (312 fake rows) |
| POST | `/phasor/histogram` | Phasor density points |
| POST | `/cellpose` | Start Cellpose run, returns `task_id` |
| POST | `/phasor/compute` | Start phasor compute |
| POST | `/wavelet` | Apply wavelet filter |
| POST | `/workflow/{name}` | Start a batch workflow |
| WS   | `/events` | Subscribes to task progress + finish events |

Long-running ops broadcast on `/events`:
```json
{ "type": "task_started",  "task_id": "...", "label": "..." }
{ "type": "task_progress", "task_id": "...", "progress": 0.42 }
{ "type": "task_finished", "task_id": "...", "success": true, "message": "..." }
```
