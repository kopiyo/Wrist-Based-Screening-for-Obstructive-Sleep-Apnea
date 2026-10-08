import { lazy, Suspense, useEffect, useState } from "react";
import { Activity, ArrowDownToLine, Bell, Check, ChevronRight, CircleHelp, Clock3, FileHeart, FileUp, Moon, Settings2, ShieldCheck, Sun, Trash2, Watch, WifiOff } from "lucide-react";
import { getJob, getModelCard, getResult, Result, uploadFile } from "../../../packages/shared/src/client";

const ProbabilityChart = lazy(() => import("./ProbabilityChart"));
const disclaimer = "Screening tool, not a diagnosis. Research use only.";
const nav = [
  { id: "analyze", label: "Analyze", icon: FileHeart },
  { id: "history", label: "Nights", icon: Moon },
  { id: "devices", label: "Devices", icon: Watch },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "privacy", label: "Privacy & settings", icon: Settings2 },
];
type Page = (typeof nav)[number]["id"];

export default function App() {
  const [page, setPage] = useState<Page>("analyze");
  const [kind, setKind] = useState<"ecg" | "rr_csv">("ecg");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [jobId, setJobId] = useState("");
  const [modelCard, setModelCard] = useState<Record<string, number | string | null> | null>(null);
  const [dark, setDark] = useState(false);

  useEffect(() => { getModelCard().then(setModelCard).catch(() => setModelCard(null)); }, []);
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await getJob(jobId);
        if (cancelled) return;
        if (job.status === "complete" && job.result_id) {
          setResult(await getResult(job.result_id)); setBusy(false); setJobId("");
        } else if (job.status === "failed") {
          setError(job.error ?? "Analysis could not be completed."); setBusy(false); setJobId("");
        } else window.setTimeout(poll, 850);
      } catch (e) { setError(e instanceof Error ? e.message : "Could not retrieve analysis."); setBusy(false); setJobId(""); }
    };
    const timer = window.setTimeout(poll, 500);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId]);

  async function analyze() {
    if (!files.length) return;
    setError(""); setResult(null); setBusy(true);
    try { const job = await uploadFile(files, kind); setJobId(job.job_id); }
    catch (e) { setError(e instanceof Error ? e.message : "Upload failed."); setBusy(false); }
  }

  const pageTitle = nav.find(item => item.id === page)?.label ?? "Analyze";
  return <div className={dark ? "app dark" : "app"}>
    <aside className="sidebar">
      <a className="brand" href="#home" aria-label="Nightwise home"><span className="brand-mark"><Moon size={19} strokeWidth={1.8} /></span><span>nightwise<span className="brand-dot">.</span></span></a>
      <div className="side-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(item => { const Icon = item.icon; return <button key={item.id} onClick={() => setPage(item.id)} className={`nav-item ${page === item.id ? "selected" : ""}`}><Icon size={18} strokeWidth={1.8} /><span>{item.label}</span>{item.id === "analyze" && <ChevronRight size={15} className="nav-chevron" />}</button>; })}</nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={18} /><div><strong>No upload retention</strong><span>Files are processed in memory.</span></div></div><button className="user-button"><span className="avatar">G</span><span className="user-copy"><strong>Guest workspace</strong><small>Local session</small></span><CircleHelp size={17} className="help-icon" /></button></div>
    </aside>
    <main className="main-shell">
      <header className="topbar"><div className="crumb"><span>Workspace</span><ChevronRight size={14} /><strong>{pageTitle}</strong></div><div className="top-actions"><span className="connection"><span className="connection-dot" /> In-memory processing</span><button className="icon-button" aria-label="Toggle color theme" onClick={() => setDark(!dark)}>{dark ? <Sun size={18} /> : <Moon size={18} />}</button><span className="avatar small" aria-hidden="true">G</span></div></header>
      <div className="content"><div className="disclaimer"><ShieldCheck size={16} /><span>{disclaimer}</span></div>
        {page === "analyze" ? <>
          <div className="page-intro"><div><span className="eyebrow">OVERNIGHT SCREENING</span><h1>Review a night of sleep</h1><p>Upload an ECG recording or an interval export to create a screening summary.</p></div><div className="data-status"><span className="status-icon"><Check size={16} /></span><span>Not saved by default</span></div></div>
          <section className="upload-card card"><div className="section-heading"><div><h2>New screening</h2><p>Choose a supported file type to begin.</p></div><span className="step-label"><span>01</span> Upload</span></div>
            <div className="source-tabs" role="tablist" aria-label="Recording source"><button role="tab" aria-selected={kind === "ecg"} className={kind === "ecg" ? "active" : ""} onClick={() => { setKind("ecg"); setFiles([]); }}><Activity size={17} /> ECG recording <span className="validated">Validated input</span></button><button role="tab" aria-selected={kind === "rr_csv"} className={kind === "rr_csv" ? "active" : ""} onClick={() => { setKind("rr_csv"); setFiles([]); }}><Watch size={17} /> RR intervals <span className="unvalidated">Unvalidated source</span></button></div>
            <label className={`dropzone ${files.length ? "has-file" : ""}`} htmlFor="upload" tabIndex={0} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); document.getElementById("upload")?.click(); } }}><input id="upload" type="file" accept={kind === "ecg" ? ".hea,.dat,.csv,.txt" : ".csv"} multiple={kind === "ecg"} onChange={e => setFiles(Array.from(e.target.files ?? []))} /><span className="upload-icon"><FileUp size={21} /></span><strong>{files.length ? files.map(f => f.name).join(" + ") : "Choose files or browse"}</strong><span>{kind === "ecg" ? "WFDB .hea + .dat pair, or ECG samples in CSV" : "CSV columns: rr_ms and ts (timestamps in seconds)"}</span><small>{kind === "ecg" ? "A CSV ECG is assumed to be sampled at 100 Hz" : "At least 10 minutes of timestamped intervals"}</small></label>
            {kind === "rr_csv" && <div className="source-warning"><span className="warning-mark">!</span><div><strong>Watch and PPG intervals are not validated.</strong><span>Performance measures shown for the ECG model do not apply to these sources.</span></div></div>}
            {error && <div className="error-box" role="alert"><WifiOff size={17} /><span>{error}</span></div>}
            <div className="upload-footer"><div className="privacy-inline"><ShieldCheck size={17} /><span>Processed in memory. Results disappear when this service restarts.</span></div><button className="primary-button" disabled={!files.length || busy} onClick={analyze}>{busy ? <><span className="spinner" /> Analyzing…</> : <>Analyze recording <ChevronRight size={17} /></>}</button></div>
          </section>
          {result ? <ResultPanel result={result} modelCard={modelCard} /> : <div className="lower-grid"><section className="card model-card"><div className="section-heading"><div><span className="eyebrow">MODEL CARD</span><h2>What this screening uses</h2></div><span className="model-icon"><Activity size={18} /></span></div><p className="model-description">A two-stage model trained and evaluated on single-lead ECG recordings. It was not evaluated on smartwatch or PPG-derived intervals.</p>{kind === "ecg" ? <div className="metrics-grid"><Metric label="AUC" value={fmt(modelCard?.auc, 3)} /><Metric label="Sensitivity" value={fmtPct(modelCard?.sensitivity)} /><Metric label="Specificity" value={fmtPct(modelCard?.specificity)} /></div> : <div className="source-warning"><span className="warning-mark">!</span><div><strong>Wearable interval performance has not been evaluated.</strong><span>ECG model performance metrics are not displayed for this source.</span></div></div>}<div className="model-foot"><span>{kind === "ecg" ? `Threshold ${fmt(modelCard?.threshold, 3)}` : "ECG model threshold is not validated for this source"}</span><span>AHI ≥ {fmt(modelCard?.ahi_threshold, 0)} · {fmt(modelCard?.patients, 0)} patients</span></div></section><section className="card how-card"><span className="eyebrow">THE PROCESS</span><h2>From signal to summary</h2><ol className="process-list"><li><span>1</span><div><strong>Signal quality</strong><small>Check timing and usable intervals</small></div></li><li><span>2</span><div><strong>Minute-level analysis</strong><small>Estimate the probability for each minute</small></div></li><li><span>3</span><div><strong>Screening summary</strong><small>Show whether a sleep study is recommended</small></div></li></ol></section></div>}
          <div className="bottom-note"><span><ShieldCheck size={15} /> No account required for this local demonstration.</span><button onClick={() => setPage("privacy")}>Privacy details <ChevronRight size={14} /></button></div>
        </> : <EmptyPage page={page} setPage={setPage} />}
      </div>
      <footer className="global-disclaimer">{disclaimer}</footer>
    </main>
  </div>;
}

function ResultPanel({ result, modelCard }: { result: Result; modelCard: Record<string, number | string | null> | null }) {
  const chartData = result.per_minute_probabilities.map((probability, index) => ({ minute: index + 1, probability: Math.round(probability * 100) }));
  const percent = Math.round(result.apnea_burden_percent);
  return <section className="result-wrap">
    <div className="result-heading"><div><span className="eyebrow">SCREENING SUMMARY</span><h2>Your recording has been analyzed</h2></div><button className="secondary-button" onClick={() => window.print()}><ArrowDownToLine size={16} /> Export summary</button></div>
    {!result.source_validated && <div className="source-warning result-warning"><span className="warning-mark">!</span><div><strong>Unvalidated source</strong><span>This interval-based result has not been validated against sleep-study outcomes. ECG performance metrics do not apply.</span></div></div>}
    <div className="result-grid"><section className="card referral-card"><span className="eyebrow">SCREENING OUTCOME</span><span className="outcome-icon"><Activity size={21} /></span><h3>{result.referral_decision}</h3><p>This screening is not a diagnosis. Discuss concerns with a qualified healthcare professional.</p><div className="threshold"><div><span>Model score</span><strong>{Math.round(result.score * 100)}%</strong></div><div className="threshold-track"><i style={{ left: `${Math.min(100, Number(modelCard?.threshold ?? 0.3032) * 100)}%` }} /><b style={{ width: `${Math.round(result.score * 100)}%` }} /></div><small>{result.source_validated ? `ECG model referral threshold: ${fmt(modelCard?.threshold, 3)}` : "Threshold has not been validated for this input source"}</small></div></section>
      <section className="card burden-card"><span className="eyebrow">APNEA BURDEN ESTIMATE</span><div className="burden-value">{percent}<span>%</span></div><p>Share of analyzed minutes flagged by the model</p><div className="burden-foot"><span><Clock3 size={15} /> {chartData.length} minutes analyzed</span><span>{result.source === "ecg" ? "ECG source" : "Interval source"}</span></div></section></div>
    <section className="card chart-card"><div className="section-heading"><div><span className="eyebrow">MINUTE-BY-MINUTE</span><h3>Estimated probability over time</h3></div>{!result.source_validated && <span className="unvalidated">Unvalidated source</span>}</div><Suspense fallback={<div className="chart-area chart-loading" aria-label="Loading chart" />}><ProbabilityChart data={chartData} /></Suspense></section>
    <div className="result-grid"><section className="card quality-card"><span className="eyebrow">SIGNAL QUALITY</span><div className="quality-row"><span>Usable beat ratio</span><strong>{result.signal_quality.usable_beat_ratio == null ? "Not available" : `${Math.round(result.signal_quality.usable_beat_ratio * 100)}%`}</strong></div><div className="quality-row"><span>Detected gaps</span><strong>{result.signal_quality.gap_count == null ? "Not available" : result.signal_quality.gap_count}</strong></div><div className="quality-row"><span>Usable beats</span><strong>{result.signal_quality.usable_beats ?? "Not available"}</strong></div></section><section className="card facts-card"><span className="eyebrow">MODEL FACTS</span><p>Single-lead ECG model trained on the PhysioNet Apnea-ECG cohort. The clinical endpoint was AHI ≥ {fmt(modelCard?.ahi_threshold, 0)} in a cohort of {fmt(modelCard?.patients, 0)} patients.</p><span className="fact-disclaimer"><ShieldCheck size={15} /> {disclaimer}</span></section></div>
  </section>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="metric"><strong>{value}</strong><span>{label}</span></div>; }
function fmt(value: unknown, digits: number) { return typeof value === "number" ? value.toFixed(digits) : "—"; }
function fmtPct(value: unknown) { return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—"; }

function EmptyPage({ page, setPage }: { page: Page; setPage: (page: Page) => void }) {
  const contents: Record<Page, { title: string; copy: string }> = {
    analyze: { title: "Start a screening", copy: "Upload a recording to see a screening summary." },
    history: { title: "No nights yet", copy: "Completed screenings will appear here when you choose to save them." },
    devices: { title: "No devices connected", copy: "Live watch pairing is planned for a later phase. Phase 1 supports file uploads." },
    alerts: { title: "Alerts are not set up", copy: "Alert rules and delivery channels are planned for a later phase." },
    privacy: { title: "Privacy and data controls", copy: "This Phase 1 service processes uploads in memory. No files or results are saved after the service restarts." },
  };
  const item = contents[page];
  return <div className="empty-page"><div className="empty-art"><ShieldCheck size={29} /></div><span className="eyebrow">{page.toUpperCase()}</span><h1>{item.title}</h1><p>{item.copy}</p><div className="empty-actions">{page === "privacy" ? <><div><ShieldCheck size={17} /><span>Files are processed in memory</span></div><div><Trash2 size={17} /><span>No stored data to delete in this demo</span></div><div><ArrowDownToLine size={17} /><span>Export is available after analysis</span></div></> : <button className="primary-button" onClick={() => setPage("analyze")}>Start a screening <ChevronRight size={16} /></button>}</div><p className="empty-foot">{disclaimer}</p></div>;
}
