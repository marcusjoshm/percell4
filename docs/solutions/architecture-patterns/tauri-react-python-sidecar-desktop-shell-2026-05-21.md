---
title: "Tauri + React + Python FastAPI sidecar as desktop shell for percell4"
date: 2026-05-21
problem_type: architecture_pattern
module: desktop
related_components:
  - "backend/sidecar"
  - "src/percell4"
component: tooling
severity: high
applies_when:
  - "Packaging a Python scientific/analysis core as a cross-platform desktop app"
  - "Choosing between Electron, Tauri, and a native Python (PySide6/Qt) UI"
  - "Embedding a long-running Python process behind a web UI in a native shell"
  - "Streaming progress for long compute jobs (Cellpose, segmentation) to a web frontend"
  - "Bundling Python + heavy ML deps (Cellpose, tifffile, websockets) with PyInstaller"
tags:
  - "tauri"
  - "react"
  - "fastapi"
  - "pyinstaller"
  - "sidecar"
  - "desktop-shell"
  - "websocket"
  - "webview"
---

## Context

PerCell4 is a microscopy analysis tool with a Python core: heavy NumPy / pandas / h5py data pipeline, Cellpose for segmentation, and a domain layer (`src/percell4/`) built around the seven I/O principles. The production UI was PyQt5 with embedded napari, pyqtgraph panels, and a hub launcher. That stack solved its main problem — running OpenGL-accelerated image rendering inside a desktop process while sharing one `CellDataModel` across windows — but four things kept hurting:

1. **Windowing.** Multi-window Qt apps are awkward on modern macOS. Always-on-top, focus handling, layout presets, and the menubar all behave differently per platform; we kept finding subtle differences between PerCell3 on Windows and macOS.
2. **Layout iteration is slow in Qt.** Tweaking a panel's grouping, padding, or color scheme requires `.ui` edits, signal rewiring, and a full app restart. There is no design tool feedback loop.
3. **Distribution.** Shipping a working PyQt5 + napari app to a Windows research machine where `pip install` is unsafe is painful. The PerCell3 install playbook is long, version-fragile, and breaks on macOS Sequoia regularly.
4. **Recruiting future contributors.** Researchers in the lab know HTML/CSS/JS far better than they know PyQt's event model. A web UI lowers the bar for someone else to pick up where we leave off.

We considered three alternatives:

- **Electron.** Same web-UI advantages but a ~150 MB browser per app, two JS runtimes (main + renderer), and Node's npm ecosystem dragged into a research tool. Tauri's WebView ships at ~5–15 MB and reuses the OS browser engine.
- **Continuing with PyQt5/napari.** Real cost of switching. But napari's strengths (OpenGL image rendering, zoom/pan, plugin ecosystem) are exactly what we struggle to extend, not what we lean on. The viewer is one component out of dozens.
- **Pure web app (FastAPI + React, hosted).** Loses local-filesystem access. Microscopy users keep terabytes of `.h5` and `.sdt` on local SSDs and external drives; uploading every dataset to a server is a non-starter.

The chosen solution: keep the Python core untouched, wrap it in a FastAPI sidecar, replace the Qt windowing/layout layer with a React WebView shell driven by a Rust Tauri host. Iteration moves to the web stack (Vite HMR, browser devtools, design tools like Lovable/Figma), distribution becomes a single binary, and the domain logic ports forward at zero cost.

## Guidance

The architecture is three cooperating processes inside one OS-level app, with two wire protocols between them.

### Three processes

1. **Rust shell** (`desktop/src-tauri/src/lib.rs`). Owns the OS window, the WebView, and the sidecar lifecycle. ~80 lines of Rust. Spawns the Python sidecar on startup, pipes its stdio to the Tauri dev console, kills it cleanly when the user closes the window.

2. **Python FastAPI sidecar** (`backend/main.py`). The Python core inside a FastAPI app. HTTP endpoints for synchronous reads (`/load_image`, `/channel_image`, `/labels_image`, `/measurements`), a single `/events` WebSocket for task progress. Stateless: each request supplies its own `path` + active selectors so the sidecar can be killed and restarted without the frontend losing context (state lives in the React store).

3. **Vite + React frontend** (`desktop/src/`). React 19 + TanStack Router + Tailwind v4 + Zustand. Loaded by the Tauri WebView from a Vite dev server in development, from bundled static files in production.

### Sidecar spawn from setup()

The Rust shell registers the Python binary in `tauri.conf.json` as `bundle.externalBin`, then spawns it via `tauri-plugin-shell` inside the app's `setup()` callback. The handle is stored on the app state so a window-destroyed event can find and kill it:

```rust
// desktop/src-tauri/src/lib.rs
.setup(|app| {
    let sidecar = app
        .shell()
        .sidecar("percell4-backend")
        .expect("`percell4-backend` sidecar not found")
        .env("PERCELL4_PORT", BACKEND_PORT);

    let (mut rx, child) = sidecar
        .spawn()
        .expect("failed to spawn `percell4-backend` sidecar");

    let child_holder = Arc::new(Mutex::new(Some(child)));
    app.manage(BackendHandle { child: child_holder.clone() });

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => println!("[backend] {}", ...),
                CommandEvent::Stderr(line) => eprintln!("[backend!] {}", ...),
                CommandEvent::Terminated(payload) => { /* break */ }
                _ => {}
            }
        }
    });
    Ok(())
})
.on_window_event(|window, event| {
    if let tauri::WindowEvent::Destroyed = event {
        // kill sidecar
    }
})
```

The `externalBin` registration in `tauri.conf.json`:

```json
"externalBin": ["binaries/percell4-backend"]
```

Tauri's bundler picks `binaries/percell4-backend-<target-triple>` (e.g. `-aarch64-apple-darwin`) for the host platform.

### Wire protocol

- **HTTP on `127.0.0.1:8765`** for synchronous operations. JSON in, JSON or binary (PNG) out. CORS open to localhost only.
- **WebSocket on `/events`** for task progress. The backend has a single `TaskBus` broadcaster that fan-outs every event to every connected client. Frontend subscribes once at app boot.

Three event shapes:

```ts
{ type: "task_started",  task_id, label }
{ type: "task_progress", task_id, progress: 0..1 }
{ type: "task_finished", task_id, success, message,
  extra?: Record<string, unknown> }
```

The `extra` field on `task_finished` is the key affordance for compounding. Task-specific structured data — like `{ new_segmentation: "cellpose_3" }` after a Cellpose run — rides along with the completion event. Frontend handlers consume `extra` to update related state (snap the active segmentation selector, refetch the labels overlay) without ever parsing the human-readable `message` string.

### Frontend state in Zustand

A single `usePerCell` store. Every panel reads from it; every action writes to it. The WS subscription bridge installed at app boot routes incoming events through three underscore-prefixed framework handlers on the store:

```tsx
// desktop/src/main.tsx
subscribeEvents((event) => {
  const s = usePerCell.getState();
  if (event.type === "task_started")  s._onTaskStarted(event.label);
  else if (event.type === "task_progress") s._onTaskProgress(event.progress);
  else if (event.type === "task_finished")
    s._onTaskFinished(event.task_id, event.message, event.extra);
});
```

`subscribeEvents` auto-reconnects on close, so a sidecar restart is invisible to the UI.

### Image data as PNG, stacked as CSS layers

The Tauri WebView cannot run napari (Qt + OpenGL). The moral equivalent: the backend renders each image layer to a PNG, the frontend stacks them as positioned `<img>` elements with CSS opacity. Three endpoints, three layers:

- `/channel_image` → grayscale PNG (auto-contrasted on the server side via numpy percentile)
- `/labels_image` → RGBA PNG, each cell ID painted with `id % 20` from a fixed 20-color palette, label-0 fully transparent
- `/mask_image` → RGBA PNG, foreground a single color (yellow default), background transparent

Pixel-for-pixel alignment is guaranteed by every endpoint reading at the same `view_bin` (downsample factor) and the frontend keeping all three layers' fetches keyed to the same `viewBin` state. The viewer renders them as stacked absolutely-positioned `<img>` tags with `object-contain` so they scale together as the container resizes.

### Blob URL lifecycle + last-write-wins

Each fetched PNG becomes an object URL via `URL.createObjectURL(blob)`. The store keeps the current URL and revokes the previous one on every transition so memory doesn't leak across rapid channel switches:

```ts
// desktop/src/percell/store.ts
loadChannelImage: async () => {
  const s = get();
  if (!s.datasetPath || !s.channel) {
    if (s.imageURL) URL.revokeObjectURL(s.imageURL);
    set({ imageURL: null, imageLoading: false });
    return;
  }
  set({ imageLoading: true });
  const blob = await getChannelImage({
    path: s.datasetPath, channel: s.channel, view_bin: s.viewBin,
  });
  // Staleness guard: discard if the user moved on mid-flight.
  const cur = get();
  if (cur.channel !== s.channel || cur.datasetPath !== s.datasetPath) {
    set({ imageLoading: false });
    return;
  }
  const url = URL.createObjectURL(blob);
  if (cur.imageURL) URL.revokeObjectURL(cur.imageURL);
  set({ imageURL: url, imageLoading: false });
},
```

The same shape — null check, set loading, await, re-read store, drop-if-stale, create, revoke old, set — repeats in `loadLabelsImage` and `loadMaskImage`. It's verbose but the verbosity is exactly what keeps a fast channel-flip session from leaking dozens of blob URLs or rendering a stale image over a fresh one.

### Cellpose action: HTTP kicks off, WS finishes

The runFoo pattern: the action does input validation, sets a running-task placeholder, and POSTs to start the task. The HTTP response only confirms the task is scheduled — it never carries the result. Progress + result come over the WS bridge:

```ts
runCellpose: async (params) => {
  const s = get();
  if (!s.datasetPath) {
    set({ status: "Load a dataset first…" }); return;
  }
  if (!s.channel) {
    set({ status: "Select a channel in the Session bar first" }); return;
  }
  const label = `Cellpose [${params.model}, d=${params.diameter}]`;
  set({ runningTask: { label, progress: 0, cancellable: true }, status: label });
  try {
    await startCellpose({
      path: s.datasetPath, channel: s.channel,
      model: params.model, diameter: params.diameter,
      gpu: params.gpu, remove_edge_cells: params.remove_edge_cells,
    });
  } catch (e) {
    set({ runningTask: null, status: `Cellpose request failed: ${e}` });
  }
},
```

And the corresponding `_onTaskFinished` handler:

```ts
_onTaskFinished: (taskId, message, extra) => {
  set({ runningTask: null, status: message });
  if (taskId === CELLPOSE_TASK_ID && !message.startsWith("Cellpose failed")) {
    const newSeg = typeof extra?.new_segmentation === "string"
      ? (extra.new_segmentation as string) : null;
    get().refreshDatasetMetadata().then(() => {
      if (newSeg) {
        set({ segmentation: newSeg });
        void get().loadLabelsImage();
      }
    });
  }
},
```

Note: a **lighter** `refreshDatasetMetadata()` action that updates only the name lists, never status/selection/filter. The original implementation called `loadDataset()` here and stomped on the cellpose completion message; see gotcha (g).

### Native file picker via Tauri dialog plugin

`window.prompt()` returns `null` silently inside the WKWebView. The Tauri dialog plugin is the right primitive:

```ts
// desktop/src/percell/TaskPanels.tsx
import { open as openDialog } from "@tauri-apps/plugin-dialog";

<MiniButton onClick={async () => {
  const picked = await openDialog({
    multiple: false,
    filters: [{ name: "HDF5", extensions: ["h5", "hdf5"] }],
  });
  if (typeof picked === "string") loadDataset(picked);
}}>Load Dataset…</MiniButton>
```

Wiring: `tauri-plugin-dialog = "2"` in `Cargo.toml`, `.plugin(tauri_plugin_dialog::init())` in `lib.rs`, `"dialog:default"` in `capabilities/default.json`, and `@tauri-apps/plugin-dialog` in `package.json`.

### Production app coexistence

The PyQt5 production app at `src/percell4/` keeps working on `main`. The Tauri/React shell lives at `desktop/` + `backend/` and runs via `npm run tauri:dev` from the same repo. The Python core is shared: `backend/main.py` injects `<repo>/src/` onto `sys.path` so `from percell4.store import DatasetStore` resolves both when running from source (`python -m backend.main`) and from the PyInstaller bundle (`--paths src` at build time).

### PyInstaller bundling

The sidecar binary is built with PyInstaller:

```bash
pyinstaller --onefile --name percell4-backend backend/main.py \
  --paths src \
  --collect-all cellpose --collect-all segment_anything \
  --collect-submodules torch --collect-submodules sklearn \
  --collect-submodules uvicorn --collect-submodules websockets \
  --hidden-import wsproto --hidden-import roifile \
  --hidden-import fastremap --hidden-import fill_voids --hidden-import natsort \
  --hidden-import cv2 --hidden-import tifffile \
  --hidden-import PIL.Image --hidden-import PIL.PngImagePlugin \
  --hidden-import percell4.store --hidden-import percell4.adapters.hdf5_store \
  --hidden-import percell4.application.use_cases.segment_cells

mkdir -p desktop/src-tauri/binaries
TRIPLE=$(rustc -vV | sed -n 's/host: //p')
mv dist/percell4-backend desktop/src-tauri/binaries/percell4-backend-$TRIPLE
```

Binary size budget over the build cycle: 18 MB stub → 134 MB with `h5py`+`pandas`+`numpy` → 295 MB with Cellpose + torch + segment_anything + cv2 + sklearn. That last jump is the price of bundling Cellpose; a one-time download per user is acceptable for now.

What to exclude is more subtle than what to include. The exclusions you *can* safely take: `PyQt5`, `napari`, `pyqtgraph`, `matplotlib`. The Python core has fully decoupled the web sidecar from any GUI dep. But see gotcha (b) — `--exclude-module` cascades transitively.

## Why This Matters

Every choice in this stack compounds.

- **Web frontend = fast UI iteration.** Lovable, Figma, browser devtools, Tailwind hot-reload. Designing a new panel takes minutes instead of hours-of-`.ui`-twiddling. A non-Python contributor can ship UI.
- **Tauri WebView is small.** ~5–15 MB vs. ~150 MB for Electron. Critical when the bundle already costs 300 MB for the Python sidecar.
- **The Python core is untouched.** Every existing use case (`MeasureCells`, `SegmentCells`, `ComputePhasor`, …) lights up incrementally as new endpoints. No domain logic rewrite. The audit-driven I/O principles continue to apply unchanged.
- **The sidecar is killable.** Sidecar restarts (e.g. after a Python crash, or to pick up a code change) don't lose UI state — state lives in the React Zustand store. The frontend's WS reconnect loop makes the restart invisible.
- **Distribution = single binary.** Tauri produces `.app` / `.dmg` / `.nsis` / `.appimage` / `.deb`. No `pip install`, no Conda. Lab Windows machines get a normal installer.
- **Stateless requests = no session affinity.** Every endpoint takes the absolute path + active selectors in the request body. Multiple windows, multiple users on the same machine, or future cloud deployment are all just changing where the HTTP base URL points.
- **PNG-as-overlay is a portable rendering primitive.** It's not napari, but it ports trivially to any client that can render an `<img>`: future mobile companion view, embeddable thumbnails in reports, web previews of saved analyses.

## When to Apply

Apply this pattern when:

- You have an existing Python desktop app that you want a modern web-style UI for.
- You have heavy Python dependencies (PyTorch, NumPy, h5py, OpenCV) that you would rather not port to JavaScript.
- You need a desktop binary (not a hosted web app) because of local-filesystem access, offline use, or user familiarity with native install.
- Your computational ops are coarse-grained — at least 100s of ms — so the HTTP/WS hop adds negligible latency.
- Your image rendering can be served as pre-rendered rasters. CSS-stacked `<img>` works fine for panning a single frame at a fixed view bin; the backend re-renders on each change. It does *not* work for smooth zoom/pan of gigapixel images — that needs a WebGL tile renderer.

Do **not** apply this pattern when:

- You are building a pure web app with a hosted backend — just use React + a server, no Tauri.
- You are building a pure CLI tool — keep Python, skip the UI complexity.
- The Qt/napari-specific features (interactive OpenGL image rendering with smooth zoom/pan, native plugin ecosystem, complex 3D widgets) are core to the product. The web stack has real gaps here.
- Your team has zero web experience and is fluent in Qt — the migration tax may not pay back.

## Examples

### 1. Rust shell sidecar spawn

`desktop/src-tauri/src/lib.rs`:

```rust
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let sidecar = app
                .shell()
                .sidecar("percell4-backend")
                .expect("`percell4-backend` sidecar not found")
                .env("PERCELL4_PORT", BACKEND_PORT);

            let (mut rx, child) = sidecar.spawn()
                .expect("failed to spawn sidecar");

            let child_holder: Arc<Mutex<Option<CommandChild>>> =
                Arc::new(Mutex::new(Some(child)));
            app.manage(BackendHandle { child: child_holder.clone() });

            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) =>
                            println!("[backend] {}", String::from_utf8_lossy(&line)),
                        CommandEvent::Stderr(line) =>
                            eprintln!("[backend!] {}", String::from_utf8_lossy(&line)),
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[backend] terminated: {:?}", payload);
                            break;
                        }
                        _ => {}
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let app = window.app_handle();
                if let Some(state) = app.try_state::<BackendHandle>() {
                    let child_holder = state.child.clone();
                    tauri::async_runtime::block_on(async move {
                        if let Some(child) = child_holder.lock().await.take() {
                            let _ = child.kill();
                        }
                    });
                }
            }
        })
        .invoke_handler(tauri::generate_handler![])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

### 2. WebSocket subscription bootstrap

`desktop/src/main.tsx`:

```tsx
import { subscribeEvents } from "./lib/api";
import { usePerCell } from "./percell/store";

subscribeEvents((event) => {
  const s = usePerCell.getState();
  if (event.type === "task_started") s._onTaskStarted(event.label);
  else if (event.type === "task_progress") s._onTaskProgress(event.progress);
  else if (event.type === "task_finished")
    s._onTaskFinished(event.task_id, event.message, event.extra);
});
```

`subscribeEvents` itself (in `desktop/src/lib/api.ts`) is ~20 lines with reconnect:

```ts
export function subscribeEvents(onEvent: (e: BackendEvent) => void): () => void {
  let closed = false;
  let ws: WebSocket | null = null;
  function connect() {
    if (closed) return;
    ws = new WebSocket(`ws://127.0.0.1:8765/events`);
    ws.onmessage = (m) => {
      try { onEvent(JSON.parse(m.data) as BackendEvent); }
      catch (err) { console.warn("bad event payload", err); }
    };
    ws.onclose = () => { if (!closed) setTimeout(connect, 1000); };
  }
  connect();
  return () => { closed = true; ws?.close(); };
}
```

### 3. The image-overlay stacking pattern

`desktop/src/percell/Viewer.tsx`:

```tsx
{/* Channel raster */}
{imageURL && (
  <img src={imageURL}
    className="absolute inset-0 w-full h-full object-contain"
    style={{ imageRendering: "pixelated" }}
    draggable={false} />
)}
{/* Segmentation overlay — stacked with user-controlled opacity. */}
{imageURL && labelsImageURL && (
  <img src={labelsImageURL}
    className="absolute inset-0 w-full h-full object-contain"
    style={{ imageRendering: "pixelated", opacity: labelsOpacity }}
    draggable={false} />
)}
{/* Mask overlay — stacked above labels so it can be faded
    independently. All three share view_bin → pixel-for-pixel align. */}
{imageURL && maskImageURL && (
  <img src={maskImageURL}
    className="absolute inset-0 w-full h-full object-contain"
    style={{ imageRendering: "pixelated", opacity: maskOpacity }}
    draggable={false} />
)}
```

### 4. Backend task with `extra` payload

`backend/main.py` — the success branch of `_run_cellpose_task`:

```python
await bus.emit({
    "type": "task_finished",
    "task_id": task_id,
    "success": True,
    "message": message,
    "extra": {"new_segmentation": result.seg_name},
})
```

## Gotchas / pitfalls

### a. PyInstaller misses string-imported modules

uvicorn loads its WebSocket protocol modules by string path at runtime (e.g. `uvicorn.protocols.websockets.websockets_impl`). PyInstaller's static analyzer sees no `import` statement and silently omits them.

**Symptom:** The bundled binary serves HTTP fine — `/health` works, `/load_image` works — but the `/events` WebSocket handshake hangs, and the sidecar log shows no `WebSocket /events` accept line. The frontend's WS reconnect loop hammers `connect()` every second to no effect.

**Fix:** `--collect-submodules uvicorn --collect-submodules websockets --hidden-import wsproto`. Same pattern bites Cellpose, which selects its backend (CUDA vs MPS vs CPU) by string lookup.

### b. `--exclude-module` cascades through dependencies

`--exclude-module tifffile` removes it from your own imports — but Cellpose internally uses `tifffile`, `roifile`, `fastremap`, `fill_voids`, `natsort`, and `cv2`. Exclude any of those and the bundled Cellpose breaks at runtime with `ModuleNotFoundError: No module named 'X'` from deep inside its code, often after a 30-second model warmup.

**Rule of thumb:** Exclude only modules you are certain no bundled dependency transitively imports. When in doubt, don't exclude.

### c. Tauri WebView disables `window.prompt()`

On macOS WKWebView, `window.prompt(...)` returns `null` immediately with no UI. The symptom looks exactly like a broken click handler — the user clicks "Load Dataset…", nothing happens, no error in the console.

**Fix:** Use Tauri's native dialog plugin.

```bash
npm i @tauri-apps/plugin-dialog
```

```toml
# Cargo.toml
tauri-plugin-dialog = "2"
```

```rust
// lib.rs
.plugin(tauri_plugin_dialog::init())
```

```json
// capabilities/default.json
"permissions": ["core:default", "shell:allow-open", "dialog:default"]
```

```ts
import { open } from "@tauri-apps/plugin-dialog";
const picked = await open({ filters: [{ name: "HDF5", extensions: ["h5", "hdf5"] }] });
```

### d. Cargo doesn't rebuild on icon-file changes

Tauri embeds icons at compile time via `tauri::generate_context!()`. When you regenerate icons with `cargo tauri icon`, the only files that change are PNGs and ICOs — no `.rs` file changes. Cargo's incremental build sees no reason to rebuild, and the running binary keeps the old icons baked in.

**Fix:** Force a rebuild of just the Tauri crate after regenerating icons.

```bash
cargo clean -p percell4    # the crate name from Cargo.toml
npm run tauri:dev
```

### e. macOS Dock caches icons by app bundle identifier

Even with a freshly compiled binary embedding the new icons, the macOS Dock can keep showing the old one. The cache is per-identifier (`com.leelab.percell4`), not per-binary.

**Fix:**

```bash
killall Dock
```

Then relaunch the app. (`sudo rm -rf /Library/Caches/com.apple.iconservices.store` is the nuclear option if Dock alone doesn't do it.)

### f. CSS `@import` ordering for Tailwind v4

Tailwind v4's preprocessor enforces standard CSS rules: `@import` directives must come before any other rule. Loading a Google Font with `@import url(...)` after `@import "tailwindcss"` triggers a build warning and may strip the font import on optimize.

**Fix:** Font imports go *first*:

```css
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap");
@import "tailwindcss";

@theme {
  --color-background: oklch(0.18 0.01 250);
  /* … */
}
```

### g. WS event handlers must not stomp on completion messages

The first version of `_onTaskFinished` after Cellpose called `loadDataset(path)` to refresh the segmentation list. That worked — the new segmentation appeared in the dropdown — but `loadDataset` also reset the status bar ("Loaded foo.h5 — …"), wiping the cellpose result message, *and* reset `selection`, `filter`, and active selectors back to first-entry defaults.

**Fix:** A lighter `refreshDatasetMetadata()` action that updates only the names lists, never status/selection/filter. Carry structured data — the new segmentation name — in the `task_finished` event's `extra` dict so the handler can snap the active selector to the new value without re-parsing the message string or doing a full refresh.

```ts
refreshDatasetMetadata: async () => {
  const s = get();
  if (!s.datasetPath) return;
  try {
    const meta = await loadImage(s.datasetPath);
    // Note: do NOT clobber pixelSizeUm or status here.
    set({
      channelNames: meta.channel_names,
      maskNames: meta.mask_names,
      segmentationNames: meta.segmentation_names,
      flimFrequencyMhz: meta.flim_frequency_mhz,
    });
  } catch { /* quiet — leave stale lists if refresh fails */ }
},
```

### h. TIFF `ResolutionUnit` conversion bug in `percell4.adapters.readers`

`read_tiff_metadata` computes `xres[1] / xres[0]` regardless of the TIFF's `ResolutionUnit` tag, and labels the result as µm. That is correct only when `ResolutionUnit == 1` (none / ImageJ convention). For `ResolutionUnit == 3` (centimeter — common on commercial microscope exports), the result is off by a factor of 10000.

We worked around it in `backend/main.py:_read_tiff_pixel_size_um` with a unit-aware extraction:

```python
# Tag 296 ResolutionUnit:
#   1 = none / unspecified — treat as µm/pixel (ImageJ convention)
#   2 = inch                — convert × 25400
#   3 = centimeter          — convert × 10000
unit_per_pixel = den / num
unit_code = int(unit_tag.value) if unit_tag is not None else 1
scale_to_um = {1: 1.0, 2: 25400.0, 3: 10000.0}.get(unit_code)
value = unit_per_pixel * scale_to_um
```

The underlying bug in `src/percell4/adapters/readers.py` still exists. It needs the same unit-aware path applied at the importer.

## Related

- [`docs/solutions/build-errors/cross-platform-packaging-review-fixes.md`](../build-errors/cross-platform-packaging-review-fixes.md) — PyInstaller hygiene rules for the existing PyQt5 production app (`collect_submodules`, `upx=False` on Windows, avoiding manual hidden-import enumeration). The Tauri sidecar PyInstaller invocation here extends those rules rather than replacing them.
