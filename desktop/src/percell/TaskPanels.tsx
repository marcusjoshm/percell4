import { useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { PanelHeader, GroupBox, Row, MiniButton, MiniSelect, MiniInput, MiniCheckbox } from "./ui";
import { usePerCell } from "./store";

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
  const loadDataset = usePerCell((s) => s.loadDataset);
  return (
    <>
      <GroupBox title="Import">
        <MiniButton onClick={() => setStatus("Compress TIFF wizard opened")}>
          Compress TIFF Dataset…
        </MiniButton>
        <MiniButton
          onClick={async () => {
            const picked = await openDialog({
              multiple: false,
              filters: [{ name: "HDF5", extensions: ["h5", "hdf5"] }],
            });
            if (typeof picked === "string") loadDataset(picked);
          }}
        >
          Load Dataset…
        </MiniButton>
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
  const runCellpose = usePerCell((s) => s.runCellpose);
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
          onClick={() =>
            runCellpose({
              model,
              diameter: Number(diameter) || 0,
              gpu,
              remove_edge_cells: edge,
            })
          }
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
    measurementRows,
    measurementColumns,
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
        <div className="text-[10px] mono text-muted-foreground">
          {measurementRows.length > 0
            ? `Measured ${measurementRows.length} cells, ${measurementColumns.length} columns`
            : "No measurements yet"}
        </div>
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
  const measureCells = usePerCell((s) => s.measureCells);
  // Names here must match the keys in `percell4.domain.measure.metrics`
  // BUILTIN_METRICS — see backend rejection on unknown names.
  const all = [
    "area",
    "mean_intensity",
    "max_intensity",
    "min_intensity",
    "median_intensity",
    "integrated_intensity",
    "std_intensity",
    "mode_intensity",
    "sg_ratio",
  ];
  const [picked, setPicked] = useState(
    new Set(["area", "mean_intensity", "integrated_intensity"]),
  );
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
              measureCells([...picked]);
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
  const {
    dataset,
    channelNames,
    maskNames,
    segmentationNames,
    flimFrequencyMhz,
    viewBin,
    pixelSizeUm,
  } = usePerCell();
  return (
    <>
      <GroupBox title="Layer Management">
        <Row label="Segmentations">
          <MiniSelect
            value={segmentationNames[0] ?? ""}
            options={segmentationNames}
            onChange={() => {}}
          />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton>Rename</MiniButton>
          <MiniButton>Delete</MiniButton>
        </div>
        <Row label="Masks">
          <MiniSelect value={maskNames[0] ?? ""} options={maskNames} onChange={() => {}} />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton>Rename</MiniButton>
          <MiniButton>Delete</MiniButton>
        </div>
        <Row label="Channels">
          <MiniSelect
            value={channelNames[0] ?? ""}
            options={channelNames}
            onChange={() => {}}
          />
        </Row>
        <div className="grid grid-cols-2 gap-1.5">
          <MiniButton>Rename</MiniButton>
          <MiniButton>Delete</MiniButton>
        </div>
      </GroupBox>
      <GroupBox title="Dataset Info">
        <div className="mono text-[10px] text-foreground/80 leading-relaxed">
          <div>File: {dataset}</div>
          <div>Channels: {channelNames.length}</div>
          <div>Labels: {segmentationNames.length} │ Masks: {maskNames.length}</div>
          <div>View bin: {viewBin}</div>
          {flimFrequencyMhz !== null && <div>FLIM frequency: {flimFrequencyMhz} MHz</div>}
          <div>
            Pixel size: {pixelSizeUm !== null
              ? `${pixelSizeUm.toPrecision(4)} µm/px`
              : "(not in TIFF metadata)"}
          </div>
        </div>
      </GroupBox>
    </>
  );
}
