import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { Activity, ArrowDownToLine, Bell, Check, ChevronRight, CircleCheck, FileHeart, LogIn, LogOut, Moon, Settings2, ShieldCheck, Sun, Trash2, Upload, Watch, WifiOff, X } from "lucide-react";
import { api, getJob, getModelCard, getResult, NightDetail, NightSummary, Result, serializeCredential, publicKeyOptions, uploadFile, User } from "../../../packages/shared/src/client";
import { DemoDataToggle, NightlyBurden, ProbabilityTimeline, RRTachogram, SignalQualityStrip, SourceDeviceComparison, syntheticDemoFixtures } from "./charts";
import type { ChartPoint, ChartMode, QualityGap } from "./charts/types";
import "./charts/charts.css";

const disclaimer = "Screening tool, not a diagnosis. Research use only.";
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
const nav = [
  { id: "analyze", label: "Analyze", short: "Analyze", icon: FileHeart }, { id: "history", label: "Nights", short: "Nights", icon: Moon },
  { id: "devices", label: "Devices", short: "Devices", icon: Watch }, { id: "alerts", label: "Alerts", short: "Alerts", icon: Bell },
  { id: "privacy", label: "Privacy & settings", short: "Settings", icon: Settings2 },
] as const;
type Page = (typeof nav)[number]["id"];
type AuthView = "login" | "register" | "recovery";
type ConsentText = { text_version: string; text: string; disclaimer: string };
type Consent = { id: string; text_version: string; granted_at: string; scope: "save_analysis_results"; revoked_at?: string | null };
type AlertRule = { kind: "screening_result" | "worsening_trend" | "morning_summary" | "device_silent_overnight"; enabled: boolean; channels: ("email" | "web_push")[]; availability?: string; disabled_reason?: string };
type Device = { id: string; source: string; label?: string | null };
type Alert = { id: string; kind: string; created_at: string; status: string; source_validated?: boolean; model_version?: string; details_available?: boolean };
type Phase = "idle" | "uploading" | "queued" | "analyzing";
type Notify = (text: string) => void;

// ---------------------------------------------------------------------------
// Small shared UI: toasts and an accessible confirm dialog replacing alert()/confirm().
// ---------------------------------------------------------------------------
type DialogAction<T> = { label: string; value: T; tone?: "primary" | "danger" | "secondary" };
type DialogRequest = { title: string; body: ReactNode; actions: DialogAction<unknown>[]; resolve: (value: unknown) => void };

function useDialog() {
  const [request, setRequest] = useState<DialogRequest | null>(null);
  const ask = useCallback(<T,>(title: string, body: ReactNode, actions: DialogAction<T>[]) =>
    new Promise<T | null>(resolve => setRequest({ title, body, actions: actions as DialogAction<unknown>[], resolve: value => resolve(value as T | null) })), []);
  const close = (value: unknown) => { request?.resolve(value); setRequest(null); };
  return { ask, element: request && <ConfirmDialog request={request} onClose={close} /> };
}

function ConfirmDialog({ request, onClose }: { request: DialogRequest; onClose: (value: unknown) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current; if (dialog && !dialog.open) dialog.showModal(); }, []);
  return <dialog ref={ref} className="confirm-dialog" aria-labelledby="confirm-title" onCancel={e => { e.preventDefault(); onClose(null); }}>
    <h2 id="confirm-title">{request.title}</h2>
    <div className="confirm-body">{request.body}</div>
    <div className="confirm-actions">
      <button type="button" className="secondary-button" onClick={() => onClose(null)}>Cancel</button>
      {request.actions.map(action => <button type="button" key={action.label} className={action.tone === "danger" ? "danger-button" : action.tone === "secondary" ? "secondary-button" : "primary-button"} onClick={() => onClose(action.value)}>{action.label}</button>)}
    </div>
  </dialog>;
}

function useToasts() {
  const [toasts, setToasts] = useState<{ id: number; text: string }[]>([]);
  const notify = useCallback<Notify>(text => {
    const id = Date.now() + Math.random();
    setToasts(items => [...items.slice(-2), { id, text }]);
    window.setTimeout(() => setToasts(items => items.filter(t => t.id !== id)), 4500);
  }, []);
  const element = <div className="toast-region" role="status" aria-live="polite">{toasts.map(t => <div className="toast" key={t.id}><CircleCheck size={16} aria-hidden="true" /><span>{t.text}</span><button type="button" aria-label="Dismiss" onClick={() => setToasts(items => items.filter(x => x.id !== t.id))}><X size={14} aria-hidden="true" /></button></div>)}</div>;
  return { notify, element };
}

function readStored(key: string) { try { return localStorage.getItem(key); } catch { return null; } }
function writeStored(key: string, value: string | null) { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* storage unavailable */ } }

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
  const [grantedConsents, setGrantedConsents] = useState<Consent[]>([]);
  const [modelCard, setModelCard] = useState<Record<string, number | string | null> | null>(null);
  const [dark, setDark] = useState(() => { const stored = readStored("nightwise-theme"); return stored ? stored === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false; });
  const [verificationMessage, setVerificationMessage] = useState("");
  const [verificationToken, setVerificationToken] = useState("");
  const [verificationBusy, setVerificationBusy] = useState(false);
  const headingFocus = useRef(false);
  const { notify, element: toastRegion } = useToasts();
  const { ask, element: dialog } = useDialog();

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
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    writeStored("nightwise-theme", dark ? "dark" : "light");
  }, [dark]);
  const pageTitle = nav.find(item => item.id === page)?.label ?? "Analyze";
  useEffect(() => {
    document.title = `${pageTitle} · Nightwise`;
    // Move focus to the new page heading for keyboard and screen reader users, but not on first load.
    if (headingFocus.current) document.querySelector<HTMLElement>(".content h1")?.focus();
    headingFocus.current = true;
  }, [page, pageTitle]);

  function go(next: Page) { setPage(next); window.scrollTo({ top: 0 }); }
  async function signOut() {
    try { await api.post<void>("/auth/logout"); } finally {
      sessionStorage.removeItem("nightwiseRecoveryPending"); setRecoveryPending(false); setUser(null); setGrantedConsents([]); go("analyze"); notify("Signed out.");
    }
  }
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
  const badge = user ? user.email.slice(0, 1).toUpperCase() : "G";
  return <div className={dark ? "app dark" : "app"}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar">
      <a className="brand" href="#home" aria-label="Nightwise home" onClick={e => { e.preventDefault(); go("analyze"); }}><span className="brand-mark"><Moon size={19} strokeWidth={1.8} aria-hidden="true" /></span><span>nightwise<span className="brand-dot">.</span></span></a>
      <div className="side-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{nav.map(item => {
        const Icon = item.icon;
        return <button key={item.id} onClick={() => go(item.id)} aria-label={item.label} title={item.label} aria-current={page === item.id ? "page" : undefined} className={`nav-item ${page === item.id ? "selected" : ""}`}>
          <Icon size={18} strokeWidth={1.8} aria-hidden="true" /><span className="nav-label">{item.label}</span><span className="nav-short" aria-hidden="true">{item.short}</span>
        </button>;
      })}</nav>
      <div className="sidebar-bottom"><div className="privacy-note"><ShieldCheck size={18} aria-hidden="true" /><div><strong>Consent controls saving</strong><span>Anonymous analyses stay temporary.</span></div></div>
        <div className="user-button"><span className="avatar" aria-hidden="true">{badge}</span><span className="user-copy"><strong>{user?.email ?? "Guest workspace"}</strong><small>{user ? (user.email_verified_at ? "Verified account" : "Email not verified") : "Anonymous analysis"}</small></span>
          <button className="plain-icon" onClick={() => user ? void signOut() : go("privacy")} aria-label={user ? "Sign out" : "Sign in"} title={user ? "Sign out" : "Sign in"}>{user ? <LogOut size={17} aria-hidden="true" /> : <LogIn size={17} aria-hidden="true" />}</button></div></div>
    </aside>
    <main className="main-shell">
      <header className="topbar"><div className="crumb"><span>Workspace</span><ChevronRight size={14} aria-hidden="true" /><strong>{pageTitle}</strong></div>
        <div className="top-actions"><span className="connection"><span className="connection-dot" aria-hidden="true" /> Private processing</span>
          <button className="icon-button" aria-label="Toggle color theme" aria-pressed={dark} title={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={() => setDark(!dark)}>{dark ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}</button>
          <button className="avatar small avatar-button" aria-label={user ? `Account: ${user.email}` : "Account"} onClick={() => go("privacy")}>{badge}</button></div></header>
      <div className="content" id="main-content"><div className="disclaimer"><ShieldCheck size={16} aria-hidden="true" /><span>{disclaimer}</span></div>
        {verificationMessage && <div className="notice-box" role="status"><span>{verificationMessage}</span>{verificationToken && <button className="secondary-button" disabled={verificationBusy} onClick={() => void confirmEmailVerification()}>{verificationBusy ? "Verifying…" : "Confirm email"}</button>}</div>}
        {page === "analyze" && <AnalyzePage user={user} modelCard={modelCard} notify={notify} onConsent={consent => setGrantedConsents(items => [consent, ...items.filter(item => item.id !== consent.id)])} go={go} />}
        {page === "history" && <HistoryPage user={user} onSignIn={() => go("privacy")} onAnalyze={() => go("analyze")} notify={notify} ask={ask} />}
        {page === "alerts" && <AlertsPage user={user} onSignIn={() => go("privacy")} notify={notify} />}
        {page === "devices" && <DevicesPage user={user} onSignIn={() => go("privacy")} notify={notify} ask={ask} />}
        {page === "privacy" && <PrivacyPage user={user} setUser={setUser} consents={grantedConsents} setConsents={setGrantedConsents} authView={authView} setAuthView={setAuthView} authEmail={authEmail} setAuthEmail={setAuthEmail} authBusy={authBusy} setAuthBusy={setAuthBusy} authMessage={authMessage} setAuthMessage={setAuthMessage} authError={authError} setAuthError={setAuthError} recoveryCode={recoveryCode} setRecoveryCode={setRecoveryCode} recoveryPending={recoveryPending} setRecoveryPending={setRecoveryPending} onSignOut={signOut} ask={ask} />}
      </div>
      <footer className="global-disclaimer">{disclaimer}</footer>
    </main>
    {toastRegion}
    {dialog}
  </div>;
}

// ---------------------------------------------------------------------------
// Analyze
// ---------------------------------------------------------------------------
function AnalyzePage({ user, modelCard, notify, onConsent, go }: { user: User | null; modelCard: Record<string, number | string | null> | null; notify: Notify; onConsent: (consent: Consent) => void; go: (page: Page) => void }) {
  const [kind, setKind] = useState<"ecg" | "rr_csv">("ecg");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [jobId, setJobId] = useState("");
  const [jobCapability, setJobCapability] = useState<string | undefined>();
  const [saveThisAnalysis, setSaveThisAnalysis] = useState(false);
  const [savedResult, setSavedResult] = useState(false);
  const [consentText, setConsentText] = useState<ConsentText | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const resultRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = phase !== "idle";

  useEffect(() => { if (!user) { setSaveThisAnalysis(false); setConsentText(null); } }, [user]);
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const finish = (value: Result) => { setResult(value); setPhase("idle"); setJobId(""); notify("Analysis complete."); };
    const poll = async () => {
      try {
        const job = await getJob(jobId, jobCapability);
        if (cancelled) return;
        if (["finished", "complete"].includes(job.status) && job.result_id) finish(await getResult(job.result_id, jobCapability));
        else if (["finished", "complete"].includes(job.status) && job.result) finish(job.result);
        else if (job.status === "failed" || job.status === "expired") { setError(job.error ?? "Analysis could not be completed."); setPhase("idle"); setJobId(""); }
        else { setPhase(job.status === "started" ? "analyzing" : "queued"); window.setTimeout(poll, 850); }
      } catch (e) { if (!cancelled) { setError(message(e)); setPhase("idle"); setJobId(""); } }
    };
    const timer = window.setTimeout(poll, 500); return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId, jobCapability, notify]);
  useEffect(() => { if (result) resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [result]);

  function chooseFiles(list: File[]) {
    setError("");
    const allowed = kind === "ecg" ? [".hea", ".dat", ".csv", ".txt"] : [".csv"];
    const accepted = list.filter(file => allowed.some(ext => file.name.toLowerCase().endsWith(ext)));
    if (accepted.length < list.length) setError(`Unsupported file type. Use ${allowed.join(", ")}.`);
    const chosen = kind === "ecg" ? accepted.slice(0, 10) : accepted.slice(0, 1);
    if (chosen.reduce((sum, file) => sum + file.size, 0) > MAX_UPLOAD_BYTES) { setError("Combined upload size must be at most 100 MB."); return; }
    setFiles(chosen);
  }
  function onDrop(event: DragEvent) { event.preventDefault(); setDragging(false); if (!busy) chooseFiles(Array.from(event.dataTransfer.files)); }
  const names = files.map(f => f.name.toLowerCase());
  const wfdbIncomplete = kind === "ecg" && names.some(n => n.endsWith(".hea") || n.endsWith(".dat")) && !(names.some(n => n.endsWith(".hea")) && names.some(n => n.endsWith(".dat")));
  const verified = !!user?.email_verified_at;

  async function toggleSave(on: boolean) {
    setSaveThisAnalysis(on);
    if (!on) { setConsentText(null); return; }
    try {
      const [text, deviceList] = await Promise.all([api.get<ConsentText>("/privacy/consent"), api.get<{ items: Device[] }>("/devices").catch(() => ({ items: [] }))]);
      setConsentText(text); setDevices(deviceList.items ?? []);
    } catch (e) { setError(message(e)); setSaveThisAnalysis(false); }
  }
  async function analyze() {
    if (!files.length) return;
    if (saveThisAnalysis && (!user || !consentText)) { setError("Sign in and accept the current consent before saving this analysis."); return; }
    setError(""); setResult(null); setPhase("uploading");
    try {
      let consentId: string | undefined;
      if (saveThisAnalysis && consentText && user) {
        const consent = await api.post<Consent>("/privacy/consents", { text_version: consentText.text_version, scope: "save_analysis_results", affirmative: true });
        consentId = consent.id; onConsent(consent);
      }
      const job = await uploadFile(files, kind, saveThisAnalysis, consentId, saveThisAnalysis && deviceId ? deviceId : undefined);
      setSavedResult(saveThisAnalysis); setPhase("queued"); setJobId(job.job_id); setJobCapability(job.job_capability ?? undefined);
    } catch (e) { setError(e instanceof Error ? e.message : "Upload failed."); setPhase("idle"); }
  }
  function reset() { setResult(null); setFiles([]); setError(""); window.scrollTo({ top: 0, behavior: "smooth" }); }

  return <>
    <PageHeading eyebrow="OVERNIGHT SCREENING" title="Review a night of sleep" copy="Analyze anonymously, or sign in and explicitly opt in to save this result." aside={<div className="data-status"><span className="status-icon"><Check size={16} aria-hidden="true" /></span><span>Never saved by default</span></div>} />
    <section className="upload-card card" aria-busy={busy}>
      <div className="section-heading"><div><h2>New screening</h2><p>Choose a supported file type to begin.</p></div><span className="step-label"><span>01</span> Upload</span></div>
      <div className="source-tabs" role="tablist" aria-label="Recording source">
        <button role="tab" aria-selected={kind === "ecg"} className={kind === "ecg" ? "active" : ""} disabled={busy} onClick={() => { setKind("ecg"); setFiles([]); setError(""); }}><Activity size={17} aria-hidden="true" /> ECG recording <span className="validated">Research model</span></button>
        <button role="tab" aria-selected={kind === "rr_csv"} className={kind === "rr_csv" ? "active" : ""} disabled={busy} onClick={() => { setKind("rr_csv"); setFiles([]); setError(""); }}><Watch size={17} aria-hidden="true" /> RR intervals <span className="unvalidated">Unvalidated source</span></button>
      </div>
      <label className={`dropzone ${files.length ? "has-file" : ""} ${dragging ? "is-dragging" : ""}`} htmlFor="upload" tabIndex={0}
        onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputRef.current?.click(); } }}
        onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <input ref={inputRef} id="upload" type="file" disabled={busy} accept={kind === "ecg" ? ".hea,.dat,.csv,.txt" : ".csv"} multiple={kind === "ecg"} onChange={e => { chooseFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        <span className="upload-icon"><Upload size={21} aria-hidden="true" /></span>
        <strong>{dragging ? "Drop to add files" : files.length ? "Replace files" : "Drag files here or browse"}</strong>
        <span>{kind === "ecg" ? "WFDB .hea + .dat pair, or ECG samples in CSV" : "CSV columns: rr_ms and ts"}</span><small>Maximum combined upload size: 100 MB</small>
      </label>
      {files.length > 0 && <ul className="file-list" aria-label="Selected files">{files.map(file => <li key={file.name}><FileHeart size={16} aria-hidden="true" /><span>{file.name}</span><small>{formatBytes(file.size)}</small><button type="button" className="plain-icon" disabled={busy} aria-label={`Remove ${file.name}`} onClick={() => setFiles(list => list.filter(f => f !== file))}><X size={15} aria-hidden="true" /></button></li>)}</ul>}
      {wfdbIncomplete && <p className="field-hint" role="note">WFDB recordings need both the .hea header and the .dat signal file.</p>}
      {kind === "rr_csv" && <div className="source-warning"><span className="warning-mark" aria-hidden="true">!</span><div><strong>Watch and PPG intervals are unvalidated sources.</strong><span>ECG model performance measures do not apply to RR/PPG.</span></div></div>}
      {user && <div className={`save-optin${saveThisAnalysis ? " is-on" : ""}`}>
        <label><input type="checkbox" checked={saveThisAnalysis} disabled={busy || !verified} onChange={e => void toggleSave(e.target.checked)} /><span><strong>Save this analysis to my nights</strong>
          <small>{verified ? "Only this analysis will be saved. It will be evaluated for alerts only if saved with this consent." : "Verify your email address to save analyses. Check your inbox or resend the link from Privacy & settings."}</small></span></label>
        {saveThisAnalysis && <div className="consent-detail">
          {consentText ? <p className="consent-text">{consentText.text}</p> : <p className="consent-text">Loading the current consent…</p>}
          {devices.length > 0 && <label className="inline-field">Recorded with <select value={deviceId} onChange={e => setDeviceId(e.target.value)}><option value="">No device</option>{devices.map(d => <option key={d.id} value={d.id}>{d.label || sourceLabel(d.source)}</option>)}</select></label>}
        </div>}
      </div>}
      {error && <div className="error-box" role="alert"><WifiOff size={17} aria-hidden="true" /><span>{error}</span></div>}
      {busy && <ProgressSteps phase={phase} />}
      <div className="upload-footer"><div className="privacy-inline"><ShieldCheck size={17} aria-hidden="true" /><span>{saveThisAnalysis ? "This analysis is saved with your consent." : "Anonymous and temporary. No history is created."}</span></div>
        <button className="primary-button" disabled={!files.length || busy || wfdbIncomplete || (saveThisAnalysis && !consentText)} onClick={() => void analyze()}>{busy ? <><span className="spinner" aria-hidden="true" /> Analyzing…</> : <>Analyze recording <ChevronRight size={17} aria-hidden="true" /></>}</button></div>
    </section>
    <div ref={resultRef} className="result-anchor">
      {result ? <ResultPanel result={result} modelCard={modelCard} saved={savedResult} onReviewSaving={() => go(user ? "history" : "privacy")} onReset={reset} /> : <div className="lower-grid">
        <section className="card model-card"><div className="section-heading"><div><span className="eyebrow">MODEL CARD</span><h2>What this screening uses</h2></div><span className="model-icon"><Activity size={18} aria-hidden="true" /></span></div>
          <p className="model-description">A two-stage model trained and evaluated on single-lead ECG recordings. It was not evaluated on smartwatch or PPG-derived intervals.</p>
          {kind === "ecg" ? <div className="metrics-grid"><Metric label="AUC" value={fmt(modelCard?.auc, 3)} /><Metric label="Sensitivity" value={fmtPct(modelCard?.sensitivity)} /><Metric label="Specificity" value={fmtPct(modelCard?.specificity)} /></div>
            : <div className="source-warning"><span className="warning-mark" aria-hidden="true">!</span><div><strong>Wearable interval performance has not been evaluated.</strong><span>ECG model metrics are not displayed for this source.</span></div></div>}
          <div className="model-foot"><span>{kind === "ecg" ? `ECG model threshold ${fmt(modelCard?.threshold, 3)}` : "Threshold not validated for this source"}</span><span>AHI ≥ {fmt(modelCard?.ahi_threshold, 0)} · {fmt(modelCard?.patients, 0)} participants</span></div></section>
        <section className="card how-card"><span className="eyebrow">THE PROCESS</span><h2>From signal to summary</h2><ol className="process-list"><li><span>1</span><div><strong>Signal quality</strong><small>Check timing and usable intervals</small></div></li><li><span>2</span><div><strong>Minute-level analysis</strong><small>Estimate probability by minute</small></div></li><li><span>3</span><div><strong>Screening summary</strong><small>Review a research screening result</small></div></li></ol></section>
      </div>}
    </div>
    <div className="bottom-note"><span><ShieldCheck size={15} aria-hidden="true" /> Anonymous analysis remains available without an account.</span><button onClick={() => go("privacy")}>Privacy details <ChevronRight size={14} aria-hidden="true" /></button></div>
  </>;
}

function ProgressSteps({ phase }: { phase: Phase }) {
  const steps: [Phase, string][] = [["uploading", "Uploading"], ["queued", "Queued"], ["analyzing", "Analyzing"]];
  const current = steps.findIndex(([id]) => id === phase);
  return <ol className="progress-steps" aria-label="Analysis progress">{steps.map(([id, label], index) =>
    <li key={id} className={index < current ? "done" : index === current ? "current" : ""} aria-current={index === current ? "step" : undefined}><span aria-hidden="true">{index < current ? <Check size={12} /> : index + 1}</span>{label}</li>)}</ol>;
}

function ResultPanel({ result, modelCard, saved, onReviewSaving, onReset }: { result: Result; modelCard: Record<string, number | string | null> | null; saved: boolean; onReviewSaving: () => void; onReset: () => void }) {
  const points = result.charts?.probability_by_minute ?? result.per_minute_probabilities?.map((y, i) => ({ x: (i + 1) * 60, y })) ?? [];
  const percent = Math.round(result.apnea_burden_percent);
  const threshold = Number(modelCard?.threshold ?? 0.3032);
  const refer = result.referral_decision ? result.referral_decision === "Sleep study recommended" : result.score >= threshold;
  const realMode: ChartMode<ChartPoint[]> = { kind: "real", data: points };
  const quality = result.signal_quality;
  return <section className="result-wrap" aria-labelledby="result-title">
    <div className="result-heading"><div><span className="eyebrow">SCREENING SUMMARY</span><h2 id="result-title" tabIndex={-1}>Your recording has been analyzed</h2><p>Model {result.model_version} · {sourceLabel(result.source)}</p></div>
      <div className="inline-actions"><button className="secondary-button" onClick={() => window.print()}><ArrowDownToLine size={16} aria-hidden="true" /> Export summary</button>{saved ? <button className="saved-pill as-button" onClick={onReviewSaving}>Saved to nights <ChevronRight size={13} aria-hidden="true" /></button> : <button className="secondary-button" onClick={onReviewSaving}>Review saving options</button>}<button className="secondary-button" onClick={onReset}>Analyze another</button></div></div>
    {!result.source_validated && <div className="source-warning result-warning"><span className="warning-mark" aria-hidden="true">!</span><div><strong>Unvalidated source</strong><span>This result has not been validated against sleep-study outcomes. ECG performance metrics do not apply.</span></div></div>}
    <div className="result-grid"><section className={`card referral-card ${refer ? "is-refer" : "is-clear"}`}><span className="eyebrow">SCREENING OUTCOME</span><span className="outcome-icon"><Activity size={21} aria-hidden="true" /></span><h3>{result.referral_decision ?? (refer ? "Sleep study recommended" : "Not indicated by this screening")}</h3><p>This screening is not a diagnosis. Discuss concerns with a qualified healthcare professional.</p>
        <div className="threshold"><div><span>Model score</span><strong>{Math.round(result.score * 100)}%</strong></div><div className="threshold-track" role="img" aria-label={`Model score ${Math.round(result.score * 100)} percent; threshold ${Math.round(threshold * 100)} percent`}><i style={{ left: `${Math.min(100, threshold * 100)}%` }} /><b style={{ width: `${Math.min(100, Math.round(result.score * 100))}%` }} /></div><small>{result.source_validated ? `ECG model referral threshold: ${fmt(modelCard?.threshold, 3)}` : "Threshold has not been validated for this input source"}</small></div></section>
      <section className="card burden-card"><span className="eyebrow">APNEA BURDEN ESTIMATE</span><div className="burden-value">{percent}<span>%</span></div><p>Share of analyzed minutes flagged by the model</p><div className="burden-foot"><span>{points.length} minutes analyzed</span><span>{sourceLabel(result.source)}</span></div></section></div>
    <section className="card p2-card"><ProbabilityTimeline mode={realMode} sourceValidated={result.source_validated} modelVersion={result.model_version} /></section>
    <div className="result-grid"><section className="card quality-card"><span className="eyebrow">SIGNAL QUALITY</span>
        <div className="quality-row"><span>Usable beat ratio</span><strong>{quality.usable_beat_ratio == null ? "Not available" : `${Math.round(quality.usable_beat_ratio * 100)}%`}</strong></div>
        <div className="quality-row"><span>Detected gaps</span><strong>{quality.gap_count ?? "Not available"}</strong></div>
        <div className="quality-row"><span>Rejected artifacts</span><strong>{quality.rejected_artifact_count ?? "Not available"}</strong></div></section>
      <section className="card facts-card"><span className="eyebrow">SOURCE & MODEL</span><p>{result.source_validated ? "ECG research-model result." : "Unvalidated source. ECG performance metrics do not apply."}</p><span className="fact-disclaimer"><ShieldCheck size={15} aria-hidden="true" /> {disclaimer}</span></section></div>
  </section>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="metric"><strong>{value}</strong><span>{label}</span></div>; }
function fmt(value: unknown, digits: number) { return typeof value === "number" ? value.toFixed(digits) : "—"; }
function fmtPct(value: unknown) { return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—"; }

// ---------------------------------------------------------------------------
// Nights
// ---------------------------------------------------------------------------
type Ask = ReturnType<typeof useDialog>["ask"];

function HistoryPage({ user, onSignIn, onAnalyze, notify, ask }: { user: User | null; onSignIn: () => void; onAnalyze: () => void; notify: Notify; ask: Ask }) {
  const [nights, setNights] = useState<NightSummary[]>([]); const [source, setSource] = useState(""); const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const [detail, setDetail] = useState<NightDetail | null>(null); const [selected, setSelected] = useState<string[]>([]); const [comparison, setComparison] = useState<NightDetail[] | null>(null); const [resolution, setResolution] = useState<"downsampled" | "full">("downsampled");
  const [busy, setBusy] = useState(false); const [loaded, setLoaded] = useState(false); const [error, setError] = useState(""); const [demo, setDemo] = useState(false);
  const [deviceLabels, setDeviceLabels] = useState<Record<string, string>>({});
  const detailRef = useRef<HTMLDivElement>(null);
  const load = useCallback(async () => {
    if (!user) return;
    setBusy(true); setError("");
    try {
      const q = new URLSearchParams();
      if (from) q.set("from", new Date(`${from}T00:00:00`).toISOString());
      if (to) q.set("to", new Date(`${to}T23:59:59`).toISOString());
      if (source) q.set("source", source);
      const items: NightSummary[] = []; let cursor: string | null = null;
      do {
        if (cursor) q.set("cursor", cursor);
        const data: { items: NightSummary[]; next_cursor?: string | null } = await api.get(`/nights?${q}`);
        items.push(...data.items); cursor = data.next_cursor ?? null;
      } while (cursor);
      setNights(items); setSelected(old => old.filter(id => items.some(n => n.id === id)));
    } catch (e) { setError(message(e)); } finally { setBusy(false); setLoaded(true); }
  }, [user, from, to, source]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!user) return;
    api.get<{ items: Device[] }>("/devices").then(d => setDeviceLabels(Object.fromEntries((d.items ?? []).map(x => [x.id, x.label || sourceLabel(x.source)])))).catch(() => setDeviceLabels({}));
  }, [user]);
  useEffect(() => { if (detail || comparison) detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [detail?.id, comparison]);
  async function openNight(id: string, mode = resolution) { setComparison(null); setError(""); try { setResolution(mode); setDetail(await api.get<NightDetail>(`/nights/${encodeURIComponent(id)}?resolution=${mode}&max_points=1000`)); } catch (e) { setError(message(e)); } }
  async function deleteNight(id: string) {
    const confirmed = await ask("Delete this night?", <p>The saved result and its chart data are removed permanently. This cannot be undone.</p>, [{ label: "Delete night", value: true, tone: "danger" }]);
    if (!confirmed) return;
    try { await api.delete<void>(`/nights/${encodeURIComponent(id)}`); setDetail(null); setNights(n => n.filter(x => x.id !== id)); setSelected(s => s.filter(x => x !== id)); notify("Night deleted."); } catch (e) { setError(message(e)); }
  }
  async function compare() { if (selected.length < 2) return; try { const q = new URLSearchParams({ night_ids: selected.join(","), resolution: "downsampled", max_points: "1000" }); const data = await api.get<{ items: NightDetail[] }>(`/nights/compare?${q}`); setComparison(data.items); setDetail(null); } catch (e) { setError(message(e)); } }
  if (!user) return <SignedOut title="Nights" copy="Sign in to review nights you explicitly chose to save." onSignIn={onSignIn} />;
  const filtered = !!(from || to || source);
  const comparisonMode: ChartMode<NightDetail[]> = demo ? syntheticDemoFixtures.demoComparison : { kind: "real", data: comparison ?? [] };
  const detailSeries = detail?.charts.probability_by_minute ?? [];
  const detailIntervals = detail?.charts.interval_tachogram ?? [];
  const gaps: QualityGap[] = readGaps(detail?.signal_quality);
  return <section className="p2-page"><PageHeading eyebrow="SAVED WITH CONSENT" title="Nights" copy="Only analyses saved with your per-analysis consent appear here." />
    {error && <div className="error-box" role="alert">{error}</div>}
    <div className="card p2-toolbar" role="search" aria-label="Filter nights">
      <label>From <input type="date" value={from} max={to || undefined} onChange={e => setFrom(e.target.value)} /></label>
      <label>To <input type="date" value={to} min={from || undefined} onChange={e => setTo(e.target.value)} /></label>
      <label>Source <select value={source} onChange={e => setSource(e.target.value)}><option value="">All sources</option><option value="ecg">ECG</option><option value="watch_rr">Watch RR</option><option value="ppg_rr">PPG RR</option></select></label>
      {filtered && <button className="secondary-button" onClick={() => { setFrom(""); setTo(""); setSource(""); }}>Reset filters</button>}
      <span className="toolbar-status" aria-live="polite">{busy ? <><span className="spinner dark-spinner" aria-hidden="true" /> Loading…</> : ""}</span>
      <DemoDataToggle realData={nights} demoData={syntheticDemoFixtures.demoBurden.data} enabled={demo} onChange={on => { setDemo(on); setDetail(null); setComparison(null); }} />
    </div>
    {demo && <p className="p2-demo-disclosure">Synthetic demo data — not patient data. Demo and account results are never combined.</p>}
    {(demo || nights.length > 0) && <div className="card p2-card"><NightlyBurden mode={demo ? syntheticDemoFixtures.demoBurden : { kind: "real", data: nights }} /></div>}
    {!demo && <div className="card p2-list-card"><div className="section-heading"><div><h2>Saved nights</h2><p>{loaded ? `${nights.length} night${nights.length === 1 ? "" : "s"} ${filtered ? "match these filters" : "saved"}` : "Loading saved nights…"}</p></div>
        <div className="inline-actions">{selected.length > 0 && <button className="secondary-button" onClick={() => setSelected([])}>Clear selection</button>}<button className="secondary-button" disabled={selected.length < 2} onClick={() => void compare()}>Compare selected ({selected.length})</button></div></div>
      {!loaded ? <div className="skeleton-list" aria-hidden="true"><span /><span /><span /></div>
        : nights.length === 0 ? <div className="empty-inline empty-state"><Moon size={22} aria-hidden="true" /><p>{filtered ? "No saved nights match these filters." : "No saved nights yet. Turn on “Save this analysis to my nights” when you analyze a recording."}</p>{filtered ? <button className="secondary-button" onClick={() => { setFrom(""); setTo(""); setSource(""); }}>Reset filters</button> : <button className="primary-button" onClick={onAnalyze}>Analyze a recording</button>}</div>
        : <div className="p2-night-list">{nights.map(n => <article key={n.id} className={`p2-night-row${detail?.id === n.id ? " is-open" : ""}`}>
          <label><input aria-label={`Select ${new Date(n.started_at).toLocaleDateString()} for comparison`} type="checkbox" checked={selected.includes(n.id)} onChange={e => setSelected(old => e.target.checked ? [...old, n.id] : old.filter(id => id !== n.id))} /></label>
          <div className="p2-night-main"><strong>{new Date(n.started_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</strong><small>{sourceLabel(n.source)}{n.device_id ? ` · ${deviceLabels[n.device_id] ?? "Device"}` : ""} · model {n.model_version}</small></div>
          <span className="night-burden" title="Apnea burden estimate">{n.apnea_burden_percent == null ? "—" : `${n.apnea_burden_percent.toFixed(0)}%`}</span>
          <span className={n.source_validated ? "validated" : "unvalidated"}>{n.source_validated ? "Validated source" : "Unvalidated source"}</span>
          <button className="secondary-button" aria-expanded={detail?.id === n.id} onClick={() => detail?.id === n.id ? setDetail(null) : void openNight(n.id)}>{detail?.id === n.id ? "Hide" : "Details"}</button></article>)}</div>}
    </div>}
    <div ref={detailRef} className="result-anchor">
      {(comparison || demo) && <div className="card p2-card"><SourceDeviceComparison mode={comparisonMode} deviceLabels={deviceLabels} /></div>}
      {demo && <DemoNightDetail />}
      {detail && !demo && <div className="p2-detail"><div className="section-heading"><div><span className="eyebrow">NIGHT DETAIL</span><h2>{new Date(detail.started_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</h2><p>{sourceLabel(detail.source)}{detail.device_id ? ` · ${deviceLabels[detail.device_id] ?? "Device"}` : ""} · model {detail.model_version} · {detail.source_validated ? "Validated source" : "Unvalidated source"}</p>
          <label className="resolution-select">Chart resolution <select value={resolution} onChange={e => void openNight(detail.id, e.target.value as "downsampled" | "full")}><option value="downsampled">Downsampled (max 1,000 points)</option><option value="full">Full (max 50,000 points)</option></select></label></div>
          <div className="inline-actions"><button className="secondary-button" onClick={() => setDetail(null)}><X size={15} aria-hidden="true" /> Close</button><button className="secondary-button danger-text" onClick={() => void deleteNight(detail.id)}><Trash2 size={15} aria-hidden="true" /> Delete night</button></div></div>
        {!detail.source_validated && <div className="source-warning"><span className="warning-mark" aria-hidden="true">!</span><div><strong>Unvalidated source</strong><span>ECG model performance metrics do not apply.</span></div></div>}
        {!!detailSeries.length && <div className="card p2-card"><ProbabilityTimeline mode={{ kind: "real", data: detailSeries }} sourceValidated={detail.source_validated} modelVersion={detail.model_version} /></div>}
        {detailIntervals.length > 0 && <div className="card p2-card"><RRTachogram mode={{ kind: "real", data: detailIntervals }} rejected={detail.charts.rejected_artifacts ?? []} gaps={gaps} sourceValidated={detail.source_validated} modelVersion={detail.model_version} /></div>}
        <div className="card p2-card"><SignalQualityStrip mode={{ kind: "real", data: quality(detail.signal_quality) }} durationSeconds={detail.duration_seconds} gaps={gaps} /></div><p className="p2-disclaimer">{detail.disclaimer}</p>
      </div>}
    </div>
  </section>;
}

function DemoNightDetail() {
  const { demoProbability, demoIntervals, demoRejected, demoQuality } = syntheticDemoFixtures;
  const demoGaps: QualityGap[] = [{ start_x: 24, end_x: 27, label: "Synthetic gap" }];
  return <div className="p2-detail"><div className="section-heading"><div><span className="eyebrow">DEMO NIGHT DETAIL</span><h2>Synthetic example night</h2><p>Illustrates the charts available for a saved night.</p></div></div>
    <div className="card p2-card"><ProbabilityTimeline mode={demoProbability} sourceValidated modelVersion="synthetic-demo" /></div>
    <div className="card p2-card"><RRTachogram mode={demoIntervals} rejected={demoRejected} gaps={demoGaps} sourceValidated={false} modelVersion="synthetic-demo" /></div>
    <div className="card p2-card"><SignalQualityStrip mode={demoQuality} durationSeconds={40} gaps={demoGaps} /></div>
  </div>;
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------
function AlertsPage({ user, onSignIn, notify }: { user: User | null; onSignIn: () => void; notify: Notify }) {
  const [rules, setRules] = useState<AlertRule[]>([]); const [savedRules, setSavedRules] = useState(""); const [alerts, setAlerts] = useState<Alert[]>([]);
  const [quiet, setQuiet] = useState({ start: "22:00", end: "07:00" }); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [pushId, setPushId] = useState(() => readStored("nightwise-push-subscription-id"));
  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [r, a] = await Promise.all([api.get<any>("/alerts/rules"), api.get<any>("/alerts")]);
      const loadedRules = r.rules ?? r.items ?? []; const loadedQuiet = r.quiet_hours ?? { start: "22:00", end: "07:00" };
      setRules(loadedRules); setQuiet(loadedQuiet); setSavedRules(JSON.stringify({ rules: loadedRules, quiet: loadedQuiet })); setAlerts(a.items ?? []);
    } catch (e) { setError(message(e)); }
  }, [user]);
  useEffect(() => { void load(); }, [load]);
  if (!user) return <SignedOut title="Alerts" copy="Sign in to configure alerts for saved nights." onSignIn={onSignIn} />;
  const dirty = JSON.stringify({ rules, quiet }) !== savedRules;
  const missingChannel = rules.filter(rule => rule.channels.length === 0);
  function changeRule(kind: AlertRule["kind"], patch: Partial<AlertRule>) { setRules(old => old.map(rule => rule.kind === kind ? { ...rule, ...patch } : rule)); }
  async function save() {
    if (missingChannel.length) { setError("Each alert rule needs at least one delivery channel."); return; }
    setBusy(true); setError("");
    try { const response = await api.put<any>("/alerts/rules", { rules, quiet_hours: quiet }); const next = response.rules ?? rules; setRules(next); setSavedRules(JSON.stringify({ rules: next, quiet })); notify("Alert settings saved."); }
    catch (e) { setError(message(e)); } finally { setBusy(false); }
  }
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
      writeStored("nightwise-push-subscription-id", created.id); setPushId(created.id);
      notify("Web Push is connected. Notifications are generic; sign in to view details.");
    } catch (e) { setError(message(e)); }
  }
  async function disconnectPush() {
    if (!pushId) return;
    try {
      await api.delete<void>(`/alerts/web-push/subscriptions/${encodeURIComponent(pushId)}`);
      const registration = await navigator.serviceWorker.getRegistration("/"); await registration?.pushManager.getSubscription().then(s => s?.unsubscribe());
      writeStored("nightwise-push-subscription-id", null); setPushId(null); notify("Web Push disconnected.");
    } catch (e) { setError(message(e)); }
  }
  const labels: Record<AlertRule["kind"], string> = { screening_result: "Saved screening complete", worsening_trend: "Worsening trend", morning_summary: "Morning summary", device_silent_overnight: "Device silent overnight" };
  const descriptions: Record<AlertRule["kind"], string> = {
    worsening_trend: "At least 6 saved nights in 30 days; compare newest 3-night mean with preceding 3-night mean and alert at +10 percentage points.",
    morning_summary: `One summary at 07:00 ${user.timezone} for the previous local calendar day.`,
    device_silent_overnight: "Needs a live device; planned for Phase 3.",
    screening_result: "One generic notification after a saved analysis completes.",
  };
  return <section className="p2-page"><PageHeading eyebrow="SAVED NIGHTS ONLY" title="Alerts" copy="Alerts only apply to nights saved with explicit consent. Notification text is generic; sign in to view details." />{error && <div className="error-box" role="alert">{error}</div>}
    <div className="card p2-list-card"><div className="section-heading"><div><h2>Alert rules</h2><p>Quiet hours and morning summaries use {user.timezone}.</p></div>
        <div className="inline-actions">{pushId ? <><span className="saved-pill"><Check size={12} aria-hidden="true" /> Web Push connected</span><button className="secondary-button" onClick={() => void disconnectPush()}>Disconnect Push</button></> : <button className="secondary-button" onClick={() => void connectPush()}>Connect Web Push</button>}</div></div>
      <div className="p2-rule-list">{rules.map(rule => {
        const unavailable = rule.kind === "device_silent_overnight" || rule.availability === "needs_live_device";
        return <fieldset className={`p2-rule${unavailable ? " is-unavailable" : ""}`} key={rule.kind}><legend className="sr-only">{labels[rule.kind]}</legend>
          <div className="p2-rule-copy"><strong>{labels[rule.kind]}</strong><small>{descriptions[rule.kind]}</small>{rule.kind === "device_silent_overnight" && <span className="muted-pill">Needs live device · disabled until Phase 3</span>}</div>
          <label className="switch-line"><input type="checkbox" role="switch" checked={rule.enabled} disabled={unavailable} onChange={e => changeRule(rule.kind, { enabled: e.target.checked })} /><span>{rule.enabled ? "On" : "Off"}</span></label>
          <div className="p2-channel-list">{(["email", "web_push"] as const).map(channel => <label key={channel}><input type="checkbox" checked={rule.channels.includes(channel)} disabled={unavailable || (channel === "email" && !user.email_verified_at)} onChange={e => changeRule(rule.kind, { channels: e.target.checked ? [...rule.channels, channel] : rule.channels.filter(x => x !== channel) })} />{channel === "email" ? `Email${user.email_verified_at ? "" : " (verify email first)"}` : "Web Push"}</label>)}
            {rule.channels.length === 0 && <small className="field-error" role="alert">Choose at least one channel.</small>}</div>
        </fieldset>;
      })}</div>
      <div className="p2-quiet-hours"><strong>Quiet hours ({user.timezone})</strong><label>Start <input type="time" value={quiet.start} onChange={e => setQuiet({ ...quiet, start: e.target.value })} /></label><label>End <input type="time" value={quiet.end} onChange={e => setQuiet({ ...quiet, end: e.target.value })} /></label></div>
      <div className="save-row"><button className="primary-button" onClick={() => void save()} disabled={busy || !dirty || missingChannel.length > 0}>{busy ? "Saving…" : "Save alert settings"}</button>{dirty && <span className="unsaved-note">Unsaved changes</span>}</div>
      <p className="p2-note">Push and email notifications contain no score or health values. They include the research disclaimer and “Unvalidated source” where relevant. Details are available only after login.</p>
    </div>
    <div className="card p2-list-card"><h2>Recent alerts</h2>{alerts.length ? alerts.map(alert => <article className="p2-alert-row" key={alert.id}><Bell size={16} aria-hidden="true" /><div><strong>{labels[alert.kind as AlertRule["kind"]] ?? "Screening alert"}</strong><small>{new Date(alert.created_at).toLocaleString()} · <span className={`status-text status-${alert.status}`}>{alert.status.replaceAll("_", " ")}</span>{alert.source_validated === false ? " · Unvalidated source" : ""}</small>{alert.details_available && <small>Account-only details are shown here after login.</small>}</div><span className="saved-pill">Private detail</span></article>) : <p className="empty-inline">No alerts yet. Only consented saved nights can produce alerts.</p>}</div>
  </section>;
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------
function DevicesPage({ user, onSignIn, notify, ask }: { user: User | null; onSignIn: () => void; notify: Notify; ask: Ask }) {
  const [devices, setDevices] = useState<Device[] | null>(null); const [label, setLabel] = useState(""); const [source, setSource] = useState("watch_rr"); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(() => { if (user) api.get<any>("/devices").then(d => setDevices(d.items ?? [])).catch(e => setError(message(e))); }, [user]); useEffect(() => load(), [load]);
  if (!user) return <SignedOut title="Devices" copy="Sign in to manage account-associated devices." onSignIn={onSignIn} />;
  async function add() { setBusy(true); setError(""); try { await api.post("/devices", { source, label: label.trim() || null }); setLabel(""); load(); notify("Device added."); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  async function remove(device: Device) {
    const confirmed = await ask("Remove this device?", <p>“{device.label || sourceLabel(device.source)}” will no longer be offered when saving analyses. Nights already saved keep their results.</p>, [{ label: "Remove device", value: true, tone: "danger" }]);
    if (!confirmed) return;
    try { await api.delete(`/devices/${encodeURIComponent(device.id)}`); load(); notify("Device removed."); } catch (e) { setError(message(e)); }
  }
  return <section className="p2-page"><PageHeading eyebrow="ACCOUNT DEVICES" title="Devices" copy="Devices label saved analyses: choose one under “Save this analysis” on the Analyze page. Live pairing and overnight presence are planned for Phase 3." />{error && <div className="error-box" role="alert">{error}</div>}
    <div className="card p2-list-card"><h2>Add a device record</h2>
      <form className="p2-toolbar" onSubmit={e => { e.preventDefault(); void add(); }}><label>Source <select value={source} onChange={e => setSource(e.target.value)}><option value="watch_rr">Watch RR</option><option value="ppg_rr">PPG RR</option><option value="ecg">ECG</option></select></label><label>Label <input value={label} maxLength={80} onChange={e => setLabel(e.target.value)} placeholder="e.g. Left-wrist watch" /></label><button className="primary-button" type="submit" disabled={busy}>{busy ? "Adding…" : "Add device"}</button></form>
      {devices === null ? <div className="skeleton-list" aria-hidden="true"><span /><span /></div> : devices.length ? devices.map(device => <article className="p2-night-row" key={device.id}><Watch size={17} aria-hidden="true" /><div className="p2-night-main"><strong>{device.label || "Device"}</strong><small>{sourceLabel(device.source)}</small></div><button className="secondary-button" onClick={() => void remove(device)}>Remove</button></article>) : <p className="empty-inline">No devices added yet.</p>}
    </div></section>;
}

// ---------------------------------------------------------------------------
// Privacy & account
// ---------------------------------------------------------------------------
function PrivacyPage(props: { user: User | null; setUser: (user: User | null) => void; consents: Consent[]; setConsents: (consents: Consent[]) => void; authView: AuthView; setAuthView: (v: AuthView) => void; authEmail: string; setAuthEmail: (v: string) => void; authBusy: boolean; setAuthBusy: (v: boolean) => void; authMessage: string; setAuthMessage: (v: string) => void; authError: string; setAuthError: (v: string) => void; recoveryCode: string; setRecoveryCode: (v: string) => void; recoveryPending: boolean; setRecoveryPending: (v: boolean) => void; onSignOut: () => Promise<void>; ask: Ask }) {
  const { user, setUser, consents, setConsents, authView, setAuthView, authEmail, setAuthEmail, authBusy, setAuthBusy, authMessage, setAuthMessage, authError, setAuthError, recoveryCode, setRecoveryCode, recoveryPending, setRecoveryPending, ask } = props;
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
  async function exportData() { setAuthError(""); setBusy(true); try { const token = await reauth("export"); const data = await api.post<any>("/me/data/export", undefined, { "X-Reauth-Token": token }); const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "nightwise-export.json"; a.click(); URL.revokeObjectURL(url); setAuthMessage("Export downloaded."); } catch (e) { setAuthError(message(e)); } finally { setBusy(false); } }
  async function deleteAccount() {
    if (deleteText !== "DELETE MY DATA") { setAuthError("Type DELETE MY DATA to confirm."); return; }
    const confirmed = await ask("Permanently delete your account?", <p>All saved nights, consents, devices and alert settings are deleted. You will be asked to confirm with your passkey. This cannot be undone.</p>, [{ label: "Delete everything", value: true, tone: "danger" }]);
    if (!confirmed) return;
    setBusy(true); setAuthError(""); try { const token = await reauth("delete"); await api.delete<void>("/me/data", { confirmation: deleteText }, { "X-Reauth-Token": token }); setUser(null); setConsents([]); setDeleteText(""); setAuthMessage("Account deletion has started."); } catch (e) { setAuthError(message(e)); } finally { setBusy(false); }
  }
  async function replaceCodes() { setAuthError(""); setBusy(true); try { const token = await reauth("recovery_codes"); const data = await api.post<{ codes: string[] }>("/auth/recovery-codes", undefined, { "X-Reauth-Token": token }); setNewCodes(data.codes); } catch (e) { setAuthError(message(e)); } finally { setBusy(false); } }
  async function saveTimezone() { try { const updated = await api.patch<User>("/settings", { timezone }); setUser(updated); setAuthMessage("Timezone saved."); } catch (e) { setAuthError(message(e)); } }
  async function revokeConsent(id: string) {
    // A real three-way choice: the old confirm() treated Cancel as "keep nights" and offered no way out.
    const choice = await ask("Revoke saved-analysis consent?", <p>Future analyses will no longer be saved under this consent. Choose what happens to nights already saved with it.</p>, [
      { label: "Keep saved nights", value: "keep", tone: "secondary" }, { label: "Delete saved nights", value: "delete", tone: "danger" }]);
    if (!choice) return;
    const remove = choice === "delete";
    try { await api.delete<void>(`/privacy/consents/${encodeURIComponent(id)}`, { delete_saved_nights: remove }); setConsents(consents.map(c => c.id === id ? { ...c, revoked_at: new Date().toISOString() } : c)); setAuthMessage(remove ? "Consent revoked and saved nights deleted." : "Consent revoked. Existing saved nights were retained."); } catch (e) { setAuthError(message(e)); }
  }
  async function resend() { try { await api.post<void>("/auth/email/verification/resend"); setAuthMessage("If verification is needed, an email has been sent."); } catch (e) { setAuthError(message(e)); } }
  async function copyCodes() { try { await navigator.clipboard.writeText(newCodes.join("\n")); setAuthMessage("Recovery codes copied."); } catch { setAuthError("Copy failed. Select the codes and copy them manually."); } }
  const authLabel = authView === "register" ? "Register with a passkey" : authView === "recovery" ? "Use recovery code" : "Continue with passkey";
  return <section className="p2-page"><PageHeading eyebrow="PRIVACY & ACCOUNT" title={user ? "Account settings" : "Sign in or create an account"} copy="Anonymous screening is always available. An account is needed only for saved results and account features." />
    {authError && <div className="error-box" role="alert">{authError}</div>}{authMessage && <div className="notice-box" role="status">{authMessage}</div>}
    {!user ? (recoveryPending ? <div className="card p2-list-card"><h2>Restore account access</h2><p>Your recovery code created a restricted session. Add a passkey to finish recovery and enable normal sign-in.</p><div className="auth-form"><label>Passkey label<input value={deviceLabel} onChange={e => setDeviceLabel(e.target.value)} placeholder="This device" /></label><button className="primary-button" disabled={authBusy} onClick={() => void finishRecovery()}>{authBusy ? "Working…" : "Add passkey and restore access"}</button></div></div>
      : <div className="card p2-list-card"><div className="auth-tabs" role="tablist" aria-label="Account access">{([["login", "Sign in"], ["register", "Create account"], ["recovery", "Recovery"]] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={authView === id} className={authView === id ? "active" : ""} onClick={() => { setAuthView(id); setAuthError(""); }}>{label}</button>)}</div>
        <form className="auth-form" onSubmit={e => { e.preventDefault(); if (authEmail && !authBusy) void runAuth(); }}>
          <label>Email<input type="email" required autoComplete={authView === "login" ? "username webauthn" : "email"} value={authEmail} onChange={e => setAuthEmail(e.target.value)} /></label>
          {authView === "register" && <label>Timezone (IANA)<input value={timezone} onChange={e => setTimezone(e.target.value)} placeholder="Africa/Nairobi" /></label>}
          {authView === "recovery" && <label>One-time recovery code<input autoComplete="one-time-code" required value={recoveryCode} onChange={e => setRecoveryCode(e.target.value)} /></label>}
          <button type="submit" className="primary-button" disabled={authBusy || !authEmail}>{authBusy ? <><span className="spinner" aria-hidden="true" /> Working…</> : authLabel}</button>
          <p className="p2-note">{authView === "login" ? "If this account exists, your passkey prompt will continue. Account lookup responses are the same either way." : authView === "register" ? "Existing email addresses cannot be registered again. Verify your email before using email alerts." : "A recovery code is single-use. After recovery, add a passkey to restore full access."}</p></form></div>) : <>
      <div className="card p2-list-card"><div className="section-heading"><div><h2>Account</h2><p>{user.email}</p></div><button className="secondary-button" onClick={() => void props.onSignOut()}><LogOut size={15} aria-hidden="true" /> Sign out</button></div>
        <p className={user.email_verified_at ? "verified-line" : "unverified-line"}>{user.email_verified_at ? <><CircleCheck size={15} aria-hidden="true" /> Email verified</> : "Email not verified — email alerts and saving stay disabled until verified."}</p>{!user.email_verified_at && <button className="secondary-button" onClick={() => void resend()}>Resend verification link</button>}
        <div className="p2-toolbar"><label>Timezone (IANA)<input value={timezone} onChange={e => setTimezone(e.target.value)} /></label><button className="secondary-button" disabled={timezone === user.timezone} onClick={() => void saveTimezone()}>Save timezone</button></div></div>
      <div className="card p2-list-card"><h2>Passkeys and recovery</h2><div className="p2-toolbar"><label>Passkey label<input value={deviceLabel} onChange={e => setDeviceLabel(e.target.value)} placeholder="This device" /></label><button className="secondary-button" disabled={authBusy} onClick={() => void passkeyRegister()}>Add passkey</button><button className="secondary-button" disabled={busy} onClick={() => void replaceCodes()}>Create recovery codes</button></div>
        {newCodes.length > 0 && <div className="recovery-codes"><strong>Shown once. Save these codes securely.</strong><code>{newCodes.join("\n")}</code><div className="inline-actions"><button className="secondary-button" onClick={() => void copyCodes()}>Copy codes</button><button className="secondary-button" onClick={() => setNewCodes([])}>Hide codes</button></div></div>}</div>
      <ConsentSettings consents={consents} onRevoke={revokeConsent} />
      <div className="card p2-list-card"><h2>Your data</h2><p>Export and account deletion require a fresh passkey check. Deletion removes live account data and keys; backup copies expire according to the service retention policy.</p><div className="p2-toolbar"><button className="secondary-button" disabled={busy} onClick={() => void exportData()}><ArrowDownToLine size={15} aria-hidden="true" /> Export JSON</button></div>
        <div className="danger-zone"><strong>Delete account and all saved data</strong><label>Type DELETE MY DATA<input value={deleteText} autoComplete="off" onChange={e => setDeleteText(e.target.value)} /></label><button className="danger-button" disabled={busy || deleteText !== "DELETE MY DATA"} onClick={() => void deleteAccount()}><Trash2 size={15} aria-hidden="true" /> Permanently delete</button></div></div>
    </>}
    <div className="card p2-list-card"><h2>Privacy details</h2><ul className="p2-privacy-list"><li>Anonymous analyses are temporary; nothing is saved without explicit per-analysis consent.</li><li>Logs contain request identifiers and operational status only, never email addresses, tokens, uploads, or health payloads.</li><li>Redis is private, password protected, JSON-serialized, and configured without disk persistence. Uploads stay in encrypted short-lived temp blobs on a dedicated volume and are removed after processing or expiry.</li><li>Backups are retained for 30 days. A hard delete removes live rows and wrapped data keys immediately; backup copies expire within 30 days and are not restored without replaying deletion tombstones.</li><li>Notification text is generic and always carries the research disclaimer. Alert details require login.</li></ul></div>
  </section>;
}

function ConsentSettings({ consents, onRevoke }: { consents: Consent[]; onRevoke: (id: string) => void }) {
  const active = consents.filter(c => !c.revoked_at), revoked = consents.filter(c => c.revoked_at);
  const row = (c: Consent) => <div className={`p2-night-row${c.revoked_at ? " is-revoked" : ""}`} key={c.id}><div className="p2-night-main"><strong>{c.scope.replaceAll("_", " ")}</strong><small>Version {c.text_version} · granted {new Date(c.granted_at).toLocaleString()}{c.revoked_at ? ` · revoked ${new Date(c.revoked_at).toLocaleString()}` : ""}</small></div>{c.revoked_at ? <span className="muted-pill">Revoked</span> : <button className="secondary-button" onClick={() => onRevoke(c.id)}>Revoke consent</button>}</div>;
  return <div className="card p2-list-card"><h2>Saved-analysis consent</h2><p>Revoke consent to stop future saving. You choose whether to delete nights saved under that consent.</p>
    {consents.length === 0 ? <p className="empty-inline">No consents yet. A consent is recorded each time you choose to save an analysis.</p> : <>
      {active.length ? active.map(row) : <p className="empty-inline">No active consents.</p>}
      {revoked.length > 0 && <details className="revoked-list"><summary>Revoked consents ({revoked.length})</summary>{revoked.map(row)}</details>}
    </>}
  </div>;
}

function SignedOut({ title, copy, onSignIn }: { title: string; copy: string; onSignIn: () => void }) { return <section className="p2-page"><PageHeading eyebrow="ACCOUNT REQUIRED" title={title} copy={copy} /><div className="card p2-list-card"><p>Anonymous analyses remain available on the Analyze page. Sign-in is required only for saved history and account features.</p><button className="primary-button" onClick={onSignIn}>Sign in</button></div></section>; }
function PageHeading({ eyebrow, title, copy, aside }: { eyebrow: string; title: string; copy: string; aside?: ReactNode }) { return <div className="page-intro"><div><span className="eyebrow">{eyebrow}</span><h1 tabIndex={-1}>{title}</h1><p>{copy}</p></div>{aside}</div>; }
function message(e: unknown) { return e instanceof Error ? e.message : "The request could not be completed."; }
function sourceLabel(source: string) { return ({ ecg: "ECG", watch_rr: "Watch RR", ppg_rr: "PPG RR" } as Record<string, string>)[source] ?? source; }
function formatBytes(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`; }
function readGaps(value?: Record<string, unknown>): QualityGap[] { const raw = value?.gaps; return Array.isArray(raw) ? raw.filter((g): g is QualityGap => !!g && typeof g === "object" && typeof (g as any).start_x === "number" && typeof (g as any).end_x === "number") : []; }
function quality(value?: Record<string, unknown>) { return { usable_beat_ratio: numberOrNull(value?.usable_beat_ratio), gap_count: numberOrNull(value?.gap_count), usable_beats: numberOrNull(value?.usable_beats), rejected_artifact_count: numberOrNull(value?.rejected_artifact_count) }; }
function numberOrNull(value: unknown): number | null { return typeof value === "number" ? value : null; }
