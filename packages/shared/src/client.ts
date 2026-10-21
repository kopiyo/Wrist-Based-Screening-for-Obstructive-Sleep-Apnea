import type { paths } from "./openapi";

const buildEnv = (import.meta as ImportMeta & { env?: { VITE_API_URL?: string } }).env;
export const API_BASE = (buildEnv?.VITE_API_URL ?? "http://localhost:8000/v1").replace(/\/$/, "");
type ApiPath = keyof paths;
const routes = {
  upload: "/v1/analyze/file",
  job: "/v1/jobs/{job_id}",
  result: "/v1/results/{result_id}",
  modelCard: "/v1/model-card",
} as const satisfies Record<string, ApiPath>;

export type Job = { job_id: string; job_capability?: string | null; status?: string };
export type Result = {
  score: number;
  apnea_burden_percent: number;
  per_minute_probabilities?: number[];
  charts?: { probability_by_minute?: ChartPoint[]; interval_tachogram?: ChartPoint[] | null; rejected_artifacts?: ChartPoint[] | null };
  signal_quality: { usable_beat_ratio?: number | null; gap_count?: number; gap_seconds?: number; usable_beats?: number; rejected_artifact_count?: number; gaps?: { start_x: number; end_x: number }[] };
  referral_decision?: "Sleep study recommended" | "Not indicated by this screening";
  source_validated: boolean;
  model_version: string;
  source: string;
  source_badge?: string;
  disclaimer: string;
};
export type User = { id: string; email: string; email_verified_at: string | null; timezone: string };
export type Consent = { id: string; text_version: string; granted_at: string; scope: "save_analysis_results"; revoked_at?: string | null };
export type NightSummary = {
  id: string; started_at: string; source: "ecg" | "watch_rr" | "ppg_rr";
  source_validated: boolean; model_version: string; score?: number | null; apnea_burden_percent?: number | null;
  device_id?: string | null;
};
export type ChartPoint = { x: number; y: number; flagged?: boolean | null };
export type NightDetail = NightSummary & {
  duration_seconds: number; charts: { probability_by_minute?: ChartPoint[]; interval_tachogram?: ChartPoint[]; rejected_artifacts?: ChartPoint[] };
  signal_quality?: Record<string, unknown>; disclaimer: string;
};
export type AlertRule = { kind: string; enabled: boolean; channels: ("email" | "web_push")[]; availability?: string; disabled_reason?: string };
export type Alert = { id: string; kind: string; created_at: string; status: string; source_validated?: boolean; model_version?: string; details_available?: boolean };
export type Device = { id: string; source: string; label?: string | null };

let csrfToken: string | undefined;
async function csrf() {
  if (csrfToken) return csrfToken;
  const data = await request<{ csrf_token: string }>("/auth/csrf", { method: "GET" }, false);
  csrfToken = data.csrf_token;
  return csrfToken;
}

async function request<T>(path: string, init: RequestInit = {}, addCsrf = true, retried = false): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (addCsrf && !["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", await csrf());
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  const authFlow = ["/auth/refresh", "/auth/login/options", "/auth/login/verify", "/auth/register/options", "/auth/register/verify", "/auth/recovery", "/auth/email/verify"].some(route => path.startsWith(route));
  const unsafe = addCsrf && !["GET", "HEAD", "OPTIONS"].includes(method);
  if (response.status === 403 && unsafe && !retried) {
    // The CSRF cookie expires server-side; drop the cached token and retry once with a fresh one.
    csrfToken = undefined;
    return request<T>(path, init, addCsrf, true);
  }
  if (response.status === 401 && !retried && !authFlow) {
    try {
      const refreshWith = async () => fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "include", headers: { "X-CSRF-Token": await csrf() } });
      let refresh = await refreshWith();
      if (refresh.status === 403) { csrfToken = undefined; refresh = await refreshWith(); }
      if (!refresh.ok) throw new Error("Session refresh failed.");
      return request<T>(path, init, addCsrf, true);
    } catch { /* Preserve the original endpoint's authentication error. */ }
  }
  if (response.status === 204) return undefined as T;
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed." }));
    throw new Error(body.detail ?? body.message ?? `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}
const json = (method: string, data?: unknown): RequestInit => ({ method, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
const enc = encodeURIComponent;

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, data?: unknown, extraHeaders?: Record<string, string>) => request<T>(path, { ...json("POST", data), headers: extraHeaders }),
  put: <T,>(path: string, data: unknown) => request<T>(path, json("PUT", data)),
  patch: <T,>(path: string, data: unknown) => request<T>(path, json("PATCH", data)),
  delete: <T,>(path: string, data?: unknown, extraHeaders?: Record<string, string>) => request<T>(path, { ...json("DELETE", data), headers: extraHeaders }),
};

export function uploadFile(files: File[], kind: "ecg" | "rr_csv", persist = false, consentId?: string, deviceId?: string): Promise<Job> {
  const data = new FormData();
  files.forEach(file => data.append("files", file));
  data.append("kind", kind); data.append("persist_result", String(persist));
  if (consentId) data.append("consent_id", consentId);
  if (deviceId) data.append("device_id", deviceId);
  return request(routes.upload.replace("/v1", ""), { method: "POST", body: data });
}
export const getJob = (id: string, capability?: string) => request<{ status: string; result_id?: string; result?: Result; error?: string }>(routes.job.replace("/v1", "").replace("{job_id}", enc(id)), { headers: capability ? { "X-Job-Capability": capability } : undefined });
export const getResult = (id: string, capability?: string) => request<Result>(routes.result.replace("/v1", "").replace("{result_id}", enc(id)), { headers: capability ? { "X-Job-Capability": capability } : undefined });
export const getModelCard = () => request<Record<string, number | string | null>>(routes.modelCard.replace("/v1", ""));

export function serializeCredential(credential: Credential | null) {
  if (!credential) throw new Error("Passkey operation was cancelled.");
  const encode = (value: ArrayBuffer | null) => value ? btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "") : undefined;
  const publicCredential = credential as PublicKeyCredential;
  const response = publicCredential.response as AuthenticatorAttestationResponse | AuthenticatorAssertionResponse;
  const out: Record<string, unknown> = {
    id: publicCredential.id, rawId: encode(publicCredential.rawId), type: publicCredential.type,
    authenticatorAttachment: publicCredential.authenticatorAttachment,
    response: { clientDataJSON: encode(response.clientDataJSON) },
  };
  if ("attestationObject" in response) Object.assign(out.response as object, { attestationObject: encode(response.attestationObject), transports: response.getTransports?.() });
  else Object.assign(out.response as object, { authenticatorData: encode(response.authenticatorData), signature: encode(response.signature), userHandle: encode(response.userHandle) });
  return out;
}
export function publicKeyOptions(raw: Record<string, unknown>): PublicKeyCredentialCreationOptions | PublicKeyCredentialRequestOptions {
  const decode = (v: unknown) => typeof v === "string" ? Uint8Array.from(atob(v.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)) : v;
  const options = structuredClone(raw) as Record<string, any>;
  if (options.challenge) options.challenge = decode(options.challenge);
  if (options.user?.id) options.user.id = decode(options.user.id);
  for (const key of ["allowCredentials", "excludeCredentials"]) if (Array.isArray(options[key])) options[key] = options[key].map((item: Record<string, unknown>) => ({ ...item, id: decode(item.id) }));
  return options as unknown as PublicKeyCredentialCreationOptions;
}
