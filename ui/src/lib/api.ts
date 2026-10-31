// Thin, typed calls to the Rulecam API. Owner-only calls work from the owner's machine (loopback).
// They go through the same-origin rewrite (next.config.ts): the API sends no CORS headers.

import { API_PROXY } from "./config";
import type { PlanSummary, SitePlan } from "./plan";
import type { DisputeRequest, DisputeResult, WorkerPage } from "./worker";

export interface RequestItemInput {
  kind: "text" | "voice" | "pdf" | "photo" | "video";
  text?: string;
  file?: string;
  lang?: string;
}

export interface UploadResult {
  file: string;
  kind: RequestItemInput["kind"];
  bytes: number;
}

export interface CameraInfo {
  camera_id: string;
  label: string;
  source: string;
  last_chunk_at?: string | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PROXY}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  plans: () => call<PlanSummary[]>("/plans"),
  plan: (id: string) => call<SitePlan>(`/plans/${encodeURIComponent(id)}`),
  approve: (id: string) =>
    call<PlanSummary>(`/plans/${encodeURIComponent(id)}/approve`, { method: "POST" }),
  requestPlan: (items: RequestItemInput[]) =>
    call<{ plan_id: string }>("/plans/requests", json({ items })),
  upload: (file: Blob, name: string) => {
    const form = new FormData();
    form.append("file", file, name);
    return call<UploadResult>("/plans/uploads", { method: "POST", body: form });
  },
  cameras: () => call<CameraInfo[]>("/cameras"),
  workerPage: (planId: string, camera: string, lang?: string | null) =>
    call<WorkerPage>(
      `/worker/${encodeURIComponent(planId)}/${encodeURIComponent(camera)}` +
        (lang ? `?lang=${encodeURIComponent(lang)}` : ""),
    ),
  dispute: (body: DisputeRequest) => call<DisputeResult>("/worker/dispute", json(body)),
};
