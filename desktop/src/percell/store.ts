import { create } from "zustand";
import { CELLS, CHANNELS, MASKS, SEGMENTATIONS, type CellId } from "./mock";
import { getChannelImage, getMeasurements, loadImage, startCellpose } from "@/lib/api";

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

const CELLPOSE_TASK_ID = "cellpose";

interface State {
  dataset: string;
  // Absolute path of the loaded dataset on disk. `dataset` above is the
  // basename for display; the backend needs the full path on every call.
  datasetPath: string;
  channel: string;
  mask: string;
  segmentation: string;
  viewBin: number;
  alwaysOnTop: boolean;

  // Available items in each selector. Populated from the FastAPI
  // sidecar's /load_image response (real .h5 metadata) once a dataset
  // loads; defaulted to the mock constants pre-load so the UI is
  // demoable without a backend.
  channelNames: string[];
  maskNames: string[];
  segmentationNames: string[];
  flimFrequencyMhz: number | null;

  // Per-cell measurements as returned by /measurements. Empty until
  // the user runs Measure Cells. Schema is dynamic (column set depends
  // on the dataset's channels and active mask).
  measurementRows: Record<string, number | string | null>[];
  measurementColumns: string[];

  // Blob URL of the current active channel rendered as a PNG by the
  // backend's /channel_image endpoint. Lifecycle:
  //   - null when no dataset is loaded
  //   - URL.createObjectURL(blob) after a successful fetch
  //   - previous URL is URL.revokeObjectURL()'d before replacement so
  //     blobs don't pile up across channel switches.
  imageURL: string | null;
  // True while a /channel_image fetch is in flight so the viewer can
  // show a "Loading…" spinner instead of the stale image.
  imageLoading: boolean;

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
  loadDataset: (path: string) => Promise<void>;
  measureCells: (metrics?: string[]) => Promise<void>;
  loadChannelImage: () => Promise<void>;

  // Backend event handlers — called by the WS bridge in main.tsx.
  // Underscore prefix marks "framework plumbing, not for UI".
  _onTaskStarted: (label: string) => void;
  _onTaskProgress: (progress: number) => void;
  _onTaskFinished: (
    taskId: string,
    message: string,
    extra?: Record<string, unknown>,
  ) => void;

  // Refresh segmentation/mask/channel lists from /load_image WITHOUT
  // touching status, selection, filter, or active selectors. Use this
  // after a backend op writes new layers to disk (e.g. Cellpose).
  refreshDatasetMetadata: () => Promise<void>;

  detach: (c: CompanionId) => void;
  reattach: (c: CompanionId) => void;
}

export const visibleCells = (filter: Set<CellId> | null) =>
  filter ? CELLS.filter((c) => filter.has(c.id)) : CELLS;

export const usePerCell = create<State>((set, get) => ({
  dataset: "experiment_0824_HeLa.h5",
  datasetPath: "",
  channel: "DAPI",
  mask: "thresh_488",
  segmentation: "dapi_seg",
  viewBin: 1,
  alwaysOnTop: false,

  channelNames: [...CHANNELS],
  maskNames: [...MASKS],
  segmentationNames: [...SEGMENTATIONS],
  flimFrequencyMhz: 80.0,

  measurementRows: [],
  measurementColumns: [],

  imageURL: null,
  imageLoading: false,

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
  setChannel: (c) => {
    set({ channel: c, status: `Channel → ${c}` });
    // Channel switch invalidates the displayed raster. Fire-and-forget;
    // loadChannelImage handles its own errors via status.
    void get().loadChannelImage();
  },
  setMask: (m) => set({ mask: m, status: `Mask → ${m}` }),
  setSegmentation: (s) => set({ segmentation: s, status: `Segmentation → ${s}` }),
  setViewBin: (n) => {
    set({ viewBin: n, status: `View bin → ${n}` });
    void get().loadChannelImage();
  },
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
  // POST /load_image, then commit the returned metadata to state.
  // Resets selection/filter and forces the active channel/mask/seg to
  // the first entry of the new dataset (or empty if there is none) so
  // the SessionBar dropdowns are never stuck pointing at a stale name.
  loadDataset: async (path) => {
    const trimmed = path.trim();
    if (!trimmed) return;
    set({ status: `Loading ${trimmed}…` });
    let meta;
    try {
      meta = await loadImage(trimmed);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ status: `Load failed: ${msg}` });
      return;
    }
    const stem = meta.path.split("/").pop() || meta.path;
    set({
      dataset: stem,
      datasetPath: meta.path,
      channelNames: meta.channel_names,
      maskNames: meta.mask_names,
      segmentationNames: meta.segmentation_names,
      flimFrequencyMhz: meta.flim_frequency_mhz,
      channel: meta.channel_names[0] ?? "",
      mask: meta.mask_names[0] ?? "",
      segmentation: meta.segmentation_names[0] ?? "",
      selection: new Set(),
      filter: null,
      // Dataset switch invalidates any prior measurements + image.
      measurementRows: [],
      measurementColumns: [],
      status:
        `Loaded ${stem} — ${meta.channel_names.length} channel(s), ` +
        `${meta.segmentation_names.length} segmentation(s), ` +
        `${meta.mask_names.length} mask(s)`,
    });
    // Drop the stale blob URL from the previous dataset. Refetch the
    // first channel of the new one.
    const prevURL = get().imageURL;
    if (prevURL) URL.revokeObjectURL(prevURL);
    set({ imageURL: null });
    void get().loadChannelImage();
  },

  // POST /measurements against the currently loaded dataset using the
  // active segmentation + mask + view bin. The result DataFrame goes
  // into measurementRows/measurementColumns; the status bar reports
  // the count. Companion views can read these fields once they're
  // refactored to consume real measurement data.
  measureCells: async (metrics) => {
    const s = get();
    if (!s.datasetPath) {
      set({ status: "Load a dataset first (I/O → Load Dataset…)" });
      return;
    }
    if (!s.segmentation) {
      set({ status: "Select a segmentation in the Session bar first" });
      return;
    }
    const label = `Measuring cells (${metrics?.length ?? "all"} metrics)`;
    set({
      runningTask: { label, progress: 0, cancellable: false },
      status: label,
    });
    try {
      const resp = await getMeasurements({
        path: s.datasetPath,
        segmentation: s.segmentation,
        mask: s.mask || undefined,
        view_bin: s.viewBin,
        metrics,
      });
      set({
        runningTask: null,
        measurementRows: resp.rows,
        measurementColumns: resp.columns,
        status: `Measured ${resp.n_cells} cells, ${resp.columns.length} columns`,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ runningTask: null, status: `Measure failed: ${msg}` });
    }
  },

  runCellpose: async (params) => {
    const s = get();
    if (!s.datasetPath) {
      set({ status: "Load a dataset first (I/O → Load Dataset…)" });
      return;
    }
    if (!s.channel) {
      set({ status: "Select a channel in the Session bar first" });
      return;
    }
    const label = `Cellpose [${params.model}, d=${params.diameter}]`;
    set({
      runningTask: { label, progress: 0, cancellable: true },
      status: label,
    });
    try {
      await startCellpose({
        path: s.datasetPath,
        channel: s.channel,
        model: params.model,
        diameter: params.diameter,
        gpu: params.gpu,
        remove_edge_cells: params.remove_edge_cells,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ runningTask: null, status: `Cellpose request failed: ${msg}` });
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
  _onTaskFinished: (taskId, message, extra) => {
    set({ runningTask: null, status: message });
    // Cellpose wrote a new segmentation to the .h5: refresh the seg
    // list and snap the active selector to the new name. Skip on
    // failure — nothing changed on disk.
    if (taskId === CELLPOSE_TASK_ID && !message.startsWith("Cellpose failed")) {
      const newSeg =
        typeof extra?.new_segmentation === "string"
          ? (extra.new_segmentation as string)
          : null;
      // Quiet refresh — does NOT touch status, so the cellpose result
      // message stays visible.
      get()
        .refreshDatasetMetadata()
        .then(() => {
          if (newSeg) set({ segmentation: newSeg });
        });
    }
  },

  // Fetch the current active channel as a PNG and update imageURL.
  // Idempotent against rapid channel switches: the in-flight blob URL
  // is created last so a fast follow-up replaces it cleanly.
  loadChannelImage: async () => {
    const s = get();
    if (!s.datasetPath || !s.channel) {
      // Nothing to render; clear any stale image.
      if (s.imageURL) URL.revokeObjectURL(s.imageURL);
      set({ imageURL: null, imageLoading: false });
      return;
    }
    set({ imageLoading: true });
    let blob: Blob;
    try {
      blob = await getChannelImage({
        path: s.datasetPath,
        channel: s.channel,
        view_bin: s.viewBin,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ imageLoading: false, status: `Image fetch failed: ${msg}` });
      return;
    }
    // Only overwrite if the user hasn't moved on to a different channel
    // mid-flight (last-write-wins is fine when channel still matches).
    const cur = get();
    if (cur.channel !== s.channel || cur.datasetPath !== s.datasetPath) {
      // Stale response; discard.
      set({ imageLoading: false });
      return;
    }
    const url = URL.createObjectURL(blob);
    if (cur.imageURL) URL.revokeObjectURL(cur.imageURL);
    set({ imageURL: url, imageLoading: false });
  },

  refreshDatasetMetadata: async () => {
    const s = get();
    if (!s.datasetPath) return;
    try {
      const meta = await loadImage(s.datasetPath);
      set({
        channelNames: meta.channel_names,
        maskNames: meta.mask_names,
        segmentationNames: meta.segmentation_names,
        flimFrequencyMhz: meta.flim_frequency_mhz,
      });
    } catch {
      // Quiet refresh — leave stale lists if the load fails. The user
      // is still reading the cellpose status; we don't want to
      // overwrite it with a generic error message.
    }
  },

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
