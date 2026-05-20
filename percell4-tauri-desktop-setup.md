# PerCell4 Desktop App — Tauri Setup Guide

## 1. Overview

This guide instructs your coding agent to convert the existing **React + TypeScript + TanStack Start** web project into a **Tauri desktop application** for Windows, macOS, and Linux. The app will launch as a standalone native executable, **not** a browser-based web app.

## 2. Prerequisites (One-Time per Dev Machine)

```bash
# 1. Install Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# 2. Install system dependencies for WebKit2GTK (Linux)
sudo apt-get update
sudo apt-get install libwebkit2gtk-4.1-dev \
  build-essential \
  curl \
  wget \
  file \
  libxdo3 \
  libssl-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev

# 3. Install Tauri CLI v2
cargo install tauri-cli@^2 --locked

# 4. Verify
cargo tauri --version
```

## 3. Vite Configuration Changes

In `vite.config.ts`, ensure `base: './'` so assets load correctly via `file://` protocol inside the Tauri WebView:

```typescript
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

export default defineConfig({
  tanstackStart: {
    server: { entry: "server" },
  },
  vite: {
    base: './',
    server: {
      port: 8080,
      strictPort: true,
    },
    build: {
      target: 'esnext',
    },
  },
});
```

## 4. Initialize Tauri in the Project

Run this inside the project root:

```bash
# Install Tauri as a dev dependency
npm install -D @tauri-apps/cli@^2

# Initialize Tauri (creates src-tauri/ directory)
npx tauri init
```

Answer the prompts as follows:
- **App name**: `PerCell4`
- **Window title**: `PerCell4`
- **WebAssetsDir**: `dist` (or whatever Vite outputs to)
- **Dev server URL**: `http://localhost:8080`
- **Dev command**: `npm run dev`
- **Build command**: `npm run build`

## 5. Tauri Configuration (`src-tauri/tauri.conf.json`)

Replace or update `tauri.conf.json` with the following:

```json
{
  "$schema": "../node_modules/@tauri-apps/cli/config.schema.json",
  "productName": "PerCell4",
  "version": "0.1.1",
  "identifier": "com.percell4.desktop",
  "build": {
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build",
    "frontendDist": "../../dist",
    "devUrl": "http://localhost:8081"
  },
  "app": {
    "windows": [
      {
        "title": "PerCell4",
        "width": 1400,
        "height": 900,
        "minWidth": 800,
        "minHeight": 600,
        "resizable": true,
        "fullscreen": false,
        "visible": true,
        "decorations": true
      }
    ],
    "security": {
      "csp": null,
      "capabilities": []
    }
  },
  "bundle": {
    "active": true,
    "targets": ["deb", "rpm", "dmg", "nsis", "app", "appimage"],
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ]
  }
}
```

## 6. Python Backend as Tauri Sidecar

### 6.1 Build Python Backend Executable

From the Python backend directory:

```bash
pip install pyinstaller
pyinstaller --onefile --name percell4-backend backend/main.py
```

This produces a single executable: `dist/percell4-backend` (or `.exe` on Windows).

### 6.2 Configure Sidecar in `tauri.conf.json`

Add to `tauri.conf.json` under the `app` key:

```json
{
  "app": {
    "withGlobalTauri": true
  },
  "plugins": {
    "shell": {
      "open": true,
      "sidecar": true,
      "scope": [
        {
          "name": "percell4-backend",
          "cmd": "percell4-backend",
          "args": true
        }
      ]
    }
  }
}
```

### 6.3 Rust Main — Spawn Sidecar on Launch

In `src-tauri/src/main.rs`:

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Arc;
use tauri::{Manager, State};
use tokio::sync::Mutex;
use tokio::process::Command;
use tokio::io::{AsyncBufReadExt, BufReader};

struct BackendHandle {
    child: Arc<Mutex<tokio::process::Child>>,
}

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[tokio::main]
async fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let app_handle = app.handle().clone();

            // Spawn Python sidecar
            let sidecar_path = app_handle
                .path()
                .resolve("percell4-backend", tauri::path::BaseDirectory::Resource)?;

            let mut child = Command::new(sidecar_path)
                .env("PERCELL4_PORT", "8765")
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
                .expect("Failed to spawn Python backend");

            // Log stdout / stderr
            if let Some(stdout) = child.stdout.take() {
                let reader = BufReader::new(stdout);
                tauri::async_runtime::spawn(async move {
                    let mut lines = reader.lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        println!("[PYTHON STDOUT] {}", line);
                    }
                });
            }
            if let Some(stderr) = child.stderr.take() {
                let reader = BufReader::new(stderr);
                tauri::async_runtime::spawn(async move {
                    let mut lines = reader.lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        eprintln!("[PYTHON STDERR] {}", line);
                    }
                });
            }

            // Store handle so it can be killed on app exit
            app.manage(BackendHandle {
                child: Arc::new(Mutex::new(child)),
            });

            Ok(())
        })
        .on_window_event(|app, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Kill Python backend when window closes
                if let Some(handle) = app.try_state::<BackendHandle>() {
                    let mut child = tauri::async_runtime::block_on(handle.child.lock());
                    let _ = child.start_kill();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![greet])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

### 6.4 Add Required Cargo Dependencies

In `src-tauri/Cargo.toml`, add:

```toml
[dependencies]
tauri = { version = "2.0", features = ["shell-open"] }
tauri-plugin-shell = "2"
tokio = { version = "1", features = ["process", "io-util", "rt-multi-thread"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

## 7. Frontend ↔ Python Communication

### 7.1 REST/FastAPI Approach

From the React frontend, use standard `fetch` to talk to the Python backend over localhost HTTP:

```typescript
// src/lib/api.ts
const API_BASE = "http://127.0.0.1:8765";

export async function loadImage(path: string) {
  const res = await fetch(`${API_BASE}/load_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return res.json();
}

export async function runCellpose(params: CellposeParams) {
  const res = await fetch(`${API_BASE}/cellpose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return res.json();
}

export async function getPhasorHistogram(params: PhasorParams) {
  const res = await fetch(`${API_BASE}/phasor/histogram`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return res.json();
}
```

### 7.2 WebSocket Approach (for live events/progress)

```typescript
let ws: WebSocket | null = null;

export function connectEventBus(onMessage: (msg: any) => void) {
  ws = new WebSocket("ws://127.1:8765/ws");
  ws.onopen = () => console.log("Event bus connected");
  ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(() => connectEventBus(onMessage), 3001);
  return () => ws?.close();
}
```

## 8. Native Interop from React (Optional)

If you need to call OS-level APIs (file dialogs, native menus):

```bash
npm install @tauri-apps/api
```

```typescript
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/api/dialog";

// Call a Rust command
const result = await invoke("greet", { name: "PerCell4" });

// Native file picker
const selected = await open({
  multiple: false,
  filters: [{ name: "Images", extensions: ["tiff", "tif", "nd2", "czi"] }],
});
```

## 9. Development & Build Workflow

```bash
# Dev mode (starts Vite + Tauri window with hot reload)
npx tauri dev

# Production build (creates installer + binary)
npx tauri build
```

Build outputs per platform:
- **Windows**: `src-tauri/target/release/bundle/nsis/*.exe`
- **macOS**: `src-tauri/target/release/bundle/macos/*.app`
- **Linux**: `src-tauri/target/release/bundle/appimage/*.AppImage` and `*.deb`

## 10. Key Decisions Log

| Decision | Rationale |
|----------|-----------|
| Tauri over Electron | Smaller binary (~5–15 MB vs ~150 MB), lower RAM, faster startup, smaller security surface |
| Python as sidecar | Keeps existing Python backend intact; no rewrite needed. Sidecar = bundled executable spawned by Rust |
| Web frontend in WebView | Reuse existing React/TypeScript UI prototype; no Qt/PySide needed |
| FastAPI backend on localhost | Simplest bridging: Python exposes REST/WebSocket, frontend calls via fetch |
| `base: './'` in Vite | Required for `file://` protocol asset loading inside Tauri WebView |
| Port 8765 for Python | Arbitrary; chosen to avoid common dev ports. Must match frontend `API_BASE` |

## 11. Files Your Agent Must Touch

1. `vite.config.ts` — add `base: './'` and server settings
2. `src-tauri/tauri.conf.json` — Tauri app config, window settings, sidecar config
3. `src-tauri/Cargo.toml` — Rust dependencies
4. `src-tauri/src/main.rs` — Rust entry point, sidecar spawning
5. `src/lib/api.ts` — (new) frontend API client calling Python backend

## 12. Testing Checklist

- [ ] `npx tauri dev` opens a native window with the React UI visible
- [ ] Python backend starts automatically and logs appear in terminal
- [ ] Frontend `fetch` calls to `http://127.0.0.1:8765` succeed
- [ ] Closing the Tauri window kills the Python process (no orphan processes)
- [ ] `npx tauri build` produces a working `.exe` / `.app` / `.AppImage`
- [ ] All image file formats load correctly via OS native dialogs
