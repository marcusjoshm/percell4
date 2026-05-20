# PerCell4 — Tauri + React desktop app

Native desktop shell (Tauri 2 / Rust) wrapping a React 19 + TanStack
Router + Tailwind v4 UI. The Python core runs as a sidecar process
behind a FastAPI + WebSocket bridge (see `../backend/`).

## Prerequisites (per machine, one-time)

1. **Rust toolchain**
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   source ~/.cargo/env
   ```
2. **Node 22+** (already present in this environment).
3. **Linux only** — WebKit2GTK system deps:
   ```bash
   sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
     libxdo3 libssl-dev libayatana-appindicator3-dev librsvg2-dev
   ```

## Install JS deps

```bash
cd desktop
npm install
```

## Run the UI alone (no Tauri, no Python)

```bash
npm run dev
# open http://localhost:8080
```

Selection sync between viewer / table / scatter / phasor works against
the in-store fake data (312 cells, 3 channels). Tasks animate a fake
progress bar driven entirely client-side.

## Run the full desktop app (Tauri + Python sidecar)

The Tauri shell needs the Python sidecar binary in
`desktop/src-tauri/binaries/`. Build it once with PyInstaller (see
`../backend/README.md`), then:

```bash
cd desktop
npm run tauri:dev
```

This spawns:
- Vite dev server on :8080 (React UI)
- The Tauri Rust shell, which boots the WebView pointing at :8080
- The Python sidecar binary, which serves :8765 (REST + WS)

The window appears with the React UI inside. Closing it kills the
sidecar (`on_window_event(Destroyed)` in `src-tauri/src/lib.rs`).

## Production build

```bash
cd desktop
npm run tauri:build
```

Outputs:
- macOS: `src-tauri/target/release/bundle/macos/PerCell4.app` + `dmg/`
- Windows: `src-tauri/target/release/bundle/nsis/`
- Linux: `src-tauri/target/release/bundle/appimage/` + `deb/`

## Architecture

```
desktop/
├── package.json           — React + TanStack + Tailwind + Tauri CLI
├── vite.config.ts         — base:'./' is critical for file:// loading
├── index.html             — single SPA entry
├── src/
│   ├── main.tsx           — React + Router bootstrap
│   ├── styles.css         — Tailwind v4 tokens + dark theme
│   ├── routes/
│   │   ├── __root.tsx     — root with notFound/error components
│   │   └── index.tsx      — the only route — mounts the workspace
│   ├── percell/
│   │   ├── store.ts       — Zustand global state (single source of truth)
│   │   ├── mock.ts        — fake cells + metric column definitions
│   │   ├── ui.tsx         — primitives: PanelHeader, GroupBox, MiniButton…
│   │   ├── Chrome.tsx     — MenuBar, SessionBar, HubSidebar, StatusBar
│   │   ├── Viewer.tsx     — central image viewer (SVG cell overlay)
│   │   ├── Companions.tsx — Cell Table / Data Plot / Phasor Plot tab dock
│   │   └── TaskPanels.tsx — 8 hub category panels (I/O · Segment · …)
│   └── lib/
│       ├── utils.ts       — cn() helper (clsx + tailwind-merge)
│       └── api.ts         — fetch + WebSocket client to FastAPI sidecar
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json    — window, bundle targets, sidecar registration
    ├── build.rs
    ├── capabilities/default.json
    └── src/
        ├── main.rs        — windows_subsystem guard + lib.run()
        └── lib.rs         — spawn sidecar, pipe stdio, kill on close
```

## Cross-view selection sync (the killer feature)

Every panel reads from `usePerCell` (Zustand). When one panel calls
`selectOne(id)`, every other panel re-renders to highlight that cell.

- Click a cell label in the viewer → table scrolls + highlights row;
  data plot turns the point red.
- Shift-drag a rectangle in the data plot → every enclosed point
  becomes the selection; viewer + table follow.
- Analysis panel "Filter→Sel" → viewer dims non-filtered cells, table
  hides them, plot hides them.
- Press `M` over the viewer → enter multi-select mode, click cells to
  stage, Enter to commit, Esc to cancel.

## Switching to the real Python backend

The UI is wired to fake data in `store.ts`. To put a panel on real
data:

1. Make sure the sidecar is running (`python -m backend.main` or via
   `tauri dev`).
2. In the relevant action (e.g. `runTask`), replace the local
   `requestAnimationFrame` ticker with a call to `startCellpose()` (or
   the relevant `start*` in `lib/api.ts`) and a `subscribeEvents()`
   listener.
3. The function signatures in `lib/api.ts` match the mock — components
   don't need to change.
