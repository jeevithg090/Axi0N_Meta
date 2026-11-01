// Thin, typed calls to the Rulecam API, through the same-origin rewrite (next.config.ts): the API
// sends no CORS headers. Owner-only calls work from the owner's machine (loopback) or with an
// owner session: the HttpOnly cookie from `POST /auth/login` rides along, and every write also
// sends the session's CSRF token (`setCsrfToken`, kept in memory only) in `X-CSRF-Token`.

import { API_PROXY } from "./config";
import type { Alert } from "./ops";
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

/** `GET /auth/me` and `POST /auth/login`. */
export interface AuthStatus {
  owner: boolean;
  via: "loopback" | "session" | null;
  csrf_token: string | null;
  expires_at: string | null;
  login_enabled: boolean;
}

/** One saved planner question (`GET /plans/{id}/questions`). */
export interface PlanQuestion {
  step: "ask" | "accept";
  question_id: string;
  text: string;
  target_field: string;
  requires_frame_tap: boolean;
  camera_id: string | null;
  options: string[];
}

export interface PlanQuestions {
  plan_id: string;
  questions: PlanQuestion[];
  running: boolean;
  answered: number;
}

export type Point = [number, number];

/** The owner's answer: words, a tap on the camera frame, or a floor calibration. */
export interface Answer {
  question_id: string;
  text?: string;
  tap?: { x: number; y: number };
  calibration?: { image_px: Point[]; ground_m: Point[] };
}

/** `GET /runtime/metrics` → `cost` (WS3): the per-camera dollar budget and spend. */
export interface CostReport {
  budget_usd_per_camera_hour: number;
  gpu_usd_per_hour: number;
  alarm: boolean;
  over_budget: string[];
  usd: number;
  usd_per_camera_hour: number | null;
}

/** `GET /runtime/metrics` → `verifier` (P3-05): the Tier 1 circuit breaker. */
export interface VerifierHealth {
  state: "closed" | "open" | "half_open";
  down: boolean;
  consecutive_failures: number;
  failures: number;
  fast_failures: number;
  opens: number;
  last_error: string;
  retry_in_s: number;
}

/** The parts of `GET /runtime/metrics` the UI reads. */
export interface RuntimeMetrics {
  cost?: CostReport | null;
  verifications_failed?: number;
  verifier_down?: boolean;
  verifier?: VerifierHealth | null;
}

export const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
let csrfToken: string | null = null;

/** Remember (or forget) the owner session's CSRF token for the writes that follow. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** The request init with the CSRF header added on writes while a session token is known. */
export function withCsrf(init: RequestInit = {}): RequestInit {
  const method = (init.method ?? "GET").toUpperCase();
  if (SAFE_METHODS.has(method) || csrfToken === null) return init;
  const headers = new Headers(init.headers);
  headers.set(CSRF_HEADER, csrfToken);
  return { ...init, headers };
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PROXY}${path}`, withCsrf(init));
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
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Owner-facing wording for an API error (403 means: log in). */
export function errorText(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 403) return `${error.message}. Log in as the owner and try again.`;
  return error.message;
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
  runtimeMetrics: () => call<RuntimeMetrics>("/runtime/metrics"),
  workerPage: (planId: string, camera: string, lang?: string | null) =>
    call<WorkerPage>(
      `/worker/${encodeURIComponent(planId)}/${encodeURIComponent(camera)}` +
        (lang ? `?lang=${encodeURIComponent(lang)}` : ""),
    ),
  dispute: (body: DisputeRequest) => call<DisputeResult>("/worker/dispute", json(body)),
  me: () => call<AuthStatus>("/auth/me"),
  login: (passphrase: string) => call<AuthStatus>("/auth/login", json({ passphrase })),
  logout: () => call<void>("/auth/logout", { method: "POST" }),
  questions: (planId: string) =>
    call<PlanQuestions>(`/plans/${encodeURIComponent(planId)}/questions`),
  answer: (planId: string, answers: Answer[]) =>
    call<{ plan_id: string; answers: number }>(
      `/plans/${encodeURIComponent(planId)}/answers`,
      json({ answers }),
    ),
  frameUrl: (planId: string, camera: string) =>
    `${API_PROXY}/plans/${encodeURIComponent(planId)}/frames/${encodeURIComponent(camera)}`,
  alerts: (role?: string) =>
    call<Alert[]>(`/alerts${role ? `?role=${encodeURIComponent(role)}` : ""}`),
  respond: (alertId: string, button: string, role: string) =>
    call<{ alert_id: string; action: string }>(
      `/alerts/${encodeURIComponent(alertId)}/respond`,
      json({ button, role }),
    ),
  suggestRule: (surpriseId: string) =>
    call<{ rule_id: string }>(`/surprise/${encodeURIComponent(surpriseId)}/suggest`, {
      method: "POST",
    }),
};
