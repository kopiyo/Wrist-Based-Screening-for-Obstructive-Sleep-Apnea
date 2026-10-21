import { makeDemoMode } from "./types";
import type { ChartPoint, NightDetail, NightSummary } from "./types";
import type { QualitySummary } from "./SignalQualityStrip";

// Synthetic visualization fixtures only. They are never derived from or merged
// with account results; consumers must select them through DemoDataToggle.
export const demoProbability = makeDemoMode<ChartPoint[]>([
  { x: 60, y: .12 }, { x: 120, y: .18 }, { x: 180, y: .24 }, { x: 240, y: .39 },
  { x: 300, y: .31 }, { x: 360, y: .55 }, { x: 420, y: .41 }, { x: 480, y: .28 },
]);
export const demoIntervals = makeDemoMode<ChartPoint[]>([
  { x: 3, y: 790 }, { x: 8, y: 815 }, { x: 13, y: 802 }, { x: 18, y: 840 },
  { x: 23, y: 825 }, { x: 28, y: 780 }, { x: 33, y: 812 }, { x: 38, y: 835 },
]);
export const demoRejected: ChartPoint[] = [{ x: 21, y: 1450, flagged: true }, { x: 35, y: 420, flagged: true }];
export const demoBurden = makeDemoMode<NightSummary[]>([
  { id: "demo-night-1", started_at: "2026-01-11T21:00:00Z", source: "ecg", source_validated: true, model_version: "synthetic-demo", apnea_burden_percent: 18 },
  { id: "demo-night-2", started_at: "2026-01-12T21:00:00Z", source: "watch_rr", source_validated: false, model_version: "synthetic-demo", apnea_burden_percent: 26 },
  { id: "demo-night-3", started_at: "2026-01-13T21:00:00Z", source: "ecg", source_validated: true, model_version: "synthetic-demo", apnea_burden_percent: 21 },
]);
export const demoComparison = makeDemoMode<NightDetail[]>(demoBurden.data.map((night, i) => ({
  ...night,
  duration_seconds: 28800,
  charts: { probability_by_minute: [], interval_tachogram: [], rejected_artifacts: [] },
  signal_quality: { usable_beat_ratio: .94, gap_count: i, usable_beats: 12400 },
  disclaimer: "Screening tool, not a diagnosis. Research use only.",
})));
export const demoQuality = makeDemoMode<QualitySummary>({ usable_beat_ratio: .94, gap_count: 2, usable_beats: 12400, rejected_artifact_count: 2 });
