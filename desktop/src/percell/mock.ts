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
