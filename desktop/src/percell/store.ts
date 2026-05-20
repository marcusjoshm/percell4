import { create } from "zustand";
import { CELLS, type CellId } from "./mock";
import { startCellpose } from "@/lib/api";

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

export interface CellposeParams {
  model: string;
  diameter: number;
  gpu: boolean;
  remove_edge_cells: boolean;
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

  // Real backend-driven actions. These post to the FastAPI sidecar and
  // let the WebSocket event bus drive progress + completion via the
  // _on* handlers below.
  runCellpose: (params: CellposeParams) => Promise<void>;

  // Backend event handlers — called by the WS bridge in main.tsx.
  // Underscore prefix marks "framework plumbing, not for UI".
  _onTaskStarted: (label: string) => void;
  _onTaskProgress: (progress: number) => void;
  _onTaskFinished: (message: string) => void;

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

  // ── Real backend-driven actions ──────────────────────────────────
  // The HTTP POST kicks off the task and returns immediately with a
  // task_id. Progress + completion arrive on the /events WebSocket,
  // routed through _onTask* below. The local label set here is shown
  // until the backend's task_started event overrides it (usually a
  // sub-second later).
  runCellpose: async (params) => {
    const label = `Cellpose [${params.model}, d=${params.diameter}]`;
    set({
      runningTask: { label, progress: 0, cancellable: true },
      status: label,
    });
    try {
      await startCellpose(params);
    } catch (e) {
      set({ runningTask: null, status: `Cellpose request failed: ${e}` });
    }
  },

  // ── Backend WebSocket event handlers ─────────────────────────────
  // Called by the bridge in main.tsx as `task_started` / `task_progress`
  // / `task_finished` events arrive over /events. They are deliberately
  // last-write-wins: there is only one running task at a time, so we
  // don't bother keying by task_id.
  _onTaskStarted: (label) =>
    set({ runningTask: { label, progress: 0, cancellable: true }, status: label }),
  _onTaskProgress: (progress) =>
    set((s) =>
      s.runningTask
        ? { runningTask: { ...s.runningTask, progress } }
        : {},
    ),
  _onTaskFinished: (message) =>
    set({ runningTask: null, status: message }),

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
