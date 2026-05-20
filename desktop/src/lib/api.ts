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

export async function loadImage(path: string) {
  const r = await fetch(`${BASE}/load_image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return r.json();
}

export async function getMeasurements() {
  const r = await fetch(`${BASE}/measurements`);
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
  model: string;
  diameter?: number;
  gpu?: boolean;
  remove_edge_cells?: boolean;
}): Promise<{ task_id: string }> {
  const r = await fetch(`${BASE}/cellpose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
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
  | { type: "task_finished"; task_id: string; success: boolean; message: string };

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
