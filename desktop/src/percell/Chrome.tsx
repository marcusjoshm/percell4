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
import { CELLS } from "./mock";
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
    channelNames,
    maskNames,
    segmentationNames,
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
      <Selector label="Channel" value={channel} options={channelNames} onChange={setChannel} />
      <Selector label="Mask" value={mask} options={maskNames} onChange={setMask} />
      <Selector
        label="Segmentation"
        value={segmentation}
        options={segmentationNames}
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
