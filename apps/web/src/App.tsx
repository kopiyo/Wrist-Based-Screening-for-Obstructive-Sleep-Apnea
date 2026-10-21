import { useCallback, useEffect, useState } from "react";
import { Activity, ArrowDownToLine, Bell, Check, ChevronRight, CircleHelp, FileHeart, Moon, Settings2, ShieldCheck, Trash2, Watch, WifiOff } from "lucide-react";
import { api, getJob, getModelCard, getResult, NightDetail, NightSummary, Result, serializeCredential, publicKeyOptions, uploadFile, User } from "../../../packages/shared/src/client";
import { DemoDataToggle, NightlyBurden, ProbabilityTimeline, RRTachogram, SignalQualityStrip, SourceDeviceComparison, syntheticDemoFixtures } from "./charts";
import type { ChartPoint, ChartMode, QualityGap } from "./charts/types";
import "./charts/charts.css";

const disclaimer = "Screening tool, not a diagnosis. Research use only.";
const nav = [
  { id: "analyze", label: "Analyze", icon: FileHeart }, { id: "history", label: "Nights", icon: Moon },
  { id: "devices", label: "Devices", icon: Watch }, { id: "alerts", label: "Alerts", icon: Bell }, { id: "privacy", label: "Privacy & settings", icon: Settings2 },
] as const;
type Page = (typeof nav)[number]["id"];
type AuthView = "login" | "register" | "recovery";
type ConsentText = { text_version: string; text: string; disclaimer: string };
type Consent = { id: string; text_version: string; granted_at: string; scope: "save_analysis_results"; revoked_at?: string | null };
type AlertRule = { kind: "screening_result" | "worsening_trend" | "morning_summary" | "device_silent_overnight"; enabled: boolean; channels: ("email" | "web_push")[]; availability?: string; disabled_reason?: string };
type Device = { id: string; source: string; label?: string | null };
type Alert = { id: string; kind: string; created_at: string; status: string; source_validated?: boolean; model_version?: string; details_available?: boolean };

export default function App() {
  const [page, setPage] = useState<Page>("analyze");
  const [user, setUser] = useState<User | null>(null);
  const [authView, setAuthView] = useState<AuthView>("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [authMessage, setAuthMessage] = useState("");
  const [authError, setAuthError] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [recoveryPending, setRecoveryPending] = useState(() => sessionStorage.getItem("nightwiseRecoveryPending") === "1");
  const [kind, setKind] = useState<"ecg" | "rr_csv">("ecg");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [jobId, setJobId] = useState("");
  const [jobCapability, setJobCapability] = useState<string | undefined>();
  const [saveThisAnalysis, setSaveThisAnalysis] = useState(false);
  const [consentText, setConsentText] = useState<ConsentText | null>(null);
  const [grantedConsents, setGrantedConsents] = useState<Consent[]>([]);
  const [modelCard, setModelCard] = useState<Record<string, number | string | null> | null>(null);
  const [dark, setDark] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState("");
  const [verificationToken, setVerificationToken] = useState("");
  const [verificationBusy, setVerificationBusy] = useState(false);

  useEffect(() => { getModelCard().then(setModelCard).catch(() => setModelCard(null)); }, []);
  useEffect(() => { api.get<User>("/auth/me").then(setUser).catch(() => setUser(null)); }, []);
  useEffect(() => {
    if (!user) { setGrantedConsents([]); return; }
    let active = true;
    api.get<{ items: Consent[] }>("/privacy/consents").then(data => { if (active) setGrantedConsents(data.items ?? []); }).catch(() => { if (active) setGrantedConsents([]); });
    return () => { active = false; };
  }, [user?.id]);
  useEffect(() => {
    const prefix = "#verify-email=";
    if (location.hash.startsWith(prefix)) {
      setVerificationToken(decodeURIComponent(location.hash.slice(prefix.length)));
      setVerificationMessage("Confirm your email address to finish verification.");
    }
  }, []);
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const job = await getJob(jobId, jobCapability);
        if (cancelled) return;
        if (["finished", "complete"].includes(job.status) && job.result_id) { setResult(await getResult(job.result_id, jobCapability)); setBusy(false); setJobId(""); }
        else if (["finished", "complete"].includes(job.status) && job.result) { setResult(job.result); setBusy(false); setJobId(""); }
        else if (job.status === "failed" || job.status === "expired") { setError(job.error ?? "Analysis could not be completed."); setBusy(false); setJobId(""); }
        else window.setTimeout(poll, 850);
      } catch (e) { if (!cancelled) { setError(e instanceof Error ? e.message : "Could not retrieve analysis."); setBusy(false); setJobId(""); } }
    };
    const timer = window.setTimeout(poll, 500); return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId, jobCapability]);

  async function analyze() {
    if (!files.length) return;
    if (saveThisAnalysis && (!user || !consentText)) { setError("Sign in and accept the current consent before saving this analysis."); return; }
    setError(""); setResult(null); setBusy(true);
    try {
      let consentId: string | undefined;
      if (saveThisAnalysis && consentText && user) {
        const consent = await api.post<Consent>("/privacy/consents", { text_version: consentText.text_version, scope: "save_analysis_results", affirmative: true });
        consentId = consent.id;
        setGrantedConsents(items => [consent, ...items.filter(item => item.id !== consent.id)]);
      }
      const job = await uploadFile(files, kind, saveThisAnalysis, consentId); setJobId(job.job_id); setJobCapability(job.job_capability ?? undefined);
    } catch (e) { setError(e instanceof Error ? e.message : "Upload failed."); setBusy(false); }
  }
  async function signOut() { try { await api.post<void>("/auth/logout"); } finally { sessionStorage.removeItem("nightwiseRecoveryPending"); setRecoveryPending(false); setUser(null); setGrantedConsents([]); setSaveThisAnalysis(false); setConsentText(null); setPage("analyze"); } }
  async function confirmEmailVerification() {
    if (!verificationToken || verificationBusy) return;
    setVerificationBusy(true);
    try {
      await api.post<{ email_verified: boolean }>("/auth/email/verify", { token: verificationToken });
      setVerificationMessage("Email verified. Email alerts can now be enabled.");
      setUser(current => current ? { ...current, email_verified_at: new Date().toISOString() } : current);
      setVerificationToken(""); history.replaceState({}, "", `${location.pathname}${location.search}`);
    } catch (e) { setVerificationMessage(e instanceof Error ? e.message : "Verification link is invalid or expired."); }
    finally { setVerificationBusy(false); }
  }
  const pageTitle = nav.find(item => item.id === page)?.label ?? "Analyze";
  const badge = user ? user.email.slice(0, 1).toUpperCase() : "G";
  return <div className={dark ? "app dark" : "app"}>
    <aside className="sidebar">
      <a className="brand" href="#home" aria-label="Nightwise home"><span className="brand-mark"><Moon size={19} strokeWidth={1.8} /></span><span>nightwise<span className="brand-dot">.</span></span></a>
      <div className="side-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(item => { const Icon = item.icon; return <button key={item.id} onClick={() => setPage(item.id)} className={`nav-item ${page === item.id ? "selected" : ""}`}><Icon size={18} strokeWidth={1.8} /><span>{item.label}</span>{item.id === "analyze" && <ChevronRight size={15} className="nav-chevron" />}</button>; })}</nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={18} /><div><strong>Consent controls saving</strong><span>Anonymous analyses stay temporary.</span></div></div><div className="user-button"><span className="avatar">{badge}</span><span className="user-copy"><strong>{user?.email ?? "Guest workspace"}</strong><small>{user ? (user.email_verified_at ? "Verified account" : "Email not verified") : "Anonymous analysis"}</small></span><button className="plain-icon" onClick={() => user ? void signOut() : setPage("privacy")} aria-label={user ? "Sign out" : "Sign in"}><CircleHelp size={17} /></button></div></div>
    </aside>
    <main className="main-shell">
      <header className="topbar"><div className="crumb"><span>Workspace</span><ChevronRight size={14} /><strong>{pageTitle}</strong></div><div className="top-actions"><span className="connection"><span className="connection-dot" /> Private processing</span><button className="icon-button" aria-label="Toggle color theme" onClick={() => setDark(!dark)}>{dark ? "☼" : "◐"}</button><span className="avatar small" aria-hidden="true">{badge}</span></div></header>
      <div className="content"><div className="disclaimer"><ShieldCheck size={16} /><span>{disclaimer}</span></div>
        {verificationMessage && <div className="notice-box" role="status"><span>{verificationMessage}</span>{verificationToken && <button className="secondary-button" disabled={verificationBusy} onClick={() => void confirmEmailVerification()}>{verificationBusy ? "Verifying…" : "Confirm email"}</button>}</div>}
        {page === "analyze" && <>
          <div className="page-intro"><div><span className="eyebrow">OVERNIGHT SCREENING</span><h1>Review a night of sleep</h1><p>Analyze anonymously, or sign in and explicitly opt in to save this result.</p></div><div className="data-status"><span className="status-icon"><Check size={16} /></span><span>Never saved by default</span></div></div>
          <section className="upload-card card"><div className="section-heading"><div><h2>New screening</h2><p>Choose a supported file type to begin.</p></div><span className="step-label"><span>01</span> Upload</span></div>
            <div className="source-tabs" role="tablist" aria-label="Recording source"><button role="tab" aria-selected={kind === "ecg"} className={kind === "ecg" ? "active" : ""} onClick={() => { setKind("ecg"); setFiles([]); }}><Activity size={17} /> ECG recording <span className="validated">Research model</span></button><button role="tab" aria-selected={kind === "rr_csv"} className={kind === "rr_csv" ? "active" : ""} onClick={() => { setKind("rr_csv"); setFiles([]); }}><Watch size={17} /> RR intervals <span className="unvalidated">Unvalidated source</span></button></div>
            <label className={`dropzone ${files.length ? "has-file" : ""}`} htmlFor="upload" tabIndex={0} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); document.getElementById("upload")?.click(); } }}><input id="upload" type="file" accept={kind === "ecg" ? ".hea,.dat,.csv,.txt" : ".csv"} multiple={kind === "ecg"} onChange={e => setFiles(Array.from(e.target.files ?? []))} /><span className="upload-icon"><ArrowDownToLine size={21} /></span><strong>{files.length ? files.map(f => f.name).join(" + ") : "Choose files or browse"}</strong><span>{kind === "ecg" ? "WFDB .hea + .dat pair, or ECG samples in CSV" : "CSV columns: rr_ms and ts"}</span><small>Maximum combined upload size: 100 MB</small></label>
            {kind === "rr_csv" && <div className="source-warning"><span className="warning-mark">!</span><div><strong>Watch and PPG intervals are unvalidated sources.</strong><span>ECG model performance measures do not apply to RR/PPG.</span></div></div>}
            {user && <label className="save-optin"><input type="checkbox" checked={saveThisAnalysis} onChange={e => { const on = e.target.checked; setSaveThisAnalysis(on); if (on) api.get<ConsentText>("/privacy/consent").then(setConsentText).catch(err => setError(err.message)); else setConsentText(null); }} /><span><strong>Save this analysis to my nights</strong><small>Only this analysis will be saved. It will be evaluated for alerts only if saved with this consent. {consentText?.text ?? "Review the current consent before saving."}</small></span></label>}
            {error && <div className="error-box" role="alert"><WifiOff size={17} /><span>{error}</span></div>}
            <div className="upload-footer"><div className="privacy-inline"><ShieldCheck size={17} /><span>{saveThisAnalysis ? "This analysis is saved with your consent." : "Anonymous and temporary. No history is created."}</span></div><button className="primary-button" disabled={!files.length || busy || (saveThisAnalysis && !consentText)} onClick={() => void analyze()}>{busy ? <><span className="spinner" /> Analyzing…</> : <>Analyze recording <ChevronRight size={17} /></>}</button></div>
          </section>
          {result ? <ResultPanel result={result} modelCard={modelCard} onSave={() => setPage(user ? "history" : "privacy")} saved={saveThisAnalysis} /> : <div className="lower-grid"><section className="card model-card"><div className="section-heading"><div><span className="eyebrow">MODEL CARD</span><h2>What this screening uses</h2></div><span className="model-icon"><Activity size={18} /></span></div><p className="model-description">A two-stage model trained and evaluated on single-lead ECG recordings. It was not evaluated on smartwatch or PPG-derived intervals.</p>{kind === "ecg" ? <div className="metrics-grid"><Metric label="AUC" value={fmt(modelCard?.auc, 3)} /><Metric label="Sensitivity" value={fmtPct(modelCard?.sensitivity)} /><Metric label="Specificity" value={fmtPct(modelCard?.specificity)} /></div> : <div className="source-warning"><span className="warning-mark">!</span><div><strong>Wearable interval performance has not been evaluated.</strong><span>ECG model metrics are not displayed for this source.</span></div></div>}<div className="model-foot"><span>{kind === "ecg" ? `ECG model threshold ${fmt(modelCard?.threshold, 3)}` : "Threshold not validated for this source"}</span><span>AHI ≥ {fmt(modelCard?.ahi_threshold, 0)} · {fmt(modelCard?.patients, 0)} participants</span></div></section><section className="card how-card"><span className="eyebrow">THE PROCESS</span><h2>From signal to summary</h2><ol className="process-list"><li><span>1</span><div><strong>Signal quality</strong><small>Check timing and usable intervals</small></div></li><li><span>2</span><div><strong>Minute-level analysis</strong><small>Estimate probability by minute</small></div></li><li><span>3</span><div><strong>Screening summary</strong><small>Review a research screening result</small></div></li></ol></section></div>}
          <div className="bottom-note"><span><ShieldCheck size={15} /> Anonymous analysis remains available without an account.</span><button onClick={() => setPage("privacy")}>Privacy details <ChevronRight size={14} /></button></div>
        </>}
        {page === "history" && <HistoryPage user={user} onSignIn={() => setPage("privacy")} />}
        {page === "alerts" && <AlertsPage user={user} onSignIn={() => setPage("privacy")} />}
        {page === "devices" && <DevicesPage user={user} onSignIn={() => setPage("privacy")} />}
        {page === "privacy" && <PrivacyPage user={user} setUser={setUser} consents={grantedConsents} setConsents={setGrantedConsents} authView={authView} setAuthView={setAuthView} authEmail={authEmail} setAuthEmail={setAuthEmail} authBusy={authBusy} setAuthBusy={setAuthBusy} authMessage={authMessage} setAuthMessage={setAuthMessage} authError={authError} setAuthError={setAuthError} recoveryCode={recoveryCode} setRecoveryCode={setRecoveryCode} recoveryPending={recoveryPending} setRecoveryPending={setRecoveryPending} onSignOut={signOut} />}
      </div>
      <footer className="global-disclaimer">{disclaimer}</footer>
    </main>
  </div>;
}

function ResultPanel({ result, modelCard, saved, onSave }: { result: Result; modelCard: Record<string, number | string | null> | null; saved: boolean; onSave: () => void }) {
  const points = result.charts?.probability_by_minute ?? result.per_minute_probabilities?.map((y, i) => ({ x: (i + 1) * 60, y })) ?? [];
  const percent = Math.round(result.apnea_burden_percent);
  const realMode: ChartMode<ChartPoint[]> = { kind: "real", data: points };
  return <section className="result-wrap">
    <div className="result-heading"><div><span className="eyebrow">SCREENING SUMMARY</span><h2>Your recording has been analyzed</h2><p>Model {result.model_version} · {result.source}</p></div><div className="inline-actions"><button className="secondary-button" onClick={() => window.print()}><ArrowDownToLine size={16} /> Export summary</button>{saved ? <span className="saved-pill">Saved to nights</span> : <button className="secondary-button" onClick={onSave}>Review saving options</button>}</div></div>
    {!result.source_validated && <div className="source-warning result-warning"><span className="warning-mark">!</span><div><strong>Unvalidated source</strong><span>This result has not been validated against sleep-study outcomes. ECG performance metrics do not apply.</span></div></div>}
    <div className="result-grid"><section className="card referral-card"><span className="eyebrow">SCREENING OUTCOME</span><span className="outcome-icon"><Activity size={21} /></span><h3>{result.referral_decision ?? (result.score >= Number(modelCard?.threshold ?? 0.3032) ? "Sleep study recommended" : "Not indicated by this screening")}</h3><p>This screening is not a diagnosis. Discuss concerns with a qualified healthcare professional.</p><div className="threshold"><div><span>Model score</span><strong>{Math.round(result.score * 100)}%</strong></div><div className="threshold-track"><i style={{ left: `${Math.min(100, Number(modelCard?.threshold ?? 0.3032) * 100)}%` }} /><b style={{ width: `${Math.round(result.score * 100)}%` }} /></div><small>{result.source_validated ? `ECG model referral threshold: ${fmt(modelCard?.threshold, 3)}` : "Threshold has not been validated for this input source"}</small></div></section>
      <section className="card burden-card"><span className="eyebrow">APNEA BURDEN ESTIMATE</span><div className="burden-value">{percent}<span>%</span></div><p>Share of analyzed minutes flagged by the model</p><div className="burden-foot"><span>{points.length} minutes analyzed</span><span>{result.source}</span></div></section></div>
    <section className="card p2-card"><ProbabilityTimeline mode={realMode} sourceValidated={result.source_validated} modelVersion={result.model_version} /></section>
    <div className="result-grid"><section className="card quality-card"><span className="eyebrow">SIGNAL QUALITY</span><div className="quality-row"><span>Usable beat ratio</span><strong>{result.signal_quality.usable_beat_ratio == null ? "Not available" : `${Math.round(result.signal_quality.usable_beat_ratio * 100)}%`}</strong></div><div className="quality-row"><span>Detected gaps</span><strong>{result.signal_quality.gap_count ?? "Not available"}</strong></div></section><section className="card facts-card"><span className="eyebrow">SOURCE & MODEL</span><p>{result.source_validated ? "ECG research-model result." : "Unvalidated source. ECG performance metrics do not apply."}</p><span className="fact-disclaimer"><ShieldCheck size={15} /> {disclaimer}</span></section></div>
  </section>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="metric"><strong>{value}</strong><span>{label}</span></div>; }
function fmt(value: unknown, digits: number) { return typeof value === "number" ? value.toFixed(digits) : "—"; }
function fmtPct(value: unknown) { return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—"; }

function HistoryPage({ user, onSignIn }: { user: User | null; onSignIn: () => void }) {
  const [nights, setNights] = useState<NightSummary[]>([]); const [source, setSource] = useState(""); const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const [detail, setDetail] = useState<NightDetail | null>(null); const [selected, setSelected] = useState<string[]>([]); const [comparison, setComparison] = useState<NightDetail[] | null>(null); const [resolution, setResolution] = useState<"downsampled" | "full">("downsampled");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [demo, setDemo] = useState(false);
  const load = useCallback(async () => { if (!user) return; setBusy(true); setError(""); try { const q = new URLSearchParams(); if (from) q.set("from", new Date(`${from}T00:00:00`).toISOString()); if (to) q.set("to", new Date(`${to}T23:59:59`).toISOString()); if (source) q.set("source", source); const items: NightSummary[] = []; let cursor: string | null = null; do { if (cursor) q.set("cursor", cursor); const data: { items: NightSummary[]; next_cursor?: string | null } = await api.get(`/nights?${q}`); items.push(...data.items); cursor = data.next_cursor ?? null; } while (cursor); setNights(items); } catch (e) { setError(message(e)); } finally { setBusy(false); } }, [user, from, to, source]);
  useEffect(() => { void load(); }, [load]);
  async function openNight(id: string, mode = resolution) { setComparison(null); setError(""); try { setResolution(mode); setDetail(await api.get<NightDetail>(`/nights/${encodeURIComponent(id)}?resolution=${mode}&max_points=1000`)); } catch (e) { setError(message(e)); } }
  async function deleteNight(id: string) { if (!confirm("Delete this saved night and its chart data?")) return; try { await api.delete<void>(`/nights/${encodeURIComponent(id)}`); setDetail(null); setNights(n => n.filter(x => x.id !== id)); } catch (e) { setError(message(e)); } }
  async function compare() { if (selected.length < 2) return; try { const q = new URLSearchParams({ night_ids: selected.join(","), resolution: "downsampled", max_points: "1000" }); const data = await api.get<{ items: NightDetail[] }>(`/nights/compare?${q}`); setComparison(data.items); setDetail(null); } catch (e) { setError(message(e)); } }
  if (!user) return <SignedOut title="Your nights" copy="Sign in to review nights you explicitly chose to save." onSignIn={onSignIn} />;
  const chartNights = demo ? syntheticDemoFixtures.demoBurden.data : nights;
  const comparisonMode: ChartMode<NightDetail[]> = demo ? syntheticDemoFixtures.demoComparison : { kind: "real", data: comparison ?? [] };
  const detailSeries = detail?.charts.probability_by_minute ?? [];
  const detailIntervals = detail?.charts.interval_tachogram ?? [];
  const gaps: QualityGap[] = readGaps(detail?.signal_quality);
  return <section className="p2-page"><PageHeading eyebrow="SAVED WITH CONSENT" title="Nights" copy="Only analyses saved with your per-analysis consent appear here." />
    {error && <div className="error-box" role="alert">{error}</div>}
    <div className="card p2-toolbar"><label>From <input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label><label>To <input type="date" value={to} onChange={e => setTo(e.target.value)} /></label><label>Source <select value={source} onChange={e => setSource(e.target.value)}><option value="">All sources</option><option value="ecg">ECG</option><option value="watch_rr">Watch RR</option><option value="ppg_rr">PPG RR</option></select></label><button className="secondary-button" onClick={() => void load()}>{busy ? "Loading…" : "Apply filters"}</button><DemoDataToggle realData={nights} demoData={syntheticDemoFixtures.demoBurden.data} enabled={demo} onChange={setDemo} /></div>
    {demo && <p className="p2-demo-disclosure">Synthetic demo data — not patient data. Demo and account results are never combined.</p>}
    <div className="card p2-card"><NightlyBurden mode={demo ? syntheticDemoFixtures.demoBurden : { kind: "real", data: chartNights }} /></div>
    {!demo && <div className="card p2-list-card"><div className="section-heading"><div><h2>Saved nights</h2><p>{nights.length} nights match these filters</p></div><button className="secondary-button" disabled={selected.length < 2} onClick={() => void compare()}>Compare selected ({selected.length})</button></div>
      {nights.length === 0 ? <p className="empty-inline">No saved nights match these filters yet.</p> : <div className="p2-night-list">{nights.map(n => <article key={n.id} className="p2-night-row"><label><input aria-label={`Select ${new Date(n.started_at).toLocaleDateString()} for comparison`} type="checkbox" checked={selected.includes(n.id)} onChange={e => setSelected(old => e.target.checked ? [...old, n.id] : old.filter(id => id !== n.id))} /></label><div className="p2-night-main"><strong>{new Date(n.started_at).toLocaleString()}</strong><small>{n.source} · model {n.model_version}</small></div><span className={n.source_validated ? "validated" : "unvalidated"}>{n.source_validated ? "Validated source" : "Unvalidated source"}</span><button className="secondary-button" onClick={() => void openNight(n.id)}>Details</button></article>)}</div>}
    </div>}
    {comparison && <div className="card p2-card"><SourceDeviceComparison mode={comparisonMode} /></div>}
    {detail && <div className="p2-detail"><div className="section-heading"><div><span className="eyebrow">NIGHT DETAIL</span><h2>{new Date(detail.started_at).toLocaleString()}</h2><p>{detail.source} · model {detail.model_version} · {detail.source_validated ? "Validated source" : "Unvalidated source"}</p><label className="resolution-select">Chart resolution <select value={resolution} onChange={e => void openNight(detail.id, e.target.value as "downsampled" | "full")}><option value="downsampled">Downsampled (max 1,000 points)</option><option value="full">Full (max 50,000 points)</option></select></label></div><button className="secondary-button" onClick={() => void deleteNight(detail.id)}><Trash2 size={15} /> Delete night</button></div>{!detail.source_validated && <div className="source-warning"><span className="warning-mark">!</span><div><strong>Unvalidated source</strong><span>ECG model performance metrics do not apply.</span></div></div>}
      {!!detailSeries.length && <div className="card p2-card"><ProbabilityTimeline mode={{ kind: "real", data: detailSeries }} sourceValidated={detail.source_validated} modelVersion={detail.model_version} /></div>}
      {detailIntervals.length > 0 && <div className="card p2-card"><RRTachogram mode={{ kind: "real", data: detailIntervals }} rejected={detail.charts.rejected_artifacts ?? []} gaps={gaps} sourceValidated={detail.source_validated} modelVersion={detail.model_version} /></div>}
      <div className="card p2-card"><SignalQualityStrip mode={{ kind: "real", data: quality(detail.signal_quality) }} durationSeconds={detail.duration_seconds} gaps={gaps} /></div><p className="p2-disclaimer">{detail.disclaimer}</p>
    </div>}
  </section>;
}

function AlertsPage({ user, onSignIn }: { user: User | null; onSignIn: () => void }) {
  const [rules, setRules] = useState<AlertRule[]>([]); const [alerts, setAlerts] = useState<Alert[]>([]); const [quiet, setQuiet] = useState({ start: "22:00", end: "07:00" }); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { if (!user) return; try { const [r, a] = await Promise.all([api.get<any>("/alerts/rules"), api.get<any>("/alerts")]); setRules(r.rules ?? r.items ?? []); setQuiet(r.quiet_hours ?? quiet); setAlerts(a.items ?? []); } catch (e) { setError(message(e)); } }, [user]);
  useEffect(() => { void load(); }, [load]);
  if (!user) return <SignedOut title="Alerts" copy="Sign in to configure alerts for saved nights." onSignIn={onSignIn} />;
  function changeRule(kind: AlertRule["kind"], patch: Partial<AlertRule>) { setRules(old => old.map(rule => rule.kind === kind ? { ...rule, ...patch } : rule)); }
  async function save() { setBusy(true); setError(""); try { const response = await api.put<any>("/alerts/rules", { rules, quiet_hours: quiet }); setRules(response.rules ?? rules); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function connectPush() {
    setError("");
    try {
      const vapid = (import.meta as ImportMeta & { env?: { VITE_WEB_PUSH_PUBLIC_KEY?: string } }).env?.VITE_WEB_PUSH_PUBLIC_KEY;
      if (!vapid) throw new Error("Web Push is not configured for this deployment.");
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("This browser does not support Web Push.");
      const permission = await Notification.requestPermission(); if (permission !== "granted") throw new Error("Notification permission was not granted.");
      const registration = await navigator.serviceWorker.register("/service-worker.js");
      const key = Uint8Array.from(atob(vapid.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));
      const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      const created = await api.post<{ id: string }>("/alerts/web-push/subscriptions", subscription.toJSON());
      localStorage.setItem("nightwise-push-subscription-id", created.id);
      setError(""); alert("Web Push is connected. Notifications are generic; sign in to view details.");
    } catch (e) { setError(message(e)); }
  }
  async function disconnectPush() {
    const id = localStorage.getItem("nightwise-push-subscription-id");
    if (!id) return;
    try {
      await api.delete<void>(`/alerts/web-push/subscriptions/${encodeURIComponent(id)}`);
      const registration = await navigator.serviceWorker.getRegistration("/"); await registration?.pushManager.getSubscription().then(s => s?.unsubscribe());
      localStorage.removeItem("nightwise-push-subscription-id");
    } catch (e) { setError(message(e)); }
  }
  const labels: Record<AlertRule["kind"], string> = { screening_result: "Saved screening complete", worsening_trend: "Worsening trend", morning_summary: "Morning summary", device_silent_overnight: "Device silent overnight" };
  return <section className="p2-page"><PageHeading eyebrow="SAVED NIGHTS ONLY" title="Alerts" copy="Alerts only apply to nights saved with explicit consent. Notification text is generic; sign in to view details." />{error && <div className="error-box" role="alert">{error}</div>}
    <div className="card p2-list-card"><div className="section-heading"><div><h2>Alert rules</h2><p>Quiet hours and morning summaries use {user.timezone}.</p></div><div className="inline-actions"><button className="secondary-button" onClick={() => void connectPush()}>Connect Web Push</button>{localStorage.getItem("nightwise-push-subscription-id") && <button className="secondary-button" onClick={() => void disconnectPush()}>Disconnect Push</button>}</div></div>
      <div className="p2-rule-list">{rules.map(rule => <div className="p2-rule" key={rule.kind}><div className="p2-rule-copy"><strong>{labels[rule.kind]}</strong><small>{rule.kind === "worsening_trend" ? "At least 6 saved nights in 30 days; compare newest 3-night mean with preceding 3-night mean and alert at +10 percentage points." : rule.kind === "morning_summary" ? `One summary at 07:00 ${user.timezone} for the previous local calendar day.` : rule.kind === "device_silent_overnight" ? "Needs a live device; planned for Phase 3." : "One generic notification after a saved analysis completes."}</small>{rule.kind === "device_silent_overnight" && <span className="muted-pill">Needs live device · disabled until Phase 3</span>}</div><label className="switch-line"><input type="checkbox" checked={rule.enabled} disabled={rule.kind === "device_silent_overnight" || rule.availability === "needs_live_device"} onChange={e => changeRule(rule.kind, { enabled: e.target.checked })} /><span>Enabled</span></label><div className="p2-channel-list">{(["email", "web_push"] as const).map(channel => <label key={channel}><input type="checkbox" checked={rule.channels.includes(channel)} disabled={channel === "email" && !user.email_verified_at} onChange={e => changeRule(rule.kind, { channels: e.target.checked ? [...rule.channels, channel] : rule.channels.filter(x => x !== channel) })} />{channel === "email" ? `Email${user.email_verified_at ? "" : " (verify email first)"}` : "Web Push"}</label>)}</div></div>)}</div>
      <div className="p2-quiet-hours"><strong>Quiet hours ({user.timezone})</strong><label>Start <input type="time" value={quiet.start} onChange={e => setQuiet({ ...quiet, start: e.target.value })} /></label><label>End <input type="time" value={quiet.end} onChange={e => setQuiet({ ...quiet, end: e.target.value })} /></label></div><button className="primary-button" onClick={() => void save()} disabled={busy}>{busy ? "Saving…" : "Save alert settings"}</button>
      <p className="p2-note">Push and email notifications contain no score or health values. They include the research disclaimer and “Unvalidated source” where relevant. Details are available only after login.</p>
    </div>
    <div className="card p2-list-card"><h2>Recent alerts</h2>{alerts.length ? alerts.map(alert => <article className="p2-alert-row" key={alert.id}><Bell size={16} /><div><strong>{labels[alert.kind as AlertRule["kind"]] ?? "Screening alert"}</strong><small>{new Date(alert.created_at).toLocaleString()} · {alert.status}{alert.source_validated === false ? " · Unvalidated source" : ""}</small>{alert.details_available && <small>Account-only details are shown here after login.</small>}</div><span className="saved-pill">Private detail</span></article>) : <p className="empty-inline">No alerts yet. Only consented saved nights can produce alerts.</p>}</div>
  </section>;
}

function DevicesPage({ user, onSignIn }: { user: User | null; onSignIn: () => void }) {
  const [devices, setDevices] = useState<Device[]>([]); const [label, setLabel] = useState(""); const [source, setSource] = useState("watch_rr"); const [error, setError] = useState("");
  const load = useCallback(() => { if (user) api.get<any>("/devices").then(d => setDevices(d.items ?? [])).catch(e => setError(message(e))); }, [user]); useEffect(() => load(), [load]);
  if (!user) return <SignedOut title="Devices" copy="Sign in to manage account-associated devices." onSignIn={onSignIn} />;
  async function add() { try { await api.post("/devices", { source, label: label || null }); setLabel(""); load(); } catch (e) { setError(message(e)); } }
  async function remove(id: string) { try { await api.delete(`/devices/${encodeURIComponent(id)}`); load(); } catch (e) { setError(message(e)); } }
  return <section className="p2-page"><PageHeading eyebrow="ACCOUNT DEVICES" title="Devices" copy="Devices can label saved analyses. Live pairing and overnight presence are planned for Phase 3." />{error && <div className="error-box" role="alert">{error}</div>}<div className="card p2-list-card"><h2>Add a device record</h2><div className="p2-toolbar"><label>Source <select value={source} onChange={e => setSource(e.target.value)}><option value="watch_rr">Watch RR</option><option value="ppg_rr">PPG RR</option><option value="ecg">ECG</option></select></label><label>Label <input value={label} maxLength={80} onChange={e => setLabel(e.target.value)} placeholder="Optional label" /></label><button className="primary-button" onClick={() => void add()}>Add device</button></div>{devices.length ? devices.map(device => <article className="p2-night-row" key={device.id}><Watch size={17} /><div className="p2-night-main"><strong>{device.label || "Device"}</strong><small>{device.source}</small></div><button className="secondary-button" onClick={() => void remove(device.id)}>Remove</button></article>) : <p className="empty-inline">No devices added.</p>}</div></section>;
}

function PrivacyPage(props: { user: User | null; setUser: (user: User | null) => void; consents: Consent[]; setConsents: (consents: Consent[]) => void; authView: AuthView; setAuthView: (v: AuthView) => void; authEmail: string; setAuthEmail: (v: string) => void; authBusy: boolean; setAuthBusy: (v: boolean) => void; authMessage: string; setAuthMessage: (v: string) => void; authError: string; setAuthError: (v: string) => void; recoveryCode: string; setRecoveryCode: (v: string) => void; recoveryPending: boolean; setRecoveryPending: (v: boolean) => void; onSignOut: () => Promise<void> }) {
  const { user, setUser, consents, setConsents, authView, setAuthView, authEmail, setAuthEmail, authBusy, setAuthBusy, authMessage, setAuthMessage, authError, setAuthError, recoveryCode, setRecoveryCode, recoveryPending, setRecoveryPending } = props;
  const [timezone, setTimezone] = useState(user?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? "UTC"); const [deviceLabel, setDeviceLabel] = useState(""); const [newCodes, setNewCodes] = useState<string[]>([]); const [deleteText, setDeleteText] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { if (user) setTimezone(user.timezone); }, [user]);
  async function passkeyRegister() { if (!user) return; setAuthError(""); setAuthBusy(true); try { const options = await api.post<any>("/auth/passkeys/options"); const credential = await navigator.credentials.create({ publicKey: publicKeyOptions(options.public_key_options) as PublicKeyCredentialCreationOptions }); await api.post("/auth/passkeys", { challenge_id: options.challenge_id, credential: serializeCredential(credential), label: deviceLabel || undefined }); setAuthMessage("Passkey added to your account."); setDeviceLabel(""); } catch (e) { setAuthError(message(e)); } finally { setAuthBusy(false); } }
  async function finishRecovery() { setAuthError(""); setAuthBusy(true); try { const options = await api.post<any>("/auth/passkeys/options"); const credential = await navigator.credentials.create({ publicKey: publicKeyOptions(options.public_key_options) as PublicKeyCredentialCreationOptions }); await api.post("/auth/passkeys", { challenge_id: options.challenge_id, credential: serializeCredential(credential), label: deviceLabel || undefined }); const restored = await api.get<User>("/auth/me"); setUser(restored); sessionStorage.removeItem("nightwiseRecoveryPending"); setRecoveryPending(false); setRecoveryCode(""); setAuthMessage("Passkey added. Full account access has been restored."); } catch (e) { setAuthError(message(e)); } finally { setAuthBusy(false); } }
  async function runAuth() {
    setAuthError(""); setAuthMessage(""); setAuthBusy(true);
    try {
      if (authView === "register") {
        const options = await api.post<any>("/auth/register/options", { email: authEmail, timezone });
        const credential = await navigator.credentials.create({ publicKey: publicKeyOptions(options.public_key_options) as PublicKeyCredentialCreationOptions });
        const created = await api.post<User>("/auth/register/verify", { challenge_id: options.challenge_id, credential: serializeCredential(credential) });
        setUser(created); setAuthMessage("Account created. Verify your email using the link we sent before enabling email alerts.");
      } else if (authView === "recovery") {
        await api.post<{ status: string }>("/auth/recovery", { email: authEmail, code: recoveryCode }); sessionStorage.setItem("nightwiseRecoveryPending", "1"); setRecoveryPending(true); setAuthMessage("Recovery accepted. Add a passkey to restore full access.");
      } else {
        const options = await api.post<any>("/auth/login/options", { email: authEmail });
        const credential = await navigator.credentials.get({ publicKey: publicKeyOptions(options.public_key_options) as PublicKeyCredentialRequestOptions });
        const logged = await api.post<User>("/auth/login/verify", { challenge_id: options.challenge_id, credential: serializeCredential(credential) }); setUser(logged); setAuthMessage("Signed in.");
      }
    } catch (e) { setAuthError(message(e)); } finally { setAuthBusy(false); }
  }
  async function reauth(purpose: "export" | "delete" | "recovery_codes") {
    const options = await api.post<any>("/auth/reauth/options", { purpose });
    const credential = await navigator.credentials.get({ publicKey: publicKeyOptions(options.public_key_options) as PublicKeyCredentialRequestOptions });
    const verified = await api.post<{ reauth_token: string }>("/auth/reauth/verify", { challenge_id: options.challenge_id, credential: serializeCredential(credential) }); return verified.reauth_token;
  }
  async function exportData() { setAuthError(""); setBusy(true); try { const token = await reauth("export"); const data = await api.post<any>("/me/data/export", undefined, { "X-Reauth-Token": token }); const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "nightwise-export.json"; a.click(); URL.revokeObjectURL(url); } catch (e) { setAuthError(message(e)); } finally { setBusy(false); } }
  async function deleteAccount() { if (deleteText !== "DELETE MY DATA") { setAuthError("Type DELETE MY DATA to confirm."); return; } setBusy(true); setAuthError(""); try { const token = await reauth("delete"); await api.delete<void>("/me/data", { confirmation: deleteText }, { "X-Reauth-Token": token }); setUser(null); setConsents([]); setDeleteText(""); setAuthMessage("Account deletion has started."); } catch (e) { setAuthError(message(e)); } finally { setBusy(false); } }
  async function replaceCodes() { setAuthError(""); setBusy(true); try { const token = await reauth("recovery_codes"); const data = await api.post<{ codes: string[] }>("/auth/recovery-codes", undefined, { "X-Reauth-Token": token }); setNewCodes(data.codes); } catch (e) { setAuthError(message(e)); } finally { setBusy(false); } }
  async function saveTimezone() { try { const updated = await api.patch<User>("/settings", { timezone }); setUser(updated); setAuthMessage("Timezone saved."); } catch (e) { setAuthError(message(e)); } }
  async function revokeConsent(id: string) { const remove = confirm("Delete saved nights associated with this consent? Choose Cancel to keep existing nights and only stop future saves."); try { await api.delete<void>(`/privacy/consents/${encodeURIComponent(id)}`, { delete_saved_nights: remove }); setConsents(consents.map(c => c.id === id ? { ...c, revoked_at: new Date().toISOString() } : c)); setAuthMessage(remove ? "Consent revoked and saved nights deleted." : "Consent revoked. Existing saved nights were retained."); } catch (e) { setAuthError(message(e)); } }
  async function resend() { try { await api.post<void>("/auth/email/verification/resend"); setAuthMessage("If verification is needed, an email has been sent."); } catch (e) { setAuthError(message(e)); } }
  return <section className="p2-page"><PageHeading eyebrow="PRIVACY & ACCOUNT" title={user ? "Account settings" : "Sign in or create an account"} copy="Anonymous screening is always available. An account is needed only for saved results and account features." />
    {authError && <div className="error-box" role="alert">{authError}</div>}{authMessage && <div className="notice-box" role="status">{authMessage}</div>}
    {!user ? (recoveryPending ? <div className="card p2-list-card"><h2>Restore account access</h2><p>Your recovery code created a restricted session. Add a passkey to finish recovery and enable normal sign-in.</p><label>Passkey label<input value={deviceLabel} onChange={e => setDeviceLabel(e.target.value)} placeholder="This device" /></label><button className="primary-button" disabled={authBusy} onClick={() => void finishRecovery()}>{authBusy ? "Working…" : "Add passkey and restore access"}</button></div> : <div className="card p2-list-card"><div className="auth-tabs"><button className={authView === "login" ? "active" : ""} onClick={() => { setAuthView("login"); setAuthError(""); }}>Sign in</button><button className={authView === "register" ? "active" : ""} onClick={() => { setAuthView("register"); setAuthError(""); }}>Create account</button><button className={authView === "recovery" ? "active" : ""} onClick={() => { setAuthView("recovery"); setAuthError(""); }}>Recovery</button></div><div className="auth-form"><label>Email<input type="email" autoComplete="email" value={authEmail} onChange={e => setAuthEmail(e.target.value)} /></label>{authView === "register" && <label>Timezone (IANA)<input value={timezone} onChange={e => setTimezone(e.target.value)} placeholder="Africa/Nairobi" /></label>}{authView === "recovery" && <label>One-time recovery code<input autoComplete="one-time-code" value={recoveryCode} onChange={e => setRecoveryCode(e.target.value)} /></label>}<button className="primary-button" disabled={authBusy || !authEmail} onClick={() => void runAuth()}>{authBusy ? "Working…" : authView === "register" ? "Register with a passkey" : authView === "recovery" ? "Use recovery code" : "Continue with passkey"}</button><p className="p2-note">{authView === "login" ? "If this account exists, your passkey prompt will continue. Account lookup responses are the same either way." : authView === "register" ? "Existing email addresses cannot be registered again. Verify your email before using email alerts." : "A recovery code is single-use. After recovery, add a passkey to restore full access."}</p></div></div>) : <>
      <div className="card p2-list-card"><div className="section-heading"><div><h2>Account</h2><p>{user.email}</p></div><button className="secondary-button" onClick={() => void props.onSignOut()}>Sign out</button></div><p>{user.email_verified_at ? "Email verified" : "Email not verified — email alerts stay disabled until verified."}</p>{!user.email_verified_at && <button className="secondary-button" onClick={() => void resend()}>Resend verification link</button>}<div className="p2-toolbar"><label>Timezone (IANA)<input value={timezone} onChange={e => setTimezone(e.target.value)} /></label><button className="secondary-button" onClick={() => void saveTimezone()}>Save timezone</button></div></div>
      <div className="card p2-list-card"><h2>Passkeys and recovery</h2><div className="p2-toolbar"><label>Passkey label<input value={deviceLabel} onChange={e => setDeviceLabel(e.target.value)} placeholder="This device" /></label><button className="secondary-button" disabled={authBusy} onClick={() => void passkeyRegister()}>Add passkey</button><button className="secondary-button" disabled={busy} onClick={() => void replaceCodes()}>Create recovery codes</button></div>{newCodes.length > 0 && <div className="recovery-codes"><strong>Shown once. Save these codes securely.</strong><code>{newCodes.join("\n")}</code><button className="secondary-button" onClick={() => setNewCodes([])}>Hide codes</button></div>}</div>
      <ConsentSettings consents={consents} onRevoke={revokeConsent} />
      <div className="card p2-list-card"><h2>Your data</h2><p>Export and account deletion require a fresh passkey check. Deletion removes live account data and keys; backup copies expire according to the service retention policy.</p><div className="p2-toolbar"><button className="secondary-button" disabled={busy} onClick={() => void exportData()}><ArrowDownToLine size={15} /> Export JSON</button></div><div className="danger-zone"><strong>Delete account and all saved data</strong><label>Type DELETE MY DATA<input value={deleteText} onChange={e => setDeleteText(e.target.value)} /></label><button className="danger-button" disabled={busy || deleteText !== "DELETE MY DATA"} onClick={() => void deleteAccount()}><Trash2 size={15} /> Permanently delete</button></div></div>
    </>}
    <div className="card p2-list-card"><h2>Privacy details</h2><ul className="p2-privacy-list"><li>Anonymous analyses are temporary; nothing is saved without explicit per-analysis consent.</li><li>Logs contain request identifiers and operational status only, never email addresses, tokens, uploads, or health payloads.</li><li>Redis is private, password protected, JSON-serialized, and configured without disk persistence. Uploads stay in encrypted short-lived temp blobs on a dedicated volume and are removed after processing or expiry.</li><li>Backups are retained for 30 days. A hard delete removes live rows and wrapped data keys immediately; backup copies expire within 30 days and are not restored without replaying deletion tombstones.</li><li>Notification text is generic and always carries the research disclaimer. Alert details require login.</li></ul></div>
  </section>;
}

function ConsentSettings({ consents, onRevoke }: { consents: Consent[]; onRevoke: (id: string) => void }) {
  return <div className="card p2-list-card"><h2>Saved-analysis consent</h2><p>Revoke consent to stop future saving. You choose whether to delete nights saved under that consent.</p>{consents.length ? consents.map(c => <div className="p2-night-row" key={c.id}><div className="p2-night-main"><strong>{c.scope.replaceAll("_", " ")}</strong><small>Version {c.text_version} · {new Date(c.granted_at).toLocaleString()}{c.revoked_at ? ` · Revoked ${new Date(c.revoked_at).toLocaleString()}` : ""}</small></div>{c.revoked_at ? <span className="saved-pill">Revoked</span> : <button className="secondary-button" onClick={() => onRevoke(c.id)}>Revoke consent</button>}</div>) : <p className="empty-inline">Consents granted during this session will appear here.</p>}</div>;
}

function SignedOut({ title, copy, onSignIn }: { title: string; copy: string; onSignIn: () => void }) { return <section className="p2-page"><PageHeading eyebrow="ACCOUNT REQUIRED" title={title} copy={copy} /><div className="card p2-list-card"><p>Anonymous analyses remain available on the Analyze page. Sign-in is required only for saved history and account features.</p><button className="primary-button" onClick={onSignIn}>Sign in</button></div></section>; }
function PageHeading({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) { return <div className="page-intro"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div></div>; }
function message(e: unknown) { return e instanceof Error ? e.message : "The request could not be completed."; }
function readGaps(value?: Record<string, unknown>): QualityGap[] { const raw = value?.gaps; return Array.isArray(raw) ? raw.filter((g): g is QualityGap => !!g && typeof g === "object" && typeof (g as any).start_x === "number" && typeof (g as any).end_x === "number") : []; }
function quality(value?: Record<string, unknown>) { return { usable_beat_ratio: numberOrNull(value?.usable_beat_ratio), gap_count: numberOrNull(value?.gap_count), usable_beats: numberOrNull(value?.usable_beats), rejected_artifact_count: numberOrNull(value?.rejected_artifact_count) }; }
function numberOrNull(value: unknown): number | null { return typeof value === "number" ? value : null; }
