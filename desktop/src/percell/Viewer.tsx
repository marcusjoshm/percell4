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
