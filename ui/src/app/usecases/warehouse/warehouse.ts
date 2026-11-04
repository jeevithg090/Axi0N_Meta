// The warehouse use case's API contract (`src/rulecam/warehouse/models.py`) and its calls.
// Everything here is read from what the system recorded: model text (verdict reasons, talks,
// transcripts) is data and React escapes it.

import { ApiError, withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";

export type StepStatus = "done" | "waiting" | "not_yet" | "failed";

export interface StoryStep {
  key: string;
  title: string;
  status: StepStatus;
  detail: string;
  refs: Record<string, string>;
}

export interface CantCheck {
  concern: string;
  status: string;
  explanation: string;
}

export interface NearMiss {
  event_id: string;
  ts: string;
  camera_id: string;
  rule_id: string;
  rule_text: string;
  lang: string;
  plan_id: string | null;
  severity: string;
  limit_m: number | null;
  separation_m: number | null;
  closest_m: number | null;
  ttc_s: number | null;
  vehicle_speed_mps: number | null;
  calibration: string | null;
  calibration_err_m: number | null;
  verdict: string | null;
  verdict_score: number | null;
  verdict_reason: string | null;
  verifier: string | null;
  alert: boolean | null;
  route: string | null;
  actions: string[];
  acked_by: string[];
  disputes: string[];
  regression: boolean;
  training_done: boolean;
  foot_px: [number, number] | null;
}

export interface WarehouseStory {
  plan_id: string | null;
  approved: boolean;
  request: string;
  request_lang: string | null;
  rules: { id: string; severity: string }[];
  cant_check: CantCheck[];
  event_id: string | null;
  steps: StoryStep[];
  near_misses: NearMiss[];
}

export interface HeatPoint {
  event_id: string;
  x: number;
  y: number;
  alert: boolean | null;
}

export interface Heatmap {
  camera_id: string;
  day: string;
  width: number;
  height: number;
  cols: number;
  rows: number;
  cells: number[][];
  points: HeatPoint[];
  unplaced: number;
  background: string | null;
}

export interface SpokenPart {
  lang: string;
  text: string;
  tts: string | null;
  heard: string | null;
  wer: number | null;
  roundtrip_ok: boolean | null;
}

export interface SpokenWarning {
  parts?: SpokenPart[];
  event_id: string;
  lang: string;
  text: string;
  english: string;
  audio: string | null;
  tts: string | null;
  heard: string | null;
  wer: number | null;
  roundtrip_ok: boolean | null;
  error: string | null;
  model: string;
  created_at: string;
}

export interface ToolboxTalk {
  event_id: string;
  lang: string;
  title: string;
  minutes: number;
  sections: { heading: string; text: string }[];
  key_points: string[];
  questions: string[];
  illustration_prompt: string;
  illustration: string | null;
  illustration_error: string | null;
  model: string;
  image_model: string | null;
  incident_hash: string;
  created_at: string;
}

export interface GroundedClause {
  clause: string;
  title: string;
  text: string;
  source_url: string;
  source_retrieved: boolean;
  source_title: string;
  model: string;
}

export interface RefresherDuty {
  event_id: string;
  rule_id: string;
  camera_id: string;
  applies: boolean;
  why: string;
  clause: GroundedClause;
  vehicle_unit: string | null;
  unit_evidence: string;
  training_focus: string[];
  due: string;
  status: "due" | "done" | "not_required";
  done_by: string | null;
  done_at: string | null;
  done_hash: string | null;
  model: string;
  created_at: string;
}

export interface VoiceReport {
  report_id: string;
  lang: string;
  transcript: string;
  summary_en: string;
  what_happened: string;
  where: string;
  minutes_ago: number | null;
  vehicle_unit: string | null;
  candidates: string[];
  matches_event_id: string | null;
  match_reason: string;
  measured_m: number | null;
  proposed_rule: string;
  proposed_rule_en: string;
  plan_id: string | null;
  chained_hash: string | null;
  model: string;
  created_at: string;
}

export interface ShiftReport {
  day: string;
  counts: {
    candidates: number;
    alerts: number;
    suppressed: number;
    human_queue: number;
    acked: number;
    disputes: number;
    upheld: number;
    regressions: number;
    blind_records: number;
    by_rule: Record<string, number>;
    closest_m: number | null;
    fastest_mps: number | null;
  };
  headline: string;
  summary: string;
  patterns: string[];
  actions: string[];
  lang: string;
  model: string;
  chain_root: string | null;
  created_at: string;
}

/** Ready-made requests for the demo; the planner decides what each one becomes. */
export const PRESETS: { label: string; text: string; lang: string }[] = [
  {
    label: "Español: 4 m de una carretilla",
    text: "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento.",
    lang: "es",
  },
  {
    label: "English: 4 m from a forklift",
    text: "Keep people at least 4 metres away from a moving forklift.",
    lang: "en",
  },
  {
    label: "Speed near people",
    text: "Forklifts must drive at walking pace when a person is within 5 metres.",
    lang: "en",
  },
  {
    label: "Hi-vis vest",
    text: "Everyone on foot on the warehouse floor must wear a high-visibility vest.",
    lang: "en",
  },
];

const BASE = `${API_PROXY}/warehouse`;

/** How the page can show the story: loading, shown, owner-only, not on this server, or down. */
export type Access = "loading" | "ok" | "owner" | "missing" | "down";

/** A GET that hangs longer than this counts as the API being down (model POSTs are exempt). */
export const GET_TIMEOUT_MS = 15_000;

/** What a failed story call means for a visitor. */
export function accessOf(error: unknown): Exclude<Access, "loading" | "ok"> {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return "owner";
  if (error instanceof ApiError && error.status === 404) return "missing";
  return "down";
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const read = (init?.method ?? "GET").toUpperCase() === "GET";
  const signal = init?.signal ?? (read ? AbortSignal.timeout(GET_TIMEOUT_MS) : undefined);
  const response = await fetch(`${BASE}${path}`, withCsrf({ ...init, signal }));
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

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const enc = encodeURIComponent;

function query(params: Record<string, string | null | undefined>): string {
  const kept = Object.entries(params).filter((entry): entry is [string, string] => !!entry[1]);
  return kept.length ? `?${new URLSearchParams(kept).toString()}` : "";
}

export const warehouse = {
  story: (planId?: string | null) => call<WarehouseStory>(`/story${query({ plan_id: planId })}`),
  nearMisses: (planId?: string | null) =>
    call<NearMiss[]>(`/near-misses${query({ plan_id: planId })}`),
  heatmap: (cameraId?: string | null, planId?: string | null) =>
    call<Heatmap>(`/heatmap${query({ camera_id: cameraId, plan_id: planId })}`),
  warning: (eventId: string, lang: string, also?: string | null) =>
    call<SpokenWarning>(
      `/incidents/${enc(eventId)}/warning`,
      post(also ? { lang, also } : { lang }),
    ),
  talk: (eventId: string, lang: string) =>
    call<ToolboxTalk>(`/incidents/${enc(eventId)}/toolbox-talk`, post({ lang, illustrate: true })),
  savedTalk: (eventId: string, lang: string) =>
    call<ToolboxTalk>(`/incidents/${enc(eventId)}/toolbox-talk${query({ lang })}`),
  savedWarning: (eventId: string, lang: string, also?: string | null) =>
    call<SpokenWarning>(`/incidents/${enc(eventId)}/warning${query({ lang, also })}`),
  duty: (eventId: string) => call<RefresherDuty>(`/incidents/${enc(eventId)}/duty`),
  raiseDuty: (eventId: string) =>
    call<RefresherDuty>(`/incidents/${enc(eventId)}/duty`, { method: "POST" }),
  dutyDone: (eventId: string, role: string) =>
    call<RefresherDuty>(`/incidents/${enc(eventId)}/duty/done`, post({ role })),
  reports: () => call<VoiceReport[]>("/reports"),
  reportVoice: (audio: Blob, lang: string) =>
    call<VoiceReport>(`/reports/voice${query({ lang })}`, {
      method: "POST",
      headers: { "Content-Type": audio.type || "application/octet-stream" },
      body: audio,
    }),
  propose: (reportId: string) =>
    call<VoiceReport>(`/reports/${enc(reportId)}/propose`, { method: "POST" }),
  shift: (lang: string) => call<ShiftReport>("/shift-report", post({ lang })),
  savedShift: (lang: string) => call<ShiftReport>(`/shift-report${query({ lang })}`),
  fileUrl: (name: string) => `${BASE}/files/${enc(name)}`,
  frameUrl: (path: string) => `${API_PROXY}${path}`,
};

/** Metres with two decimals, or a dash. */
export function metres(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${value.toFixed(2)} m`;
}

/** The heat colour of a cell: transparent when empty, more opaque with more near misses. */
export function heatFill(count: number, max: number): string {
  if (count <= 0 || max <= 0) return "rgba(0,0,0,0)";
  const alpha = 0.25 + 0.55 * (count / max);
  return `rgba(220, 38, 38, ${alpha.toFixed(2)})`;
}

/** How many story steps are done, out of all of them. */
export function progress(steps: StoryStep[]): { done: number; total: number } {
  return { done: steps.filter((s) => s.status === "done").length, total: steps.length };
}

/** The day's evidence verify page (the API's static page, opened through the proxy). */
export function verifyUrl(day: string): string {
  return `${API_PROXY}/verify/?day=${enc(day)}`;
}

/** The printable incident report (HTML, chained records and stills). */
export function reportUrl(eventId: string): string {
  return `${API_PROXY}/incidents/${enc(eventId)}/report?format=html`;
}

/** A reader that returns the saved item only if it was made after now (minus clock skew). */
export function fresh<T extends { created_at: string }>(
  read: () => Promise<T>,
  skewMs = 30_000,
): () => Promise<T | null> {
  const started = Date.now() - skewMs;
  return async () => {
    const item = await read();
    return Date.parse(item.created_at) >= started ? item : null;
  };
}

/** Whether an error is the UI proxy giving up on a long model call (the API keeps working). */
export function proxyGaveUp(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    ((error.status === 500 && error.message === "Internal Server Error") || error.status === 504)
  );
}

/**
 * Run a model-backed write; if the proxy gives up before the API answers, read the saved
 * result until it appears (the API finishes and saves it either way).
 */
export async function withRecovery<T>(
  run: () => Promise<T>,
  saved: () => Promise<T | null>,
  { timeoutMs = 180_000, everyMs = 3_000 } = {},
): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (!proxyGaveUp(error)) throw error;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, everyMs));
      const found = await saved().catch(() => null);
      if (found !== null) return found;
    }
    throw error;
  }
}
