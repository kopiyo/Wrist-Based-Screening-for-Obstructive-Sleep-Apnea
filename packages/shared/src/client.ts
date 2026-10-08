const buildEnv = (import.meta as ImportMeta & { env?: { VITE_API_URL?: string } }).env;
export const API_BASE = buildEnv?.VITE_API_URL ?? "http://localhost:8000/v1";
import type { paths } from "./openapi";
type ApiPath = keyof paths;
const routes = {
  upload: "/v1/analyze/file",
  job: "/v1/jobs/{job_id}",
  result: "/v1/results/{result_id}",
  modelCard: "/v1/model-card",
} as const satisfies Record<string, ApiPath>;

export type Job = { job_id: string };
export type Result = {
  score: number;
  apnea_burden_percent: number;
  per_minute_probabilities: number[];
  signal_quality: { usable_beat_ratio?: number | null; gap_count?: number; gap_seconds?: number; usable_beats?: number };
  referral_decision: "Sleep study recommended" | "Not indicated by this screening";
  source_validated: boolean;
  source: string;
  source_badge?: string;
  disclaimer: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed." }));
    throw new Error(body.detail ?? "Request failed.");
  }
  return response.json() as Promise<T>;
}

export function uploadFile(files: File[], kind: "ecg" | "rr_csv"): Promise<Job> {
  const data = new FormData();
  files.forEach(file => data.append("files", file));
  data.append("kind", kind);
  return request(routes.upload.replace("/v1", ""), { method: "POST", body: data });
}

export const getJob = (id: string) => request<{ status: string; result_id?: string; error?: string }>(routes.job.replace("/v1", "").replace("{job_id}", encodeURIComponent(id)));
export const getResult = (id: string) => request<Result>(routes.result.replace("/v1", "").replace("{result_id}", encodeURIComponent(id)));
export const getModelCard = () => request<Record<string, number | string | null>>(routes.modelCard.replace("/v1", ""));
