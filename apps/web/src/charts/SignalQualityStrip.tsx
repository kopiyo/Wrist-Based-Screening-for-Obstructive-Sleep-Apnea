import { ChartFrame } from "./ChartFrame";
import type { ChartMode, QualityGap } from "./types";

export type QualitySummary = { usable_beat_ratio?: number | null; gap_count?: number | null; usable_beats?: number | null; rejected_artifact_count?: number | null };
export function SignalQualityStrip({ mode, durationSeconds, gaps = [] }: { mode: ChartMode<QualitySummary>; durationSeconds: number; gaps?: QualityGap[] }) {
  const q = mode.data;
  const ratio = q.usable_beat_ratio;
  return <ChartFrame mode={mode} title="Signal quality summary">
    <div className="p2-quality-head"><div><h3>Signal quality</h3><p>Usable segments and detected gaps across the recording.</p></div><strong>{ratio == null ? "Not available" : `${Math.round(ratio * 100)}% usable beat ratio`}</strong></div>
    <div className={`p2-quality-track${ratio == null ? " is-unknown" : ""}`} role="img" aria-label={ratio == null ? "Usable beat ratio not available for this recording" : `${Math.round(ratio * 100)}% usable beats with ${gaps.length} displayed gaps`}>
      {ratio != null && <span className="p2-quality-usable" style={{ width: `${Math.max(0, Math.min(1, ratio)) * 100}%` }} />}
      {gaps.map((gap, index) => <span key={index} className="p2-quality-gap" title={gap.label ?? "Signal quality gap"} style={{ left: `${durationSeconds > 0 ? Math.max(0, Math.min(100, gap.start_x / durationSeconds * 100)) : 0}%`, width: `${durationSeconds > 0 ? Math.max(.4, Math.min(100, (gap.end_x - gap.start_x) / durationSeconds * 100)) : .4}%` }} />)}
    </div>
    <div className="p2-quality-stats"><span>Detected gaps <strong>{q.gap_count ?? "Not available"}</strong></span><span>Usable beats <strong>{q.usable_beats ?? "Not available"}</strong></span><span>Rejected artifacts <strong>{q.rejected_artifact_count ?? "Not available"}</strong></span></div>
  </ChartFrame>;
}
