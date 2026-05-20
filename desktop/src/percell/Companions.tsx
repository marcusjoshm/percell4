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
