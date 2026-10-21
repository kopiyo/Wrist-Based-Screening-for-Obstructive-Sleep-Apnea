import type { ChartMode, NightSummary } from "./types";
import { ChartExports, ChartFrame, DataTable, SourceBadge, useChartSvgRef } from "./ChartFrame";

const W = 900, H = 300, PAD = { left: 55, right: 18, top: 20, bottom: 62 };
export function NightlyBurden({ mode }: { mode: ChartMode<NightSummary[]> }) {
  const svgRef = useChartSvgRef();
  const nights = [...mode.data].sort((a, b) => Date.parse(a.started_at) - Date.parse(b.started_at));
  const values = nights.flatMap(n => typeof n.apnea_burden_percent === "number" ? [n.apnea_burden_percent] : []);
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  const sd = values.length > 1 && mean != null ? Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)) : null;
  const plotW = W - PAD.left - PAD.right, plotH = H - PAD.top - PAD.bottom;
  const step = plotW / Math.max(nights.length, 1), barW = Math.min(36, step * .58);
  const y = (value: number) => PAD.top + plotH - Math.max(0, Math.min(100, value)) / 100 * plotH;
  // Keep roughly 12 date labels however many nights are loaded.
  const labelEvery = Math.max(1, Math.ceil(nights.length / 12));
  const combos = [...new Map(nights.map(n => [`${n.source}|${n.model_version}|${n.source_validated}`, n])).values()];
  const rows = nights.map(n => [new Date(n.started_at).toLocaleDateString(), n.source, n.apnea_burden_percent == null ? "Not available" : `${n.apnea_burden_percent.toFixed(1)}%`, n.source_validated ? "Validated" : "Unvalidated"]);
  return <ChartFrame mode={mode} title="Nightly burden estimates" table={<DataTable caption="Nightly burden and source status" columns={["Night", "Source", "Burden estimate", "Source status"]} rows={rows} />}>
    <header className="p2-chart-header"><div><h3>Nightly burden estimates</h3><p className="p2-chart-subtitle">{nights.length} night{nights.length === 1 ? "" : "s"} · mean {mean == null ? "not available" : `${mean.toFixed(1)}%`} · night-to-night SD {sd == null ? "not available" : `${sd.toFixed(1)} pts`}</p></div><ChartExports svgRef={svgRef} fileBaseName="nightly-burden" /></header>
    <p className="p2-chart-note">Burden estimates are descriptive outputs of this screening model. Compare like sources and model versions with care.</p>
    <div className="p2-svg-wrap"><svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Nightly burden estimates with source validation status">
      {[0, 25, 50, 75, 100].map(v => <g key={v}><line className="p2-grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} /><text className="p2-axis-label" x={PAD.left - 8} y={y(v) + 4} textAnchor="end">{v}%</text></g>)}
      {nights.map((night, index) => {
        const value = night.apnea_burden_percent;
        const cx = PAD.left + step * (index + .5);
        return <g key={night.id}>
          {value != null && <><rect className={night.source_validated ? "p2-burden-bar" : "p2-burden-bar unvalidated"} x={cx - barW / 2} y={y(value)} width={barW} height={Math.max(1, y(0) - y(value))} rx="4"><title>{value.toFixed(1)} percent; {night.source_validated ? "validated source" : "unvalidated source"}</title></rect><circle className={night.source_validated ? "p2-status-dot" : "p2-status-dot unvalidated"} cx={cx} cy={y(value) - 8} r="3" /> </>}
          {index % labelEvery === 0 && <text className="p2-axis-label" x={cx} y={H - PAD.bottom + 17} textAnchor="middle">{new Date(night.started_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</text>}
        </g>;
      })}
      {mean != null && <line className="p2-mean-line" x1={PAD.left} x2={W - PAD.right} y1={y(mean)} y2={y(mean)}><title>Mean {mean.toFixed(1)}%</title></line>}
      <text className="p2-axis-title" x={W / 2} y={H - 7} textAnchor="middle">Night</text>
    </svg></div>
    <div className="p2-burden-source-list">{combos.map(n => <span key={`${n.source}|${n.model_version}|${n.source_validated}`}><SourceBadge validated={n.source_validated} /> {n.source} · {n.model_version}</span>)}</div>
  </ChartFrame>;
}
