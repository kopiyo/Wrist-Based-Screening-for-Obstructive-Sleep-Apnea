import type { ChartMode } from "./types";

/** Selects one complete data source. It never concatenates demo and real data. */
export function DemoDataToggle<T>({ realData, demoData, enabled, onChange, label = "Demo data" }: {
  realData: T; demoData: T; enabled: boolean; onChange: (enabled: boolean) => void; label?: string;
}) {
  const selected: ChartMode<T> = enabled
    ? { kind: "demo", data: demoData, disclosure: "Synthetic demo data — not patient data" }
    : { kind: "real", data: realData };
  return <div className="p2-demo-toggle">
    <label><input type="checkbox" checked={enabled} onChange={event => onChange(event.currentTarget.checked)} />{label}</label>
    <span>{selected.kind === "demo" ? selected.disclosure : "Showing saved account data"}</span>
  </div>;
}
