// Frontend API client for the Python FastAPI sidecar.
//
// The mock store under `src/percell/store.ts` simulates progress with
// requestAnimationFrame and does not call any of these yet — when you
// flip a panel to the real backend, swap the call inside `runTask` (or
// the relevant action) to use `subscribeEvents()` + the matching
// `start*` function below.

const BASE = "http://127.0.0.1:8765";

export async function health(): Promise<{ status: string }> {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

export interface DatasetMeta {
  path: string;
  shape: [number, number, number] | null;
  channel_names: string[];
  segmentation_names: string[];
  mask_names: string[];
  flim_frequency_mhz: number | null;
  creation_bin: number;
}

export async function loadImage(path: string): Promise<DatasetMeta> {
  const r = await fetch(`${BASE}/load_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `load_image failed: HTTP ${r.status}`);
  }
  return r.json();
}

export interface MeasureRequest {
  path: string;
  segmentation: string;
  mask?: string;
  view_bin?: number;
  metrics?: string[];
}

export interface MeasurementsResponse {
  n_cells: number;
  columns: string[];
  rows: Record<string, number | string | null>[];
}

export interface ChannelImageRequest {
  path: string;
  channel: string;
  view_bin?: number;
  contrast_low?: number;
  contrast_high?: number;
}

export async function getChannelImage(req: ChannelImageRequest): Promise<Blob> {
  const r = await fetch(`${BASE}/channel_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `channel_image failed: HTTP ${r.status}`);
  }
  return r.blob();
}

export interface LabelsImageRequest {
  path: string;
  segmentation: string;
  view_bin?: number;
}

export async function getLabelsImage(req: LabelsImageRequest): Promise<Blob> {
  const r = await fetch(`${BASE}/labels_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `labels_image failed: HTTP ${r.status}`);
  }
  return r.blob();
}

export interface MaskImageRequest {
  path: string;
  mask: string;
  view_bin?: number;
  color?: [number, number, number];
}

export async function getMaskImage(req: MaskImageRequest): Promise<Blob> {
  const r = await fetch(`${BASE}/mask_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `mask_image failed: HTTP ${r.status}`);
  }
  return r.blob();
}

export async function getMeasurements(req: MeasureRequest): Promise<MeasurementsResponse> {
  const r = await fetch(`${BASE}/measurements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `measurements failed: HTTP ${r.status}`);
  }
  return r.json();
}

export async function getPhasorHistogram(channel: string, harmonic = 1) {
  const r = await fetch(`${BASE}/phasor/histogram`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel, harmonic }),
  });
  return r.json();
}

// ── Long-running ops ────────────────────────────────────────────────
// All start* functions return a task_id. Subscribe to progress via
// subscribeEvents() to drive the status bar.

export async function startCellpose(params: {
  path: string;
  channel: string;
  model: string;
  diameter?: number;
  gpu?: boolean;
  remove_edge_cells?: boolean;
  name?: string;
}): Promise<{ task_id: string }> {
  const r = await fetch(`${BASE}/cellpose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `cellpose start failed: HTTP ${r.status}`);
  }
  return r.json();
}

export async function startComputePhasor(params: {
  channel: string;
  harmonic?: number;
}): Promise<{ task_id: string }> {
  const r = await fetch(`${BASE}/phasor/compute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}

export async function startWavelet(params: {
  channel: string;
  level: number;
}): Promise<{ task_id: string }> {
  const r = await fetch(`${BASE}/wavelet`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}

export async function startWorkflow(
  name: string,
  params: Record<string, unknown>,
): Promise<{ task_id: string }> {
  const r = await fetch(`${BASE}/workflow/${encodeURIComponent(name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}

// ── Event bus ───────────────────────────────────────────────────────

export type BackendEvent =
  | { type: "ready" }
  | { type: "task_started"; task_id: string; label: string }
  | { type: "task_progress"; task_id: string; progress: number }
  | {
      type: "task_finished";
      task_id: string;
      success: boolean;
      message: string;
      // Optional task-specific structured payload. Cellpose includes
      // { new_segmentation: <name> }.
      extra?: Record<string, unknown>;
    };

export function subscribeEvents(onEvent: (e: BackendEvent) => void): () => void {
  let closed = false;
  let ws: WebSocket | null = null;

  function connect() {
    if (closed) return;
    ws = new WebSocket(`ws://127.0.0.1:8765/events`);
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data) as BackendEvent);
      } catch (err) {
        console.warn("bad event payload", err);
      }
    };
    ws.onclose = () => {
      if (!closed) setTimeout(connect, 1000);
    };
  }
  connect();
  return () => {
    closed = true;
    ws?.close();
  };
}
