import { useMemo, useState } from "react";
import type { ChartPoint, ChartMode } from "./types";
import { ChartExports, ChartFrame, DataTable, ModelMeta, formatElapsed, timeTicks, useChartSvgRef } from "./ChartFrame";

const W = 900, H = 270, PAD = { left: 56, right: 24, top: 15, bottom: 44 };
export function ProbabilityTimeline({ mode, sourceValidated, modelVersion, thresholds = [0.3, 0.5, 0.7] }: {
  mode: ChartMode<ChartPoint[]>; sourceValidated: boolean; modelVersion: string; thresholds?: number[];
}) {
  const [threshold, setThreshold] = useState(thresholds[0] ?? 0.3);
  const svgRef = useChartSvgRef();
  const points = mode.data;
  const xmax = Math.max(1, ...points.map(p => p.x));
  const x = (value: number) => PAD.left + value / xmax * (W - PAD.left - PAD.right);
  const y = (value: number) => H - PAD.bottom - Math.max(0, Math.min(1, value)) * (H - PAD.top - PAD.bottom);
  const line = useMemo(() => points.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" "), [points]);
  const thresholdY = y(threshold);
  return <ChartFrame mode={mode} title="Estimated probability over time" table={<DataTable caption="Probability by elapsed minute" columns={["Elapsed (h:mm)", "Probability (%)"]} rows={points.map(p => [formatElapsed(p.x), (p.y * 100).toFixed(1)])} />}>
    <header className="p2-chart-header"><div><h3>Estimated probability over time</h3><ModelMeta sourceValidated={sourceValidated} modelVersion={modelVersion} /></div><div className="p2-chart-controls">
      <label>Reference threshold <select value={threshold} onChange={e => setThreshold(Number(e.target.value))}>{thresholds.map(value => <option key={value} value={value}>{(value * 100).toFixed(0)}%</option>)}</select></label>
      <ChartExports svgRef={svgRef} fileBaseName="probability-timeline" />
    </div></header>
    <p className="p2-chart-note">The reference line is a display aid, not a diagnosis or an independently validated clinical threshold.</p>
    <div className="p2-svg-wrap"><svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Probability timeline, selected reference ${(threshold * 100).toFixed(0)} percent`}>
      {[0, .25, .5, .75, 1].map(v => <g key={v}><line className="p2-grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} /><text className="p2-axis-label" x={PAD.left - 10} y={y(v) + 4} textAnchor="end">{Math.round(v * 100)}%</text></g>)}
      <line className="p2-threshold" x1={PAD.left} x2={W - PAD.right} y1={thresholdY} y2={thresholdY} />
      <path className="p2-probability-line" d={line} />
      {timeTicks(xmax).map((v, i, all) => <text key={v} className="p2-axis-label" x={x(v)} y={H - PAD.bottom + 18} textAnchor={i === 0 ? "start" : i === all.length - 1 ? "end" : "middle"}>{formatElapsed(v)}</text>)}
      <text className="p2-axis-title" x={W / 2} y={H - 4} textAnchor="middle">Elapsed time (h:mm)</text>
    </svg></div>
  </ChartFrame>;
}
