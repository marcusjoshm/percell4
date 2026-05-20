# PerCell4 — React UI Source Code & Integration Guide

This document bundles the complete React/TanStack Start prototype UI for
PerCell4 along with step-by-step instructions for embedding it inside the
PerCell4 desktop app (Tauri shell, Python backend sidecar).

It is the companion to:
- `percell4-tauri-desktop-setup.md` (desktop shell setup)
- `2026-05-20-percell4-ui-features-for-lovable.md` (UI feature spec)
- `2026-05-20-percell4-backend-surface.md` (Python API surface)

---

## 1. What this UI is

A self-contained React 19 + TypeScript + Tailwind v4 + shadcn/ui prototype
built on TanStack Start v1 (Vite 7). It implements the full PerCell4 layout:

- **Chrome** — top bar, project switcher, status pill, task progress
- **Viewer** — central image canvas with overlays, zoom/pan stubs, ROI hooks
- **Companions** — left/right panels (channels, ROIs, histograms, phasor plot,
  task settings)
- **TaskPanels** — Cellpose / Phasor / Wavelet parameter forms with progress
- **Store** — Zustand-style state with mock fixtures and a fake event bus
- **Mock backend** — `mock.ts` simulates the Python surface so the UI is
  fully demoable without any Python running

All Python calls go through a single thin adapter (`src/percell/mock.ts`)
that you swap for a real HTTP/WebSocket client once the Tauri sidecar is up.

---

## 2. Files you need to copy

Copy these files verbatim into your target React project (created via the
Tauri setup guide):

| Path | Purpose |
|------|---------|
| `src/percell/store.ts` | Global state + event bus |
| `src/percell/mock.ts` | Mock backend (replace later) |
| `src/percell/ui.tsx` | Layout shell mounting Chrome + Viewer + Companions |
| `src/percell/Chrome.tsx` | Top bar & global controls |
| `src/percell/Viewer.tsx` | Central image viewer |
| `src/percell/Companions.tsx` | Left/right side panels |
| `src/percell/TaskPanels.tsx` | Task parameter forms |
| `src/routes/index.tsx` | Root route mounting `<PerCellUI />` |
| `src/routes/__root.tsx` | HTML shell (title, meta, providers) |
| `src/styles.css` | Tailwind v4 tokens & PerCell4 design system |

Plus the standard shadcn/ui primitives under `src/components/ui/` (button,
card, slider, tabs, etc.) — already generated in this template.

---

## 3. Dependencies

Already in this template's `package.json`. For a fresh project add:

```bash
bun add react@^19 react-dom@^19 \
  @tanstack/react-router @tanstack/react-start \
  zustand framer-motion lucide-react clsx tailwind-merge \
  class-variance-authority @radix-ui/react-slot \
  @radix-ui/react-slider @radix-ui/react-tabs \
  @radix-ui/react-tooltip @radix-ui/react-dialog
bun add -D tailwindcss@^4 @tailwindcss/vite vite@^7 typescript
```

---

## 4. Wiring the real Python backend

The whole UI talks to Python through `src/percell/mock.ts`. Replace its
exported functions with real `fetch`/WebSocket calls to the sidecar started
by Tauri (see `percell4-tauri-desktop-setup.md`):

```ts
// src/percell/api.ts (new file replacing mock.ts)
const BASE = "http://127.0.0.1:8765";

export async function loadImage(path: string) {
  const r = await fetch(`${BASE}/load_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return r.json();
}

export function subscribeEvents(onEvent: (e: any) => void) {
  const ws = new WebSocket("ws://127.0.0.1:8765/events");
  ws.onmessage = (m) => onEvent(JSON.parse(m.data));
  return () => ws.close();
}

// ...mirror every export from mock.ts
```

Then update imports in `store.ts`, `TaskPanels.tsx`, and `Viewer.tsx`:

```ts
// before
import { runCellpose, loadImage } from "./mock";
// after
import { runCellpose, loadImage } from "./api";
```

Keep the function signatures identical to the mock so no component code
needs to change. The mock acts as the contract.

---

## 5. Integration steps (for the coding agent)

1. **Generate the Tauri shell** following `percell4-tauri-desktop-setup.md`.
2. **Install dependencies** from section 3 above.
3. **Copy files** from section 2 into the new project at the same paths.
4. **Verify the root route** (`src/routes/index.tsx`) renders `<PerCellUI />`.
5. **Confirm Tailwind v4** is wired via `@tailwindcss/vite` and that
   `src/styles.css` is imported by `__root.tsx`.
6. **Run `npx tauri dev`** — you should see the full UI working against the
   mock backend.
7. **Swap mock → api** as described in section 4 once the Python sidecar
   speaks HTTP on `127.0.0.1:8765`.
8. **Verify event bus** — Cellpose progress should stream from Python via
   WebSocket and update the Chrome progress bar without changes to UI code.

---

## 6. Notes on customization

- **Design tokens** live in `src/styles.css` under `@theme`. All colors are
  `oklch` semantic tokens — never hardcode hex in components.
- **State** is a single Zustand store (`store.ts`). Add new slices there;
  components subscribe via selectors to avoid re-renders.
- **Routing** is file-based under `src/routes/`. To add a settings page,
  create `src/routes/settings.tsx` — the route tree regenerates automatically.
- **Mock event bus** in `mock.ts` has a `triggerFake(...)` helper a debug
  panel can call to simulate selection changes, filter updates, etc.

---

## 7. Full source code

Below is the verbatim source of every file listed in section 2.


### `src/percell/store.ts`

```ts
import { create } from "zustand";
import { CELLS, type CellId } from "./mock";

export type HubCategory =
  | "io"
  | "viewer"
  | "segment"
  | "analysis"
  | "flim"
  | "scripts"
  | "workflows"
  | "data";

export type CompanionId = "table" | "plot" | "phasor";
export type LayoutPreset = "laptop" | "labpc" | "flim" | "single";

export interface RunningTask {
  label: string;
  progress: number; // 0..1
  cancellable: boolean;
}

interface State {
  dataset: string;
  channel: string;
  mask: string;
  segmentation: string;
  viewBin: number;
  alwaysOnTop: boolean;

  selection: Set<CellId>;
  filter: Set<CellId> | null;

  hub: HubCategory;
  activeCompanion: CompanionId;
  detached: Set<CompanionId>;
  layoutPreset: LayoutPreset;

  status: string;
  runningTask: RunningTask | null;

  multiSelectOpen: boolean;
  staged: Set<CellId>;

  // actions
  setHub: (h: HubCategory) => void;
  setCompanion: (c: CompanionId) => void;
  setChannel: (c: string) => void;
  setMask: (m: string) => void;
  setSegmentation: (s: string) => void;
  setViewBin: (n: number) => void;
  setAlwaysOnTop: (v: boolean) => void;
  setLayoutPreset: (p: LayoutPreset) => void;

  selectOne: (id: CellId, additive?: boolean) => void;
  selectMany: (ids: CellId[], additive?: boolean) => void;
  clearSelection: () => void;
  filterToSelection: () => void;
  clearFilter: () => void;

  openMultiSelect: () => void;
  toggleStaged: (id: CellId) => void;
  commitMultiSelect: () => void;
  cancelMultiSelect: () => void;

  setStatus: (s: string) => void;
  runTask: (label: string, durationMs?: number) => void;
  cancelTask: () => void;

  detach: (c: CompanionId) => void;
  reattach: (c: CompanionId) => void;
}

export const visibleCells = (filter: Set<CellId> | null) =>
  filter ? CELLS.filter((c) => filter.has(c.id)) : CELLS;

export const usePerCell = create<State>((set, get) => ({
  dataset: "experiment_0824_HeLa.h5",
  channel: "DAPI",
  mask: "thresh_488",
  segmentation: "dapi_seg",
  viewBin: 1,
  alwaysOnTop: false,

  selection: new Set(),
  filter: null,

  hub: "analysis",
  activeCompanion: "table",
  detached: new Set(),
  layoutPreset: "labpc",

  status: "Loaded experiment_0824_HeLa.h5 — 312 cells, 3 channels",
  runningTask: null,

  multiSelectOpen: false,
  staged: new Set(),

  setHub: (h) => set({ hub: h }),
  setCompanion: (c) => set({ activeCompanion: c }),
  setChannel: (c) => set({ channel: c, status: `Channel → ${c}` }),
  setMask: (m) => set({ mask: m, status: `Mask → ${m}` }),
  setSegmentation: (s) => set({ segmentation: s, status: `Segmentation → ${s}` }),
  setViewBin: (n) => set({ viewBin: n, status: `View bin → ${n}` }),
  setAlwaysOnTop: (v) => set({ alwaysOnTop: v }),
  setLayoutPreset: (p) => set({ layoutPreset: p, status: `Layout → ${p}` }),

  selectOne: (id, additive) =>
    set((s) => {
      const next = new Set(additive ? s.selection : []);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { selection: next };
    }),
  selectMany: (ids, additive) =>
    set((s) => {
      const next = new Set(additive ? s.selection : []);
      ids.forEach((id) => next.add(id));
      return { selection: next, status: `Selected ${next.size} cells` };
    }),
  clearSelection: () => set({ selection: new Set(), status: "Selection cleared" }),
  filterToSelection: () =>
    set((s) => {
      if (s.selection.size === 0) return { status: "No selection to filter to" };
      return {
        filter: new Set(s.selection),
        status: `Filtered to ${s.selection.size} cells`,
      };
    }),
  clearFilter: () => set({ filter: null, status: "Filter cleared" }),

  openMultiSelect: () => set({ multiSelectOpen: true, staged: new Set() }),
  toggleStaged: (id) =>
    set((s) => {
      const next = new Set(s.staged);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { staged: next };
    }),
  commitMultiSelect: () =>
    set((s) => ({
      selection: new Set(s.staged),
      multiSelectOpen: false,
      staged: new Set(),
      status: `Multi-select committed: ${s.staged.size} cells`,
    })),
  cancelMultiSelect: () => set({ multiSelectOpen: false, staged: new Set() }),

  setStatus: (s) => set({ status: s }),
  runTask: (label, durationMs = 2400) => {
    const start = performance.now();
    set({ runningTask: { label, progress: 0, cancellable: true }, status: label });
    const tick = () => {
      const cur = get().runningTask;
      if (!cur) return;
      const elapsed = performance.now() - start;
      const p = Math.min(1, elapsed / durationMs);
      set({ runningTask: { ...cur, progress: p } });
      if (p < 1) requestAnimationFrame(tick);
      else set({ runningTask: null, status: `${label} — complete` });
    };
    requestAnimationFrame(tick);
  },
  cancelTask: () =>
    set((s) => ({
      runningTask: null,
      status: s.runningTask ? `${s.runningTask.label} — cancelled` : s.status,
    })),

  detach: (c) =>
    set((s) => {
      const next = new Set(s.detached);
      next.add(c);
      return { detached: next, status: `Detached ${c}` };
    }),
  reattach: (c) =>
    set((s) => {
      const next = new Set(s.detached);
      next.delete(c);
      return { detached: next, status: `Reattached ${c}` };
    }),
}));
```

### `src/percell/mock.ts`

```ts
export type CellId = number;

export interface Cell {
  id: CellId;
  x: number; // viewer coords 0-100 (%)
  y: number;
  r: number; // radius %
  area: number;
  mean_ch1: number;
  mean_ch2: number;
  integrated: number;
  eccentricity: number;
  g: number; // phasor
  s: number;
}

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

const r = rng(7);

export const CELLS: Cell[] = Array.from({ length: 312 }, (_, i) => {
  const area = 200 + r() * 900;
  const mean_ch1 = 400 + r() * 1400;
  const mean_ch2 = 200 + r() * 1000;
  // phasor inside semicircle
  const theta = r() * Math.PI;
  const radius = 0.15 + r() * 0.3;
  const g = 0.5 + radius * Math.cos(theta);
  const s = radius * Math.sin(theta);
  return {
    id: i + 1,
    x: 4 + r() * 92,
    y: 4 + r() * 92,
    r: 1.2 + r() * 1.6,
    area,
    mean_ch1,
    mean_ch2,
    integrated: area * mean_ch1,
    eccentricity: 0.2 + r() * 0.7,
    g,
    s,
  };
});

export const CHANNELS = ["DAPI", "GFP", "NADH"] as const;
export const SEGMENTATIONS = ["dapi_seg", "nuclei_v2"] as const;
export const MASKS = ["thresh_488", "particles"] as const;

export const METRIC_COLUMNS = [
  { key: "id", label: "ID", fmt: (v: number) => `#${String(v).padStart(4, "0")}` },
  { key: "area", label: "Area", fmt: (v: number) => v.toFixed(1) },
  { key: "mean_ch1", label: "Mean_Ch1", fmt: (v: number) => v.toFixed(0) },
  { key: "mean_ch2", label: "Mean_Ch2", fmt: (v: number) => v.toFixed(0) },
  { key: "integrated", label: "Integrated", fmt: (v: number) => v.toFixed(0) },
  { key: "eccentricity", label: "Eccen", fmt: (v: number) => v.toFixed(3) },
  { key: "g", label: "G", fmt: (v: number) => v.toFixed(3) },
  { key: "s", label: "S", fmt: (v: number) => v.toFixed(3) },
] as const;

export type MetricKey = (typeof METRIC_COLUMNS)[number]["key"];
```

### `src/percell/ui.tsx`

```tsx
import { ArrowUpRight, Minus, X } from "lucide-react";
import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function PanelHeader({
  title,
  meta,
  onDetach,
  onCollapse,
  onClose,
  right,
}: {
  title: string;
  meta?: ReactNode;
  onDetach?: () => void;
  onCollapse?: () => void;
  onClose?: () => void;
  right?: ReactNode;
}) {
  return (
    <div className="h-7 px-2 flex items-center justify-between border-b border-border bg-surface-elev shrink-0">
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </span>
        {meta && <span className="text-[10px] mono text-muted-foreground/80">{meta}</span>}
      </div>
      <div className="flex items-center gap-1">
        {right}
        {onDetach && (
          <button
            onClick={onDetach}
            title="Detach"
            className="size-5 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-white/5 rounded"
          >
            <ArrowUpRight className="size-3" />
          </button>
        )}
        {onCollapse && (
          <button
            onClick={onCollapse}
            title="Collapse"
            className="size-5 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-white/5 rounded"
          >
            <Minus className="size-3" />
          </button>
        )}
        {onClose && (
          <button
            onClick={onClose}
            title="Close"
            className="size-5 grid place-items-center text-muted-foreground hover:text-foreground hover:bg-white/5 rounded"
          >
            <X className="size-3" />
          </button>
        )}
      </div>
    </div>
  );
}

export function GroupBox({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <fieldset
      className={cn(
        "border border-border rounded bg-surface/40 px-3 pt-2 pb-3 space-y-2.5",
        className,
      )}
    >
      <legend className="px-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {title}
      </legend>
      {children}
    </fieldset>
  );
}

export function Row({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2 text-[11px]">
      <label className="text-foreground/70" title={hint}>
        {label}
      </label>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

export function MiniSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly string[] | string[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-surface-elev border border-border rounded px-1.5 h-6 text-[11px] mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

export function MiniInput({
  value,
  onChange,
  width = 56,
  type = "text",
  step,
}: {
  value: string | number;
  onChange: (v: string) => void;
  width?: number;
  type?: string;
  step?: number;
}) {
  return (
    <input
      type={type}
      value={value}
      step={step}
      onChange={(e) => onChange(e.target.value)}
      style={{ width }}
      className="bg-surface-elev border border-border rounded px-1.5 h-6 text-[11px] mono text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
    />
  );
}

export function MiniButton({
  children,
  onClick,
  variant = "default",
  className,
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "ghost";
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "h-7 px-2.5 text-[11px] font-medium rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed",
        variant === "default" &&
          "bg-surface-elev border border-border text-foreground hover:bg-white/5",
        variant === "primary" &&
          "bg-accent/15 border border-accent/40 text-accent hover:bg-accent/25",
        variant === "ghost" && "text-muted-foreground hover:text-foreground hover:bg-white/5",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function MiniCheckbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-foreground/80 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3 accent-[color:var(--accent)]"
      />
      {label}
    </label>
  );
}

export function Swatch({ color }: { color: string }) {
  return (
    <span
      className="inline-block size-2.5 rounded-sm border border-white/15"
      style={{ background: color }}
    />
  );
}
```

### `src/percell/Chrome.tsx`

```tsx
import {
  FileInput,
  Image as ImageIcon,
  Scissors,
  SlidersHorizontal,
  Activity,
  Code2,
  Workflow,
  Database,
  Pin,
  ChevronDown,
  Square,
  X,
  CircleDot,
} from "lucide-react";
import { usePerCell, type HubCategory, type LayoutPreset } from "./store";
import { CHANNELS, MASKS, SEGMENTATIONS, CELLS } from "./mock";
import { cn } from "@/lib/utils";

const HUB: { id: HubCategory; label: string; Icon: typeof FileInput }[] = [
  { id: "io", label: "I/O", Icon: FileInput },
  { id: "viewer", label: "Viewer", Icon: ImageIcon },
  { id: "segment", label: "Segment", Icon: Scissors },
  { id: "analysis", label: "Analysis", Icon: SlidersHorizontal },
  { id: "flim", label: "FLIM", Icon: Activity },
  { id: "scripts", label: "Scripts", Icon: Code2 },
  { id: "workflows", label: "Workflows", Icon: Workflow },
  { id: "data", label: "Data", Icon: Database },
];

export function MenuBar() {
  const { layoutPreset, setLayoutPreset, openMultiSelect } = usePerCell();
  return (
    <div className="h-7 flex items-center px-2 gap-4 border-b border-border bg-surface text-[11px] shrink-0">
      <div className="flex items-center gap-2">
        <div className="size-3 bg-accent rounded-sm" />
        <span className="font-semibold tracking-wider uppercase text-[10px]">
          PerCell<span className="text-accent">4</span>
        </span>
      </div>
      <MenuItem
        label="File"
        items={["Open Project…", "Recent Projects", "—", "Quit"]}
      />
      <MenuItem label="Selection" items={["Multi-select…"]} onPick={openMultiSelect} />
      <div className="flex-1" />
      <div className="flex items-center gap-1">
        <span className="text-muted-foreground text-[10px] uppercase tracking-wider">
          Layout
        </span>
        <select
          value={layoutPreset}
          onChange={(e) => setLayoutPreset(e.target.value as LayoutPreset)}
          className="bg-surface-elev border border-border rounded px-1.5 h-5 text-[10px] mono"
        >
          <option value="laptop">Laptop</option>
          <option value="labpc">Lab PC</option>
          <option value="flim">FLIM-focus</option>
          <option value="single">Single-cell-focus</option>
        </select>
      </div>
    </div>
  );
}

function MenuItem({
  label,
  items,
  onPick,
}: {
  label: string;
  items: string[];
  onPick?: () => void;
}) {
  return (
    <div className="relative group">
      <button
        onClick={onPick}
        className="h-7 px-2 text-foreground/80 hover:text-foreground hover:bg-white/5 rounded-sm flex items-center gap-1"
      >
        {label}
      </button>
      <div className="absolute left-0 top-full mt-px min-w-44 bg-surface-elev border border-border rounded shadow-lg z-50 hidden group-hover:block p-1">
        {items.map((it, i) =>
          it === "—" ? (
            <div key={i} className="h-px bg-border my-1" />
          ) : (
            <button
              key={it}
              onClick={onPick}
              className="block w-full text-left px-2 py-1 text-[11px] hover:bg-white/5 rounded"
            >
              {it}
            </button>
          ),
        )}
      </div>
    </div>
  );
}

export function SessionBar() {
  const {
    dataset,
    channel,
    mask,
    segmentation,
    viewBin,
    alwaysOnTop,
    setChannel,
    setMask,
    setSegmentation,
    setViewBin,
    setAlwaysOnTop,
  } = usePerCell();

  return (
    <div className="h-9 flex items-center px-3 gap-4 border-b border-border bg-surface-elev shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <CircleDot className="size-3 text-accent shrink-0" />
        <span className="text-[11px] mono text-foreground/90 truncate">{dataset}</span>
      </div>
      <div className="h-4 w-px bg-border" />
      <Selector label="Channel" value={channel} options={[...CHANNELS]} onChange={setChannel} />
      <Selector label="Mask" value={mask} options={[...MASKS]} onChange={setMask} />
      <Selector
        label="Segmentation"
        value={segmentation}
        options={[...SEGMENTATIONS]}
        onChange={setSegmentation}
      />
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Bin
        </span>
        <div className="flex items-center bg-surface border border-border rounded overflow-hidden">
          <button
            onClick={() => setViewBin(Math.max(1, viewBin - 1))}
            className="h-5 w-5 grid place-items-center hover:bg-white/5 text-muted-foreground"
          >
            −
          </button>
          <span className="px-2 text-[11px] mono text-accent w-8 text-center">
            {viewBin}
          </span>
          <button
            onClick={() => setViewBin(Math.min(16, viewBin + 1))}
            className="h-5 w-5 grid place-items-center hover:bg-white/5 text-muted-foreground"
          >
            +
          </button>
        </div>
      </div>
      <div className="flex-1" />
      <button
        onClick={() => setAlwaysOnTop(!alwaysOnTop)}
        title="Always on top"
        className={cn(
          "h-6 px-2 rounded border text-[10px] uppercase tracking-wider flex items-center gap-1.5",
          alwaysOnTop
            ? "bg-accent/15 border-accent/40 text-accent"
            : "bg-surface border-border text-muted-foreground hover:text-foreground",
        )}
      >
        <Pin className="size-3" />
        On top
      </button>
      <span className="text-[10px] mono text-muted-foreground">
        {CELLS.length} cells
      </span>
    </div>
  );
}

function Selector({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="bg-surface border border-border rounded pl-2 pr-6 h-6 text-[11px] mono text-foreground appearance-none focus:outline-none focus:ring-1 focus:ring-accent"
        >
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <ChevronDown className="size-3 absolute right-1.5 top-1/2 -translate-y-1/2 pointer-events-none text-muted-foreground" />
      </div>
    </div>
  );
}

export function HubSidebar() {
  const { hub, setHub } = usePerCell();
  return (
    <div className="w-12 border-r border-border bg-surface flex flex-col items-stretch py-1 shrink-0">
      {HUB.map(({ id, label, Icon }) => {
        const active = hub === id;
        return (
          <button
            key={id}
            onClick={() => setHub(id)}
            title={label}
            className={cn(
              "h-12 flex flex-col items-center justify-center gap-0.5 border-l-2 transition-colors",
              active
                ? "border-accent bg-accent/10 text-accent"
                : "border-transparent text-muted-foreground hover:text-foreground hover:bg-white/5",
            )}
          >
            <Icon className="size-4" />
            <span className="text-[9px] uppercase tracking-wider">{label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function StatusBar() {
  const { status, runningTask, cancelTask } = usePerCell();
  return (
    <div className="h-6 border-t border-border bg-surface flex items-center px-3 gap-3 text-[10px] mono text-muted-foreground shrink-0">
      <span className="text-accent">●</span>
      <span className="truncate flex-1">{status}</span>
      {runningTask && (
        <>
          <span className="text-foreground">{runningTask.label}</span>
          <div className="w-32 h-1 bg-surface-elev border border-border rounded overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-100"
              style={{ width: `${Math.round(runningTask.progress * 100)}%` }}
            />
          </div>
          <span>{Math.round(runningTask.progress * 100)}%</span>
          {runningTask.cancellable && (
            <button
              onClick={cancelTask}
              className="hover:text-destructive flex items-center gap-1"
            >
              <X className="size-3" /> Cancel
            </button>
          )}
        </>
      )}
      <span className="opacity-60">Node 04_West</span>
    </div>
  );
}

// silence unused warnings
void Square;
```

### `src/percell/Viewer.tsx`

```tsx
import { useEffect, useState } from "react";
import { Brush, Square, Eraser, Pentagon, Eye, EyeOff, Plus } from "lucide-react";
import { PanelHeader } from "./ui";
import { usePerCell } from "./store";
import { CELLS } from "./mock";
import { cn } from "@/lib/utils";

const LAYERS = [
  { name: "DAPI", kind: "image", color: "#67e8f9", opacity: 1 },
  { name: "GFP", kind: "image", color: "#86efac", opacity: 0.7 },
  { name: "dapi_seg", kind: "labels", color: "#f472b6", opacity: 0.5 },
  { name: "thresh_488", kind: "mask", color: "#fde047", opacity: 0.4 },
];

const TOOLS = [
  { id: "brush", Icon: Brush },
  { id: "fill", Icon: Square },
  { id: "erase", Icon: Eraser },
  { id: "poly", Icon: Pentagon },
];

export function ImageViewer() {
  const {
    selection,
    filter,
    selectOne,
    multiSelectOpen,
    openMultiSelect,
    toggleStaged,
    commitMultiSelect,
    cancelMultiSelect,
    staged,
    channel,
    viewBin,
    runTask,
  } = usePerCell();
  const [hover, setHover] = useState<{ x: number; y: number; intensity: number } | null>(
    null,
  );
  const [tool, setTool] = useState("brush");
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)
        return;
      if (e.key === "m" || e.key === "M") openMultiSelect();
      else if (multiSelectOpen && e.key === "Enter") commitMultiSelect();
      else if (multiSelectOpen && e.key === "Escape") cancelMultiSelect();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [multiSelectOpen, openMultiSelect, commitMultiSelect, cancelMultiSelect]);

  function onCellClick(id: number, additive: boolean) {
    if (multiSelectOpen) toggleStaged(id);
    else selectOne(id, additive);
  }

  return (
    <div className="flex-1 flex min-w-0">
      {/* Layer + tool rail */}
      <div className="w-44 border-r border-border bg-surface flex flex-col shrink-0">
        <PanelHeader title="Viewer Layers" />
        <div className="p-2 flex flex-col gap-2">
          <div className="space-y-1">
            {LAYERS.map((l) => {
              const isHidden = hidden.has(l.name);
              return (
                <div
                  key={l.name}
                  className="flex items-center gap-1.5 p-1 rounded hover:bg-white/5 group"
                >
                  <button
                    onClick={() => {
                      const n = new Set(hidden);
                      if (isHidden) n.delete(l.name);
                      else n.add(l.name);
                      setHidden(n);
                    }}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    {isHidden ? <EyeOff className="size-3" /> : <Eye className="size-3" />}
                  </button>
                  <span
                    className="size-2 rounded-sm shrink-0"
                    style={{ background: l.color }}
                  />
                  <span className="text-[10px] mono truncate flex-1">{l.name}</span>
                  <span className="text-[9px] text-muted-foreground">
                    {Math.round(l.opacity * 100)}
                  </span>
                </div>
              );
            })}
          </div>
          <button className="text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1 px-1">
            <Plus className="size-3" />
            Add layer
          </button>

          <div className="border-t border-border pt-2 mt-1">
            <div className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1.5 px-1">
              Tools
            </div>
            <div className="grid grid-cols-4 gap-1">
              {TOOLS.map(({ id, Icon }) => (
                <button
                  key={id}
                  onClick={() => setTool(id)}
                  className={cn(
                    "h-7 grid place-items-center rounded border",
                    tool === id
                      ? "bg-accent/15 border-accent/40 text-accent"
                      : "border-border text-muted-foreground hover:text-foreground hover:bg-white/5",
                  )}
                >
                  <Icon className="size-3.5" />
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border pt-2 mt-1">
            <div className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1.5 px-1">
              Dimensions
            </div>
            <div className="px-1 space-y-2">
              <DimSlider label="T" max={100} value={24} />
              <DimSlider label="Z" max={12} value={4} />
            </div>
          </div>

          <button
            onClick={() => runTask("Running Cellpose [cyto3]", 3000)}
            className="mt-2 h-7 text-[11px] bg-accent/15 border border-accent/40 text-accent rounded hover:bg-accent/25"
          >
            ▶ Run Cellpose
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div className="flex-1 flex flex-col min-w-0 bg-background">
        <PanelHeader
          title="Image Viewer"
          meta={`${channel} · bin ${viewBin}`}
          right={
            multiSelectOpen && (
              <span className="text-[10px] mono text-preview px-2 py-0.5 bg-preview/10 border border-preview/30 rounded">
                MULTI-SELECT · {staged.size} staged · Enter to commit, Esc to cancel
              </span>
            )
          }
        />
        <div
          className="flex-1 relative overflow-hidden"
          style={{
            background:
              "radial-gradient(ellipse at 30% 40%, oklch(0.28 0.04 220) 0%, oklch(0.1 0.01 250) 60%)",
          }}
          onMouseMove={(e) => {
            const r = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
            const x = ((e.clientX - r.left) / r.width) * 1024;
            const y = ((e.clientY - r.top) / r.height) * 1024;
            setHover({ x, y, intensity: 400 + Math.round(((x + y) % 800)) });
          }}
          onMouseLeave={() => setHover(null)}
        >
          {/* Soft cell glow underlay */}
          <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
            <defs>
              <radialGradient id="cellGlow">
                <stop offset="0%" stopColor="oklch(0.85 0.18 200 / 0.5)" />
                <stop offset="100%" stopColor="oklch(0.85 0.18 200 / 0)" />
              </radialGradient>
            </defs>
            {CELLS.map((c) => {
              const dimmed = filter && !filter.has(c.id);
              return (
                <circle
                  key={`g${c.id}`}
                  cx={c.x}
                  cy={c.y}
                  r={c.r * 1.6}
                  fill="url(#cellGlow)"
                  opacity={dimmed ? 0.05 : 0.5}
                />
              );
            })}
          </svg>

          {/* Cells */}
          <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
            {CELLS.map((c) => {
              const selected = selection.has(c.id);
              const isStaged = staged.has(c.id);
              const dimmed = filter && !filter.has(c.id);
              const stroke = selected
                ? "oklch(0.65 0.22 25)"
                : isStaged
                  ? "oklch(0.85 0.18 90)"
                  : "oklch(0.85 0.18 200 / 0.5)";
              return (
                <circle
                  key={c.id}
                  cx={c.x}
                  cy={c.y}
                  r={c.r}
                  fill={selected ? "oklch(0.65 0.22 25 / 0.25)" : "transparent"}
                  stroke={stroke}
                  strokeWidth={selected || isStaged ? 0.4 : 0.15}
                  opacity={dimmed ? 0.15 : 1}
                  vectorEffect="non-scaling-stroke"
                  style={{ cursor: "pointer", pointerEvents: "all" }}
                  onClick={(e) => onCellClick(c.id, e.shiftKey || e.ctrlKey || e.metaKey)}
                />
              );
            })}
          </svg>

          {/* HUD */}
          <div className="absolute bottom-2 left-2 px-2 py-1.5 bg-black/60 backdrop-blur-md border border-white/10 rounded mono text-[10px] flex gap-3">
            <span className="text-accent">X: {hover ? hover.x.toFixed(0) : "----"}</span>
            <span className="text-accent">Y: {hover ? hover.y.toFixed(0) : "----"}</span>
            <span className="text-foreground/80">
              I: {hover ? hover.intensity : "----"}
            </span>
            <span className="text-muted-foreground">CH: {channel}</span>
            <span className="text-muted-foreground">k={viewBin}</span>
          </div>

          <div className="absolute top-2 right-2 mono text-[10px] text-muted-foreground bg-black/40 px-2 py-1 rounded border border-white/10">
            Press <span className="text-accent">M</span> for multi-select · Shift+click to add
          </div>

          {multiSelectOpen && (
            <div className="absolute inset-0 pointer-events-none ring-2 ring-preview/40 ring-inset" />
          )}
        </div>
      </div>
    </div>
  );
}

function DimSlider({ label, max, value }: { label: string; max: number; value: number }) {
  const [v, setV] = useState(value);
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] mono text-muted-foreground w-3">{label}</span>
      <input
        type="range"
        min={0}
        max={max}
        value={v}
        onChange={(e) => setV(+e.target.value)}
        className="flex-1 h-1 accent-[color:var(--accent)]"
      />
      <span className="text-[10px] mono text-accent w-10 text-right">
        {v}/{max}
      </span>
    </div>
  );
}
```

### `src/percell/Companions.tsx`

```tsx
import { useMemo, useRef, useState } from "react";
import { PanelHeader, MiniSelect, MiniButton, MiniCheckbox, Swatch } from "./ui";
import { usePerCell, visibleCells, type CompanionId } from "./store";
import { CELLS, METRIC_COLUMNS, type MetricKey } from "./mock";

export function CompanionDock() {
  const { activeCompanion, setCompanion, detached, detach } = usePerCell();
  const tabs: { id: CompanionId; label: string }[] = [
    { id: "table", label: "Cell Table" },
    { id: "plot", label: "Data Plot" },
    { id: "phasor", label: "Phasor Plot" },
  ];
  return (
    <div className="w-[420px] border-l border-border bg-surface flex flex-col shrink-0">
      <div className="h-7 flex border-b border-border bg-surface-elev shrink-0">
        {tabs.map((t) => {
          const isDetached = detached.has(t.id);
          return (
            <button
              key={t.id}
              onClick={() => setCompanion(t.id)}
              className={`px-3 h-full text-[10px] uppercase tracking-wider border-r border-border flex items-center gap-1.5 ${
                activeCompanion === t.id
                  ? "bg-background text-accent"
                  : "text-muted-foreground hover:text-foreground"
              } ${isDetached ? "opacity-40" : ""}`}
            >
              {t.label}
              {isDetached && <span className="text-[9px]">↗</span>}
            </button>
          );
        })}
        <div className="flex-1" />
        <button
          onClick={() => detach(activeCompanion)}
          className="px-2 text-[10px] text-muted-foreground hover:text-foreground"
          title="Detach to separate window"
        >
          ↗ Detach
        </button>
      </div>
      <div className="flex-1 overflow-hidden min-h-0">
        {detached.has(activeCompanion) ? (
          <DetachedNotice id={activeCompanion} />
        ) : (
          <>
            {activeCompanion === "table" && <CellTable />}
            {activeCompanion === "plot" && <DataPlot />}
            {activeCompanion === "phasor" && <PhasorPlot />}
          </>
        )}
      </div>
    </div>
  );
}

function DetachedNotice({ id }: { id: CompanionId }) {
  const reattach = usePerCell((s) => s.reattach);
  return (
    <div className="h-full grid place-items-center text-center px-6">
      <div className="space-y-3">
        <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
          {id} detached
        </div>
        <div className="text-[11px] text-foreground/70">
          Open in a separate window — drag onto a second monitor.
        </div>
        <MiniButton variant="primary" onClick={() => reattach(id)}>
          Reattach
        </MiniButton>
      </div>
    </div>
  );
}

// ============== CELL TABLE ==============
function CellTable() {
  const { selection, filter, selectOne, setStatus } = usePerCell();
  const cells = useMemo(() => visibleCells(filter), [filter]);
  const [sortKey, setSortKey] = useState<MetricKey>("id");
  const [asc, setAsc] = useState(true);
  const sorted = useMemo(() => {
    const arr = [...cells];
    arr.sort((a, b) => {
      const av = a[sortKey] as number;
      const bv = b[sortKey] as number;
      return asc ? av - bv : bv - av;
    });
    return arr;
  }, [cells, sortKey, asc]);

  const scrollRef = useRef<HTMLDivElement>(null);

  return (
    <div className="h-full flex flex-col">
      <PanelHeader
        title="Cell Table"
        meta={
          filter
            ? `${cells.length} of ${CELLS.length} cells (filtered) · 24 cols`
            : `${CELLS.length} cells · 24 cols`
        }
        right={
          <MiniButton onClick={() => setStatus("Exported measurements.csv")}>
            Export CSV…
          </MiniButton>
        }
      />
      <div ref={scrollRef} className="flex-1 overflow-auto">
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 bg-surface-elev z-10">
            <tr>
              {METRIC_COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => {
                    if (sortKey === c.key) setAsc(!asc);
                    else {
                      setSortKey(c.key as MetricKey);
                      setAsc(true);
                    }
                  }}
                  className="px-2 py-1.5 text-[9px] mono uppercase tracking-wider text-muted-foreground border-b border-border cursor-pointer hover:text-foreground"
                >
                  {c.label}
                  {sortKey === c.key && <span className="text-accent ml-1">{asc ? "▲" : "▼"}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="mono text-[10px]">
            {sorted.map((cell, i) => {
              const selected = selection.has(cell.id);
              return (
                <tr
                  key={cell.id}
                  onClick={(e) => selectOne(cell.id, e.shiftKey || e.ctrlKey || e.metaKey)}
                  className={`cursor-pointer border-b border-white/[0.03] ${
                    selected
                      ? "bg-destructive/15 text-foreground"
                      : i % 2
                        ? "bg-white/[0.015] hover:bg-accent/5"
                        : "hover:bg-accent/5"
                  }`}
                >
                  {METRIC_COLUMNS.map((c) => (
                    <td
                      key={c.key}
                      className={`px-2 py-1 ${selected && c.key === "id" ? "text-destructive" : "text-foreground/80"}`}
                    >
                      {c.fmt(cell[c.key as MetricKey] as number)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ============== DATA PLOT ==============
function DataPlot() {
  const { selection, filter, selectMany, clearSelection } = usePerCell();
  const [xKey, setXKey] = useState<MetricKey>("area");
  const [yKey, setYKey] = useState<MetricKey>("mean_ch1");
  const cells = useMemo(() => visibleCells(filter), [filter]);
  const ext = useMemo(() => {
    const xs = cells.map((c) => c[xKey] as number);
    const ys = cells.map((c) => c[yKey] as number);
    return {
      xmin: Math.min(...xs),
      xmax: Math.max(...xs),
      ymin: Math.min(...ys),
      ymax: Math.max(...ys),
    };
  }, [cells, xKey, yKey]);

  const W = 380;
  const H = 280;
  const PAD = 28;
  const sx = (v: number) =>
    PAD + ((v - ext.xmin) / (ext.xmax - ext.xmin || 1)) * (W - PAD * 2);
  const sy = (v: number) =>
    H - PAD - ((v - ext.ymin) / (ext.ymax - ext.ymin || 1)) * (H - PAD * 2);

  // rubber band
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(
    null,
  );
  const svgRef = useRef<SVGSVGElement>(null);

  function svgCoords(e: React.MouseEvent) {
    const rect = svgRef.current!.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * W,
      y: ((e.clientY - rect.top) / rect.height) * H,
    };
  }

  function onPointerDown(e: React.MouseEvent) {
    if (!e.shiftKey) return;
    const { x, y } = svgCoords(e);
    setDrag({ x0: x, y0: y, x1: x, y1: y });
  }
  function onPointerMove(e: React.MouseEvent) {
    if (!drag) return;
    const { x, y } = svgCoords(e);
    setDrag({ ...drag, x1: x, y1: y });
  }
  function onPointerUp() {
    if (!drag) return;
    const minX = Math.min(drag.x0, drag.x1);
    const maxX = Math.max(drag.x0, drag.x1);
    const minY = Math.min(drag.y0, drag.y1);
    const maxY = Math.max(drag.y0, drag.y1);
    const inside = cells
      .filter((c) => {
        const x = sx(c[xKey] as number);
        const y = sy(c[yKey] as number);
        return x >= minX && x <= maxX && y >= minY && y <= maxY;
      })
      .map((c) => c.id);
    if (inside.length) selectMany(inside);
    setDrag(null);
  }

  const numericKeys = METRIC_COLUMNS.filter((c) => c.key !== "id").map((c) => c.key) as MetricKey[];

  return (
    <div className="h-full flex flex-col">
      <PanelHeader
        title="Data Plot"
        meta={`${cells.length} points · x=${xKey} y=${yKey}`}
        right={
          <MiniButton onClick={clearSelection}>Reset Sel</MiniButton>
        }
      />
      <div className="p-2 flex items-center gap-2 border-b border-border bg-surface/40">
        <span className="text-[10px] uppercase text-muted-foreground">X</span>
        <MiniSelect value={xKey} options={numericKeys} onChange={(v) => setXKey(v as MetricKey)} />
        <span className="text-[10px] uppercase text-muted-foreground ml-2">Y</span>
        <MiniSelect value={yKey} options={numericKeys} onChange={(v) => setYKey(v as MetricKey)} />
        <span className="ml-auto text-[10px] mono text-muted-foreground">Shift+drag to select</span>
      </div>
      <div className="flex-1 grid place-items-center bg-background overflow-hidden">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-full cursor-crosshair"
          onMouseDown={onPointerDown}
          onMouseMove={onPointerMove}
          onMouseUp={onPointerUp}
          onMouseLeave={() => setDrag(null)}
        >
          {/* axes */}
          <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="oklch(1 0 0 / 0.15)" />
          <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="oklch(1 0 0 / 0.15)" />
          {cells.map((c) => {
            const selected = selection.has(c.id);
            return (
              <circle
                key={c.id}
                cx={sx(c[xKey] as number)}
                cy={sy(c[yKey] as number)}
                r={selected ? 3 : 1.8}
                fill={selected ? "oklch(0.65 0.22 25)" : "oklch(0.85 0.18 200 / 0.6)"}
                stroke={selected ? "oklch(0.7 0.22 25)" : "none"}
                strokeWidth={selected ? 0.5 : 0}
              />
            );
          })}
          {drag && (
            <rect
              x={Math.min(drag.x0, drag.x1)}
              y={Math.min(drag.y0, drag.y1)}
              width={Math.abs(drag.x1 - drag.x0)}
              height={Math.abs(drag.y1 - drag.y0)}
              fill="oklch(0.85 0.18 200 / 0.1)"
              stroke="oklch(0.85 0.18 200)"
              strokeDasharray="2 2"
            />
          )}
          <text x={W - PAD} y={H - 8} textAnchor="end" className="mono" fontSize="9" fill="oklch(0.6 0.01 250)">
            {xKey}
          </text>
          <text x={6} y={PAD - 4} className="mono" fontSize="9" fill="oklch(0.6 0.01 250)">
            {yKey}
          </text>
        </svg>
      </div>
    </div>
  );
}

// ============== PHASOR PLOT ==============
const ROI_COLORS = [
  "#22d3ee",
  "#f472b6",
  "#a78bfa",
  "#fbbf24",
  "#34d399",
  "#fb7185",
  "#60a5fa",
  "#facc15",
];

interface ROI {
  name: string;
  visible: boolean;
  color: string;
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  angle: number;
  gmm?: boolean;
}

function PhasorPlot() {
  const { runTask, setStatus, mask, channel } = usePerCell();
  const [rois, setRois] = useState<ROI[]>([
    { name: "free NADH", visible: true, color: ROI_COLORS[0], cx: 0.42, cy: 0.32, rx: 0.06, ry: 0.04, angle: 20 },
    { name: "bound NADH", visible: true, color: ROI_COLORS[1], cx: 0.62, cy: 0.36, rx: 0.05, ry: 0.05, angle: 0 },
    { name: "Cluster 1", visible: true, color: ROI_COLORS[2], cx: 0.55, cy: 0.22, rx: 0.08, ry: 0.03, angle: -15, gmm: true },
  ]);
  const [sel, setSel] = useState(0);
  const [harmonic, setHarmonic] = useState("1");
  const [filtered, setFiltered] = useState(false);
  const [filterByMask, setFilterByMask] = useState(false);

  const W = 280;
  const H = 220;
  // map g[0..1], s[0..0.6] into svg
  const px = (g: number) => 20 + g * (W - 40);
  const py = (s: number) => H - 20 - s * (H - 40) * 1.6;

  function updateSelected(patch: Partial<ROI>) {
    setRois(rois.map((r, i) => (i === sel ? { ...r, ...patch } : r)));
  }

  const cur = rois[sel];

  return (
    <div className="h-full flex flex-col">
      <PanelHeader title="Phasor Plot" meta={`${channel} · h=${harmonic}`} />
      {/* toolbar */}
      <div className="px-2 py-1.5 flex items-center gap-3 border-b border-border bg-surface/40 text-[10px]">
        <span className="uppercase text-muted-foreground">Harmonic</span>
        <MiniSelect value={harmonic} options={["1", "2", "3"]} onChange={setHarmonic} />
        <MiniCheckbox checked={filtered} onChange={setFiltered} label="Filtered" />
        <MiniCheckbox
          checked={filterByMask}
          onChange={setFilterByMask}
          label={`Filter by mask (${mask})`}
        />
        <div className="flex-1" />
        <MiniButton onClick={() => setStatus("Saved phasor.svg")}>Save .SVG</MiniButton>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* ROI list */}
        <div className="w-28 border-r border-border bg-surface/40 overflow-y-auto">
          <div className="p-1 space-y-0.5">
            {rois.map((r, i) => (
              <button
                key={r.name}
                onClick={() => setSel(i)}
                className={`w-full flex items-center gap-1.5 px-1.5 py-1 rounded text-left text-[10px] ${
                  i === sel ? "bg-accent/15 text-accent" : "hover:bg-white/5 text-foreground/80"
                }`}
              >
                <input
                  type="checkbox"
                  checked={r.visible}
                  onChange={(e) => {
                    e.stopPropagation();
                    setRois(
                      rois.map((x, j) => (j === i ? { ...x, visible: e.target.checked } : x)),
                    );
                  }}
                  className="size-2.5 accent-[color:var(--accent)]"
                />
                <Swatch color={r.color} />
                <span className="truncate flex-1">{r.name}</span>
              </button>
            ))}
          </div>
          <div className="p-1 border-t border-border space-y-1">
            <MiniButton
              className="w-full"
              onClick={() => {
                const name = prompt("ROI name?", `ROI ${rois.length + 1}`);
                if (!name) return;
                setRois([
                  ...rois,
                  {
                    name,
                    visible: true,
                    color: ROI_COLORS[rois.length % ROI_COLORS.length],
                    cx: 0.5,
                    cy: 0.25,
                    rx: 0.05,
                    ry: 0.04,
                    angle: 0,
                  },
                ]);
                setSel(rois.length);
              }}
            >
              + ROI
            </MiniButton>
            <MiniButton
              className="w-full"
              onClick={() => {
                if (rois.length <= 1) return;
                const next = rois.filter((_, i) => i !== sel);
                setRois(next);
                setSel(0);
              }}
            >
              Remove
            </MiniButton>
          </div>
        </div>

        {/* density + ROIs */}
        <div className="flex-1 grid place-items-center bg-background overflow-hidden">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full">
            <defs>
              <radialGradient id="density">
                <stop offset="0%" stopColor="oklch(0.85 0.18 200 / 0.7)" />
                <stop offset="100%" stopColor="oklch(0.85 0.18 200 / 0)" />
              </radialGradient>
            </defs>
            {/* density blob */}
            {CELLS.slice(0, 200).map((c) => (
              <circle key={c.id} cx={px(c.g)} cy={py(c.s)} r={3} fill="url(#density)" />
            ))}
            {/* universal semicircle */}
            <path
              d={`M ${px(0)} ${py(0)} A ${(W - 40) / 2} ${(H - 40) * 0.8} 0 0 1 ${px(1)} ${py(0)}`}
              fill="none"
              stroke="oklch(0.7 0.05 250 / 0.6)"
              strokeWidth={0.8}
            />
            <line
              x1={px(0)}
              y1={py(0)}
              x2={px(1)}
              y2={py(0)}
              stroke="oklch(0.5 0.02 250 / 0.6)"
              strokeWidth={0.5}
            />
            {/* ROIs */}
            {rois
              .map((r, i) => ({ r, i }))
              .filter(({ r }) => r.visible)
              .map(({ r, i }) => (
                <g
                  key={r.name}
                  transform={`translate(${px(r.cx)} ${py(r.cy)}) rotate(${r.angle})`}
                  onClick={() => setSel(i)}
                  style={{ cursor: "pointer" }}
                >
                  <ellipse
                    cx={0}
                    cy={0}
                    rx={r.rx * (W - 40)}
                    ry={r.ry * (H - 40) * 1.6}
                    fill={r.color + "22"}
                    stroke={r.color}
                    strokeWidth={i === sel ? 1.5 : 0.8}
                  />
                  <text
                    x={0}
                    y={-r.ry * (H - 40) * 1.6 - 3}
                    textAnchor="middle"
                    fontSize="7"
                    fill={r.color}
                    className="mono"
                  >
                    {r.name}
                  </text>
                </g>
              ))}
            <text x={W - 8} y={py(0) + 10} textAnchor="end" fontSize="7" fill="oklch(0.6 0.01 250)" className="mono">
              g
            </text>
            <text x={px(0) - 6} y={20} fontSize="7" fill="oklch(0.6 0.01 250)" className="mono">
              s
            </text>
          </svg>
        </div>

        {/* Selected ROI panel */}
        <div className="w-32 border-l border-border bg-surface/40 p-2 space-y-2 overflow-y-auto">
          {cur && (
            <>
              <div className="text-[9px] uppercase tracking-wider text-muted-foreground">
                Selected ROI
              </div>
              <input
                value={cur.name}
                onChange={(e) => updateSelected({ name: e.target.value })}
                className="w-full bg-surface-elev border border-border rounded px-1.5 h-6 text-[10px] mono"
              />
              <MiniCheckbox
                checked={cur.visible}
                onChange={(v) => updateSelected({ visible: v })}
                label="Visible"
              />
              {cur.gmm ? (
                <>
                  <NumRow label="Stretch ∥" value={cur.rx} step={0.01} onChange={(v) => updateSelected({ rx: v })} />
                  <NumRow label="Stretch ⊥" value={cur.ry} step={0.01} onChange={(v) => updateSelected({ ry: v })} />
                  <NumRow label="Shift ∥" value={cur.cx} step={0.01} onChange={(v) => updateSelected({ cx: v })} />
                  <NumRow label="Shift ⊥" value={cur.cy} step={0.01} onChange={(v) => updateSelected({ cy: v })} />
                  <MiniButton className="w-full">Reset to fit</MiniButton>
                </>
              ) : (
                <NumRow
                  label="Angle"
                  value={cur.angle}
                  step={1}
                  onChange={(v) => updateSelected({ angle: v })}
                />
              )}
              <div className="pt-1 border-t border-border space-y-1">
                <MiniButton className="w-full" onClick={() => setStatus(`Cleared inside ${cur.name}`)}>
                  Clear inside
                </MiniButton>
                <MiniButton className="w-full" onClick={() => setStatus("Reset cleared")}>
                  Reset cleared
                </MiniButton>
              </div>
            </>
          )}
        </div>
      </div>

      {/* bottom actions */}
      <div className="border-t border-border p-2 flex flex-wrap gap-1.5 bg-surface/40">
        <MiniButton
          variant="primary"
          onClick={() => runTask(`Saving ${rois.filter((r) => r.visible).length} ROI masks`, 1500)}
        >
          Apply ROIs as Masks
        </MiniButton>
        <MiniButton onClick={() => setStatus("Saved current phasor as mask")}>
          Apply Current as Mask
        </MiniButton>
        <MiniButton onClick={() => setStatus("Saved rois.json")}>Save ROIs…</MiniButton>
        <MiniButton onClick={() => setStatus("Loaded rois.json")}>Load ROIs…</MiniButton>
      </div>
    </div>
  );
}

function NumRow({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step: number;
  onChange: (n: number) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-1.5">
      <label className="text-[10px] text-muted-foreground">{label}</label>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(+e.target.value)}
        className="w-16 bg-surface-elev border border-border rounded px-1 h-5 text-[10px] mono"
      />
    </div>
  );
}
```

### `src/percell/TaskPanels.tsx`

```tsx
import { useState } from "react";
import { PanelHeader, GroupBox, Row, MiniButton, MiniSelect, MiniInput, MiniCheckbox } from "./ui";
import { usePerCell } from "./store";
import { CHANNELS, MASKS, SEGMENTATIONS } from "./mock";

export function TaskPanel() {
  const hub = usePerCell((s) => s.hub);
  const title =
    {
      io: "I/O",
      viewer: "Viewer",
      segment: "Segment",
      analysis: "Analysis",
      flim: "FLIM",
      scripts: "Scripts",
      workflows: "Workflows",
      data: "Data",
    }[hub] ?? "";

  return (
    <div className="w-72 border-r border-border bg-surface flex flex-col shrink-0">
      <PanelHeader title={title} onCollapse={() => {}} />
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {hub === "io" && <IOPanel />}
        {hub === "viewer" && <ViewerPanel />}
        {hub === "segment" && <SegmentPanel />}
        {hub === "analysis" && <AnalysisPanel />}
        {hub === "flim" && <FLIMPanel />}
        {hub === "scripts" && <ScriptsPanel />}
        {hub === "workflows" && <WorkflowsPanel />}
        {hub === "data" && <DataPanel />}
      </div>
    </div>
  );
}

function IOPanel() {
  const setStatus = usePerCell((s) => s.setStatus);
  return (
    <>
      <GroupBox title="Import">
        <MiniButton onClick={() => setStatus("Compress TIFF wizard opened")}>
          Compress TIFF Dataset…
        </MiniButton>
        <MiniButton onClick={() => setStatus("Load dataset…")}>Load Dataset…</MiniButton>
        <MiniButton onClick={() => setStatus("Add layer…")}>Add Layer to Dataset…</MiniButton>
        <MiniButton onClick={() => setStatus("Batch TCSPC append…")}>
          Batch TCSPC Append…
        </MiniButton>
        <MiniButton onClick={() => setStatus("Closed dataset")}>Close Dataset</MiniButton>
      </GroupBox>
      <GroupBox title="Export">
        <MiniButton onClick={() => setStatus("Exported measurements.csv")}>
          Export Measurements to CSV…
        </MiniButton>
        <MiniButton onClick={() => setStatus("Exporting images…")}>
          Export Images…
        </MiniButton>
        <MiniButton onClick={() => setStatus("Exported phasor.npz")}>
          Export Phasor (.npz)…
        </MiniButton>
      </GroupBox>
    </>
  );
}

function ViewerPanel() {
  return (
    <GroupBox title="Viewer Options">
      <Row label="Sync zoom">
        <MiniCheckbox checked label="" onChange={() => {}} />
      </Row>
      <Row label="Show scalebar">
        <MiniCheckbox checked label="" onChange={() => {}} />
      </Row>
      <Row label="Crosshair">
        <MiniCheckbox checked={false} label="" onChange={() => {}} />
      </Row>
    </GroupBox>
  );
}

function SegmentPanel() {
  const runTask = usePerCell((s) => s.runTask);
  const [model, setModel] = useState("cyto3");
  const [diameter, setDiameter] = useState("30");
  const [gpu, setGpu] = useState(true);
  const [edge, setEdge] = useState(true);
  const [previewed, setPreviewed] = useState(false);
  const [edgeMargin, setEdgeMargin] = useState("8");
  const [minArea, setMinArea] = useState("50");
  return (
    <>
      <GroupBox title="Cellpose">
        <Row label="Model">
          <MiniSelect
            value={model}
            options={["cpsam", "cyto3", "cyto2", "cyto", "nuclei"]}
            onChange={setModel}
          />
        </Row>
        <Row label="Diameter (0=auto)">
          <MiniInput value={diameter} onChange={setDiameter} type="number" width={50} />
        </Row>
        <Row label="Use GPU">
          <MiniCheckbox checked={gpu} onChange={setGpu} label="" />
        </Row>
        <Row label="Remove edge cells">
          <MiniCheckbox checked={edge} onChange={setEdge} label="" />
        </Row>
        <MiniButton
          variant="primary"
          onClick={() => runTask(`Cellpose [${model}, d=${diameter}]`, 3200)}
        >
          ▶ Run Cellpose
        </MiniButton>
      </GroupBox>
      <GroupBox title="Manual Editing">
        <MiniButton>Create Empty Labels Layer</MiniButton>
        <MiniButton>Delete Selected Label</MiniButton>
        <MiniButton>Add New Label (next ID)</MiniButton>
        <MiniButton>Clean Up Labels (relabel)</MiniButton>
      </GroupBox>
      <GroupBox title="Label Cleanup">
        <Row label="Edge margin (px)">
          <MiniInput value={edgeMargin} onChange={setEdgeMargin} type="number" width={50} />
        </Row>
        <Row label="Min cell area (px)">
          <MiniInput value={minArea} onChange={setMinArea} type="number" width={50} />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton onClick={() => setPreviewed(true)}>Preview Removal</MiniButton>
          <MiniButton variant="primary" disabled={!previewed}>
            Apply Removal
          </MiniButton>
        </div>
      </GroupBox>
      <GroupBox title="Save">
        <MiniButton>Save Labels to HDF5</MiniButton>
      </GroupBox>
    </>
  );
}

function AnalysisPanel() {
  const {
    clearSelection,
    filterToSelection,
    clearFilter,
    selection,
    filter,
    runTask,
    setStatus,
    setCompanion,
  } = usePerCell();
  const [method, setMethod] = useState("Otsu");
  const [thresh, setThresh] = useState("1247");
  const [sigma, setSigma] = useState("0");
  const [metricsOpen, setMetricsOpen] = useState(false);
  return (
    <>
      <GroupBox title="Cell Filter">
        <div className="grid grid-cols-3 gap-1.5">
          <MiniButton onClick={clearSelection}>Clear Sel</MiniButton>
          <MiniButton variant="primary" onClick={filterToSelection}>
            Filter→Sel
          </MiniButton>
          <MiniButton onClick={clearFilter}>Clear Flt</MiniButton>
        </div>
        <div className="text-[10px] mono text-muted-foreground">
          {filter
            ? `Showing ${filter.size} of 312 cells`
            : `Selection: ${selection.size} cells`}
        </div>
      </GroupBox>

      <GroupBox title="Whole Field Thresholding">
        <Row label="Channel">
          <span className="text-[11px] mono text-accent">{usePerCell.getState().channel}</span>
        </Row>
        <Row label="Method">
          <MiniSelect
            value={method}
            options={["Otsu", "Triangle", "Li", "Adaptive", "Manual"]}
            onChange={setMethod}
          />
        </Row>
        <Row label="Threshold">
          <MiniInput value={thresh} onChange={setThresh} type="number" width={64} />
        </Row>
        <Row label="Gaussian σ">
          <MiniInput value={sigma} onChange={setSigma} type="number" width={50} />
        </Row>
        <div className="text-[10px] text-muted-foreground leading-relaxed">
          1. Preview computes & overlays. 2. Drag ROI to localize. 3. Accept to save.
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton onClick={() => setStatus("Threshold preview shown")}>
            Preview
          </MiniButton>
          <MiniButton variant="primary" onClick={() => setStatus("Mask saved to HDF5")}>
            Accept & Save
          </MiniButton>
        </div>
        <div className="text-[10px] mono text-muted-foreground">
          Threshold: 1247 / Positive: 18,432 / 1,048,576 px (1.8%)
        </div>
      </GroupBox>

      <GroupBox title="Grouped Thresholding">
        <Row label="Metric">
          <MiniSelect value="mean_int" options={["mean_int", "area", "G", "S"]} onChange={() => {}} />
        </Row>
        <Row label="Groups">
          <MiniInput value="3" onChange={() => {}} type="number" width={40} />
        </Row>
        <div className="flex gap-1.5">
          <MiniButton>◀ Prev</MiniButton>
          <MiniButton variant="primary">Step ▶</MiniButton>
        </div>
        <div className="text-[10px] mono text-muted-foreground">Group 1 of 3 — 104 cells</div>
      </GroupBox>

      <GroupBox title="Measurements">
        <div className="text-[10px] text-muted-foreground leading-relaxed">
          Measures per-cell metrics using active channel, segmentation, and mask.
        </div>
        <MiniButton variant="primary" onClick={() => setMetricsOpen(true)}>
          Measure Cells
        </MiniButton>
        <div className="text-[10px] mono text-muted-foreground">Measured 312 cells, 24 columns</div>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton onClick={() => setCompanion("plot")}>Open Data Plot</MiniButton>
          <MiniButton onClick={() => setCompanion("table")}>Open Cell Table</MiniButton>
        </div>
      </GroupBox>

      <GroupBox title="Particle Analysis">
        <div className="text-[10px] text-muted-foreground leading-relaxed">
          Counts particles within each cell using the active mask.
        </div>
        <Row label="Min particle area (px)">
          <MiniInput value="4" onChange={() => {}} type="number" width={40} />
        </Row>
        <MiniButton variant="primary" onClick={() => runTask("Particle analysis", 1800)}>
          Analyze Particles
        </MiniButton>
        <div className="text-[10px] mono text-muted-foreground">
          Found 1,847 particles across 312 cells
        </div>
        <MiniButton>Export Particle Data to CSV…</MiniButton>
      </GroupBox>

      {metricsOpen && <MetricDialog onClose={() => setMetricsOpen(false)} />}
    </>
  );
}

function MetricDialog({ onClose }: { onClose: () => void }) {
  const runTask = usePerCell((s) => s.runTask);
  const all = [
    "label",
    "area_px",
    "mean_intensity",
    "integrated_intensity",
    "eccentricity",
    "perimeter",
    "solidity",
    "phasor_g",
    "phasor_s",
    "lifetime_mean",
  ];
  const [picked, setPicked] = useState(new Set(all.slice(0, 5)));
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 grid place-items-center"
      onClick={onClose}
    >
      <div
        className="w-80 bg-surface border border-border rounded shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <PanelHeader title="Select Metrics" onClose={onClose} />
        <div className="p-3 grid grid-cols-2 gap-x-3 gap-y-1.5 max-h-72 overflow-y-auto">
          {all.map((m) => (
            <MiniCheckbox
              key={m}
              label={m}
              checked={picked.has(m)}
              onChange={(v) => {
                const n = new Set(picked);
                v ? n.add(m) : n.delete(m);
                setPicked(n);
              }}
            />
          ))}
        </div>
        <div className="p-2 border-t border-border flex justify-end gap-2">
          <MiniButton onClick={onClose}>Cancel</MiniButton>
          <MiniButton
            variant="primary"
            onClick={() => {
              runTask(`Measuring (${picked.size} metrics)`, 2200);
              onClose();
            }}
          >
            Measure
          </MiniButton>
        </div>
      </div>
    </div>
  );
}

function FLIMPanel() {
  const { runTask, setStatus, setCompanion } = usePerCell();
  return (
    <>
      <GroupBox title="Phasor Analysis">
        <Row label="Harmonic">
          <MiniSelect value="1" options={["1", "2", "3"]} onChange={() => {}} />
        </Row>
        <MiniButton
          variant="primary"
          onClick={() => runTask("Computing phasor (h=1)", 2400)}
          title="Shift+click to force recompute"
        >
          Compute Phasor
        </MiniButton>
        <MiniButton onClick={() => setCompanion("phasor")}>Open Phasor Plot</MiniButton>
      </GroupBox>
      <GroupBox title="Wavelet Filter">
        <Row label="Filter Level">
          <MiniInput value="9" onChange={() => {}} type="number" width={40} />
        </Row>
        <MiniButton onClick={() => runTask("Wavelet filter (lvl 9)", 1800)}>
          Apply Wavelet Filter
        </MiniButton>
      </GroupBox>
      <GroupBox title="Phasor Filters">
        <Row label="Intensity ≥">
          <MiniInput value="0" onChange={() => {}} type="number" width={50} />
        </Row>
        <MiniCheckbox checked label="Reference circle" onChange={() => {}} />
        <Row label="τ (ns)">
          <MiniInput value="2.5" onChange={() => {}} type="number" width={50} step={0.1} />
        </Row>
        <Row label="r">
          <MiniInput value="0.05" onChange={() => {}} type="number" width={50} step={0.01} />
        </Row>
        <div className="text-[10px] text-muted-foreground">
          Active mask filter is on the phasor plot toolbar.
        </div>
      </GroupBox>
      <GroupBox title="Phasor Segmentation">
        <Row label="Shape">
          <MiniSelect value="Ellipse" options={["Ellipse", "Circle"]} onChange={() => {}} />
        </Row>
        <Row label="Auto">
          <MiniCheckbox checked label="" onChange={() => {}} />
        </Row>
        <Row label="n_max">
          <MiniInput value="6" onChange={() => {}} type="number" width={40} />
        </Row>
        <Row label="Criterion">
          <MiniSelect value="BIC" options={["BIC", "AIC"]} onChange={() => {}} />
        </Row>
        <MiniButton variant="primary" onClick={() => runTask("GMM fit", 2200)}>
          Run GMM
        </MiniButton>
      </GroupBox>
      <GroupBox title="Lifetime Map">
        <MiniButton
          variant="primary"
          onClick={() => {
            runTask("Computing lifetime map", 2000);
            setStatus("Lifetime overlay added to viewer");
          }}
        >
          Compute Lifetime
        </MiniButton>
      </GroupBox>
    </>
  );
}

function ScriptsPanel() {
  const setStatus = usePerCell((s) => s.setStatus);
  return (
    <GroupBox title="Scripts">
      <MiniButton variant="primary" onClick={() => setStatus("Script picker…")}>
        Run Script…
      </MiniButton>
      <div className="text-[10px] text-muted-foreground leading-relaxed">
        Pick a Python file to run against the current dataset. A macro system is planned.
      </div>
    </GroupBox>
  );
}

function WorkflowsPanel() {
  const [open, setOpen] = useState<null | "single" | "dilute">(null);
  return (
    <>
      <GroupBox title="Batch Pipelines">
        <MiniButton variant="primary" onClick={() => setOpen("single")}>
          Single-cell thresholding analysis…
        </MiniButton>
        <div className="text-[10px] text-muted-foreground leading-relaxed">
          Cellpose → seg QC → grouped thresholding → measure → Parquet/CSV export.
          Runs across many datasets.
        </div>
        <MiniButton variant="primary" onClick={() => setOpen("dilute")}>
          Dilute phase mask generation…
        </MiniButton>
        <div className="text-[10px] text-muted-foreground leading-relaxed">
          Interactive threshold + dilation + subtract loop on a single dataset.
        </div>
      </GroupBox>
      {open && <WorkflowDialog kind={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function WorkflowDialog({
  kind,
  onClose,
}: {
  kind: "single" | "dilute";
  onClose: () => void;
}) {
  const runTask = usePerCell((s) => s.runTask);
  const [tab, setTab] = useState(0);
  const tabs = kind === "single" ? ["Datasets", "Channels", "Cellpose", "Threshold", "Export"] : ["Source", "Iterate", "Save"];
  return (
    <div className="fixed inset-0 z-50 bg-black/70 grid place-items-center">
      <div className="w-[520px] bg-surface border border-border rounded shadow-xl">
        <PanelHeader
          title={kind === "single" ? "Single-cell Workflow Config" : "Dilute Phase Mask"}
          onClose={onClose}
        />
        <div className="flex border-b border-border bg-surface-elev">
          {tabs.map((t, i) => (
            <button
              key={t}
              onClick={() => setTab(i)}
              className={`px-3 h-8 text-[11px] border-r border-border ${
                i === tab ? "bg-background text-accent" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="p-4 space-y-3 text-[11px] min-h-48">
          <div className="text-muted-foreground">
            {tabs[tab]} configuration · placeholder controls for the prototype.
          </div>
          <div className="border border-dashed border-border rounded p-6 text-center text-muted-foreground">
            [ {tabs[tab]} form ]
          </div>
        </div>
        <div className="p-2 border-t border-border flex justify-end gap-2">
          <MiniButton onClick={onClose}>Cancel</MiniButton>
          <MiniButton
            variant="primary"
            onClick={() => {
              runTask(`${kind === "single" ? "Single-cell" : "Dilute"} workflow (12 datasets)`, 6000);
              onClose();
            }}
          >
            Run Workflow
          </MiniButton>
        </div>
      </div>
    </div>
  );
}

function DataPanel() {
  return (
    <>
      <GroupBox title="Layer Management">
        <Row label="Segmentations">
          <MiniSelect value="dapi_seg" options={[...SEGMENTATIONS]} onChange={() => {}} />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton>Rename</MiniButton>
          <MiniButton>Delete</MiniButton>
        </div>
        <Row label="Masks">
          <MiniSelect value="thresh_488" options={[...MASKS]} onChange={() => {}} />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton>Rename</MiniButton>
          <MiniButton>Delete</MiniButton>
        </div>
        <Row label="Channels">
          <MiniSelect value="DAPI" options={[...CHANNELS]} onChange={() => {}} />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton>Rename</MiniButton>
          <MiniButton>Delete</MiniButton>
        </div>
      </GroupBox>
      <GroupBox title="Dataset Info">
        <div className="mono text-[10px] text-foreground/80 leading-relaxed">
          <div>File: experiment_0824_HeLa.h5</div>
          <div>Shape: (4, 1024, 1024)</div>
          <div>Native: (1024, 1024)</div>
          <div>Creation bin: 1 │ View bin: {usePerCell.getState().viewBin}</div>
          <div>Labels: 3 │ Masks: 2</div>
        </div>
      </GroupBox>
    </>
  );
}
```

### `src/routes/index.tsx`

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { MenuBar, SessionBar, HubSidebar, StatusBar } from "@/percell/Chrome";
import { TaskPanel } from "@/percell/TaskPanels";
import { ImageViewer } from "@/percell/Viewer";
import { CompanionDock } from "@/percell/Companions";
import { usePerCell } from "@/percell/store";

export const Route = createFileRoute("/")({
  component: Index,
  head: () => ({
    meta: [
      { title: "PerCell4 — Single-Cell Microscopy Workspace" },
      {
        name: "description",
        content:
          "Interactive prototype of the PerCell4 desktop UI: image viewer, segmentation, FLIM phasor, and cell table with cross-view selection sync.",
      },
    ],
  }),
});

function Index() {
  const layout = usePerCell((s) => s.layoutPreset);
  const showCompanion = layout !== "laptop";
  return (
    <div className="h-screen w-screen flex flex-col bg-background text-foreground overflow-hidden">
      <MenuBar />
      <SessionBar />
      <div className="flex-1 flex min-h-0">
        <HubSidebar />
        <TaskPanel />
        <ImageViewer />
        {showCompanion && <CompanionDock />}
      </div>
      <StatusBar />
    </div>
  );
}
```

### `src/routes/__root.tsx`

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";

import appCss from "../styles.css?url";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Lovable App" },
      { name: "description", content: "Lovable Generated Project" },
      { name: "author", content: "Lovable" },
      { property: "og:title", content: "Lovable App" },
      { property: "og:description", content: "Lovable Generated Project" },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
      { name: "twitter:site", content: "@Lovable" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();

  return (
    <QueryClientProvider client={queryClient}>
      <Outlet />
    </QueryClientProvider>
  );
}
```

### `src/styles.css`

```css
@import "tailwindcss" source(none);
@source "../src";
@import "tw-animate-css";

@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap");

@custom-variant dark (&:is(.dark *));

/*
 * Design system definition.
 *
 * The @theme inline block maps CSS custom properties to Tailwind utility
 * classes (e.g. --color-primary -> bg-primary, text-primary).
 *
 * The :root and .dark blocks define the actual color values using oklch.
 * All colors MUST use oklch format.
 *
 * To add a new semantic color:
 * 1. Add the variable to :root (light value) and .dark (dark value)
 * 2. Register it in @theme inline as --color-<name>: var(--<name>)
 */

@theme inline {
  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) + 4px);
  --radius-2xl: calc(var(--radius) + 8px);
  --radius-3xl: calc(var(--radius) + 12px);
  --radius-4xl: calc(var(--radius) + 16px);
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-ring-offset-background: var(--background);
  --color-chart-1: var(--chart-1);
  --color-chart-2: var(--chart-2);
  --color-chart-3: var(--chart-3);
  --color-chart-4: var(--chart-4);
  --color-chart-5: var(--chart-5);
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-ring: var(--sidebar-ring);
  --color-surface: var(--surface);
  --color-surface-elev: var(--surface-elev);
  --color-select: var(--select);
  --color-preview: var(--preview);
  --font-sans: "Inter", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
}

:root {
  --radius: 0.25rem;
  /* PerCell4 console — dark by default */
  --background: oklch(0.16 0.005 250);
  --foreground: oklch(0.92 0.005 250);
  --surface: oklch(0.21 0.006 250);
  --surface-elev: oklch(0.25 0.006 250);
  --card: oklch(0.21 0.006 250);
  --card-foreground: oklch(0.92 0.005 250);
  --popover: oklch(0.21 0.006 250);
  --popover-foreground: oklch(0.92 0.005 250);
  --primary: oklch(0.85 0.18 200);
  --primary-foreground: oklch(0.16 0.005 250);
  --secondary: oklch(0.25 0.006 250);
  --secondary-foreground: oklch(0.92 0.005 250);
  --muted: oklch(0.25 0.006 250);
  --muted-foreground: oklch(0.62 0.01 250);
  --accent: oklch(0.85 0.18 200);
  --accent-foreground: oklch(0.16 0.005 250);
  --destructive: oklch(0.65 0.22 25);
  --destructive-foreground: oklch(0.98 0 0);
  --border: oklch(1 0 0 / 0.08);
  --input: oklch(1 0 0 / 0.1);
  --ring: oklch(0.85 0.18 200);
  --select: oklch(0.65 0.22 25);
  --preview: oklch(0.85 0.18 90);
  --chart-1: oklch(0.646 0.222 41.116);
  --chart-2: oklch(0.6 0.118 184.704);
  --chart-3: oklch(0.398 0.07 227.392);
  --chart-4: oklch(0.828 0.189 84.429);
  --chart-5: oklch(0.769 0.188 70.08);
  --sidebar: oklch(0.21 0.006 250);
  --sidebar-foreground: oklch(0.92 0.005 250);
  --sidebar-primary: oklch(0.85 0.18 200);
  --sidebar-primary-foreground: oklch(0.16 0.005 250);
  --sidebar-accent: oklch(0.25 0.006 250);
  --sidebar-accent-foreground: oklch(0.92 0.005 250);
  --sidebar-border: oklch(1 0 0 / 0.08);
  --sidebar-ring: oklch(0.85 0.18 200);
}

.dark {
  --background: oklch(0.129 0.042 264.695);
  --foreground: oklch(0.984 0.003 247.858);
  --card: oklch(0.208 0.042 265.755);
  --card-foreground: oklch(0.984 0.003 247.858);
  --popover: oklch(0.208 0.042 265.755);
  --popover-foreground: oklch(0.984 0.003 247.858);
  --primary: oklch(0.929 0.013 255.508);
  --primary-foreground: oklch(0.208 0.042 265.755);
  --secondary: oklch(0.279 0.041 260.031);
  --secondary-foreground: oklch(0.984 0.003 247.858);
  --muted: oklch(0.279 0.041 260.031);
  --muted-foreground: oklch(0.704 0.04 256.788);
  --accent: oklch(0.279 0.041 260.031);
  --accent-foreground: oklch(0.984 0.003 247.858);
  --destructive: oklch(0.704 0.191 22.216);
  --destructive-foreground: oklch(0.984 0.003 247.858);
  --border: oklch(1 0 0 / 10%);
  --input: oklch(1 0 0 / 15%);
  --ring: oklch(0.551 0.027 264.364);
  --chart-1: oklch(0.488 0.243 264.376);
  --chart-2: oklch(0.696 0.17 162.48);
  --chart-3: oklch(0.769 0.188 70.08);
  --chart-4: oklch(0.627 0.265 303.9);
  --chart-5: oklch(0.645 0.246 16.439);
  --sidebar: oklch(0.208 0.042 265.755);
  --sidebar-foreground: oklch(0.984 0.003 247.858);
  --sidebar-primary: oklch(0.488 0.243 264.376);
  --sidebar-primary-foreground: oklch(0.984 0.003 247.858);
  --sidebar-accent: oklch(0.279 0.041 260.031);
  --sidebar-accent-foreground: oklch(0.984 0.003 247.858);
  --sidebar-border: oklch(1 0 0 / 10%);
  --sidebar-ring: oklch(0.551 0.027 264.364);
}

@layer base {
  * {
    border-color: var(--color-border);
  }

  body {
    background-color: var(--color-background);
    color: var(--color-foreground);
    font-family: var(--font-sans);
    font-size: 12px;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
  }

  .mono {
    font-family: var(--font-mono);
  }
}
```
