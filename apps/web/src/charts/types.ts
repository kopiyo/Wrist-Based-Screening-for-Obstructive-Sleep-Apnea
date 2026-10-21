/** Chart payloads mirror docs/phase-2-openapi.yaml. Chart x values are elapsed
 * seconds; probability chart y values are fractions in [0, 1], interval values
 * are milliseconds, and burden values are percentages in [0, 100]. */
export type ChartPoint = { x: number; y: number; flagged?: boolean | null };

export type ChartPayload = {
  probability_by_minute?: ChartPoint[];
  interval_tachogram?: ChartPoint[];
  rejected_artifacts?: ChartPoint[];
};

export type ScreeningSource = "ecg" | "watch_rr" | "ppg_rr";

export type NightSummary = {
  id: string;
  started_at: string;
  source: ScreeningSource;
  source_validated: boolean;
  model_version: string;
  score?: number | null;
  apnea_burden_percent?: number | null;
  device_id?: string | null;
};

export type NightDetail = NightSummary & {
  duration_seconds: number;
  charts: ChartPayload;
  signal_quality?: Record<string, unknown>;
  disclaimer: string;
};

/** A chart receives either one real response or one explicitly synthetic fixture. */
export type ChartMode<T> =
  | { kind: "real"; data: T }
  | { kind: "demo"; data: T; disclosure: "Synthetic demo data — not patient data" };

export const makeDemoMode = <T,>(data: T): ChartMode<T> => ({
  kind: "demo",
  data,
  disclosure: "Synthetic demo data — not patient data",
});

export type QualityGap = { start_x: number; end_x: number; label?: string };
