import type { ChartMode, NightDetail } from "./types";
import { ChartFrame, DataTable, ModelMeta, SourceBadge } from "./ChartFrame";

export function SourceDeviceComparison({ mode, deviceLabels = {} }: { mode: ChartMode<NightDetail[]>; deviceLabels?: Record<string, string> }) {
  const items = mode.data;
  const bySource = new Set(items.map(n => n.source)).size > 1;
  const byModel = new Set(items.map(n => n.model_version)).size > 1;
  const rows = items.map(n => [new Date(n.started_at).toLocaleString(), n.source, n.device_id ? deviceLabels[n.device_id] ?? "Device" : "Not recorded", n.model_version, n.apnea_burden_percent == null ? "Not available" : `${n.apnea_burden_percent.toFixed(1)}%`, n.source_validated ? "Validated" : "Unvalidated"]);
  return <ChartFrame mode={mode} title="Source and device comparison" table={<DataTable caption="Compared nights by source and device" columns={["Night", "Source", "Device", "Model version", "Burden estimate", "Source status"]} rows={rows} />}>
    <header className="p2-chart-header"><div><h3>Source and device comparison</h3><p className="p2-chart-subtitle">Each row retains its own source validation and model version.</p></div></header>
    {(bySource || byModel || items.some(n => !n.source_validated)) && <div className="p2-comparison-warning" role="note"><strong>Use care when comparing these nights.</strong><span>{bySource ? "Multiple sources are included. " : ""}{byModel ? "Model versions differ. " : ""}{items.some(n => !n.source_validated) ? "One or more sources are unvalidated. " : ""}These estimates are not evidence of a diagnosis.</span></div>}
    <div className="p2-comparison-list">{items.map(n => <article className="p2-comparison-row" key={n.id}>
      <div><strong>{new Date(n.started_at).toLocaleString()}</strong><small>{n.source} · {n.device_id ? deviceLabels[n.device_id] ?? "Device" : "Device not recorded"}</small></div>
      <div><span className="p2-comparison-value">{n.apnea_burden_percent == null ? "Not available" : `${n.apnea_burden_percent.toFixed(1)}%`}</span><small>Burden estimate</small></div>
      <div><SourceBadge validated={n.source_validated} /><ModelMeta sourceValidated={n.source_validated} modelVersion={n.model_version} /></div>
    </article>)}</div>
    <p className="p2-chart-note">Compare only with the source and model details shown. Wearable RR and PPG sources remain unvalidated unless the API explicitly reports otherwise.</p>
  </ChartFrame>;
}
