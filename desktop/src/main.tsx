import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { subscribeEvents } from "./lib/api";
import { usePerCell } from "./percell/store";
import "./styles.css";

const router = createRouter({ routeTree });

// Bridge the backend's /events WebSocket onto store actions. Lives for
// the lifetime of the app process; `subscribeEvents` auto-reconnects so
// a sidecar restart is transparent.
subscribeEvents((event) => {
  const s = usePerCell.getState();
  if (event.type === "task_started") s._onTaskStarted(event.label);
  else if (event.type === "task_progress") s._onTaskProgress(event.progress);
  else if (event.type === "task_finished") s._onTaskFinished(event.message);
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");
createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
