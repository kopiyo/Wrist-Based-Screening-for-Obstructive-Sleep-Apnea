import type { ChartMode, ChartPoint, QualityGap } from "./types";
import { ChartExports, ChartFrame, DataTable, ModelMeta, formatElapsed, timeTicks, useChartSvgRef } from "./ChartFrame";

const W = 900, H = 280, PAD = { left: 62, right: 24, top: 16, bottom: 46 };
export function RRTachogram({ mode, rejected = [], gaps = [], sourceValidated, modelVersion, qualityLabel }: {
  mode: ChartMode<ChartPoint[]>; rejected?: ChartPoint[]; gaps?: QualityGap[]; sourceValidated: boolean; modelVersion: string; qualityLabel?: string;
}) {
  const svgRef = useChartSvgRef();
  const all = [...mode.data, ...rejected];
  const xmax = Math.max(1, ...all.map(p => p.x));
  const ymin0 = Math.min(300, ...all.map(p => p.y)), ymax0 = Math.max(1600, ...all.map(p => p.y));
  const span = Math.max(1, ymax0 - ymin0), ymin = ymin0 - span * .05, ymax = ymax0 + span * .05;
  const x = (value: number) => PAD.left + value / xmax * (W - PAD.left - PAD.right);
  const y = (value: number) => H - PAD.bottom - (value - ymin) / (ymax - ymin) * (H - PAD.top - PAD.bottom);
  const rows = [...mode.data.map(p => [formatElapsed(p.x), p.y.toFixed(0), "Accepted"] as (string | number)[]), ...rejected.map(p => [formatElapsed(p.x), p.y.toFixed(0), "Rejected artifact"])];
  return <ChartFrame mode={mode} title="RR interval tachogram" table={<DataTable caption="RR interval points" columns={["Elapsed (h:mm)", "Interval (ms)", "Classification"]} rows={rows} />}>
    <header className="p2-chart-header"><div><h3>RR interval tachogram</h3><ModelMeta sourceValidated={sourceValidated} modelVersion={modelVersion} /></div><ChartExports svgRef={svgRef} fileBaseName="rr-tachogram" /></header>
    <p className="p2-chart-note">Rejected artifacts and signal gaps are shown separately from accepted intervals. {qualityLabel ? `Signal quality: ${qualityLabel}.` : ""}</p>
    <div className="p2-chart-legend"><span><i className="p2-legend-dot" /> Accepted interval</span><span><i className="p2-legend-dot rejected" /> Rejected artifact</span>{gaps.length > 0 && <span><i className="p2-legend-gap" /> Quality gap ({gaps.length})</span>}</div>
    <div className="p2-svg-wrap"><svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="RR interval tachogram with rejected artifacts and quality gaps">
      {gaps.map((gap, index) => <rect key={`gap-${index}`} className="p2-gap" x={x(gap.start_x)} width={Math.max(2, x(gap.end_x) - x(gap.start_x))} y={PAD.top} height={H - PAD.top - PAD.bottom}><title>{gap.label ?? "Signal quality gap"}</title></rect>)}
      {[ymin, (ymin + ymax) / 2, ymax].map((v, i) => <g key={i}><line className="p2-grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} /><text className="p2-axis-label" x={PAD.left - 9} y={y(v) + 4} textAnchor="end">{Math.round(v)}</text></g>)}
      {mode.data.map((p, i) => <circle key={`ok-${i}`} className="p2-rr-point" cx={x(p.x)} cy={y(p.y)} r="2.2" />)}
      {rejected.map((p, i) => <circle key={`bad-${i}`} className="p2-rejected-point" cx={x(p.x)} cy={y(p.y)} r="3.5"><title>Rejected artifact at {formatElapsed(p.x)}</title></circle>)}
      <text className="p2-axis-title" x="15" y={H / 2} textAnchor="middle" transform={`rotate(-90 15 ${H / 2})`}>RR interval (ms)</text>
      {timeTicks(xmax).map((v, i, all) => <text key={v} className="p2-axis-label" x={x(v)} y={H - PAD.bottom + 18} textAnchor={i === 0 ? "start" : i === all.length - 1 ? "end" : "middle"}>{formatElapsed(v)}</text>)}
      <text className="p2-axis-title" x={W / 2} y={H - 4} textAnchor="middle">Elapsed time (h:mm)</text>
    </svg></div>
  </ChartFrame>;
}
