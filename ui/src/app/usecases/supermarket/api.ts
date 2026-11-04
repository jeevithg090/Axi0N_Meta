// The supermarket use case's API (`/retail/*`, owner-only), through the same-origin rewrite with
// the session's CSRF token. Contract: docs/usecases/supermarket.md.

import { ApiError, withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";

export type CheckKind = "queue" | "hazard" | "shelf";
export type TaskStatus = "open" | "on_my_way" | "done" | "false_alarm";

export interface ModelCall {
  model: string;
  role: string;
  seconds: number;
  error: string | null;
}

/** One check's result (`POST /retail/checks/{kind}`). */
export interface Finding {
  id: string;
  kind: CheckKind;
  camera_id: string;
  camera_name: string;
  at: string | null;
  checked_at: string;
  watch_id: string | null;
  act: boolean;
  headline: string;
  staff_message: string;
  detail: Record<string, unknown>;
  evidence: { frames: string[]; marked: string | null; clip: string | null };
  calls: ModelCall[];
  seconds: number;
  alert_id: string | null;
  task_id: string | null;
  metric: number | null;
  /** Evidence-chain record of the alert (`retail_finding`), when one was sent. */
  chain_seq?: number | null;
  /** `open`, `acknowledged` or `false_alarm` once staff answered (overview only). */
  alert_status?: string | null;
}

export interface Watch {
  id: string;
  kind: CheckKind;
  camera_id: string;
  every_s: number;
  role: string;
  focus: string;
  advice_id: string | null;
  created_at: string;
  active: boolean;
  last_run: string | null;
}

export interface StaffTask {
  id: string;
  kind: "shelf" | "hazard";
  finding_id: string;
  camera_id: string;
  where: string;
  items: string[];
  marked: string | null;
  seen_at: string | null;
  created_at: string;
  status: TaskStatus;
  done_at: string | null;
  closed_by: "photo" | "camera" | "staff" | null;
  minutes_to_fix: number | null;
  alert_id: string | null;
  role: string;
  proof_image: string | null;
  proof: {
    closed?: boolean;
    spark?: { summary?: string };
    glimmer?: { summary?: string } | null;
  } | null;
  fixed_seq: number | null;
  /** Evidence-chain record of the detection (`retail_finding`). */
  detected_seq?: number | null;
}

export interface CardNumber {
  id: string;
  label: string;
  value: number;
  unit: string;
  definition: string;
  grounded: boolean;
}

export interface MeasurementPlan {
  id: string;
  primary_metric: string;
  guard_metrics: string[];
  design: string;
  block_minutes: number;
  n_pairs: number;
  min_detectable_effect: number;
  smallest_possible_p: number;
  decision_rule: string;
  preregistration_hash: string;
}

export interface CardProgress {
  status: string;
  pairs_planned: number;
  pairs_done: number;
  watch_readings: number;
  mean_reading: number | null;
  preregistered_seq: number | null;
}

/** An advice card made from an Ask answer (`POST /retail/advice`). */
export interface RetailCard {
  recommendation: {
    id: string;
    status: string;
    do: { op: string; params: Record<string, string> }[];
    who_role: string;
    revert: string;
    expect: { interval: [number, number]; unit: string; model_rung: string };
  };
  title: string;
  saw: string[];
  change: string;
  gain: string;
  numbers: CardNumber[];
  hidden: string[];
  plan: MeasurementPlan;
  sigma_note: string;
  watch: { kind: CheckKind; camera_id: string; every_s: number; role: string; focus: string };
  ask_receipt: string;
  question: string;
  created_at: string;
  seconds: number;
  preregistered_seq: number | null;
  watch_id: string | null;
  progress?: CardProgress;
}

export interface AskRun {
  receipt: string;
  ts: string;
  question: string;
  answer: string;
  seconds: number;
  models: string[];
  conversation_id: string;
}

export interface Brief {
  day: string;
  written_at: string;
  lang: string;
  text: string;
  headline: string;
  stats: Record<string, unknown>;
  decisions: string[];
  model: string;
  seconds: number;
}

export interface Receipt {
  receipt: string;
  prev_sha256: string;
  hashed_text: string;
  question: string;
  answer: string;
  ts: string;
  models: string[];
  rows_verified: number | null;
  qr_png_base64: string | null;
}

export interface Camera {
  camera_id: string;
  name: string;
  first: string;
  last: string;
}

export interface Overview {
  cameras: Camera[];
  watches: Watch[];
  findings: Finding[];
  tasks: StaffTask[];
  cards: RetailCard[];
  asks: AskRun[];
  brief: Brief | null;
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
  return (await response.json()) as T;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const upload = (field: string, file: Blob, name: string): RequestInit => {
  const form = new FormData();
  form.append(field, file, name);
  return { method: "POST", body: form };
};

export const retail = {
  overview: () => call<Overview>("/retail/overview"),
  check: (kind: CheckKind, cameraId: string) =>
    call<Finding>(`/retail/checks/${kind}`, post({ camera_id: cameraId })),
  checkPhoto: (kind: "hazard" | "shelf", photo: File) =>
    call<Finding>(`/retail/checks/${kind}/image`, upload("image", photo, photo.name)),
  addWatch: (kind: CheckKind, cameraId: string, everyS: number, role: string, focus = "") =>
    call<Watch>(
      "/retail/watches",
      post({ kind, camera_id: cameraId, every_s: everyS, role, focus }),
    ),
  runWatch: (id: string) => call<Finding>(`/retail/watches/${id}/run`, { method: "POST" }),
  stopWatch: (id: string) => call<Watch>(`/retail/watches/${id}`, { method: "DELETE" }),
  setTask: (id: string, status: TaskStatus) =>
    call<StaffTask>(`/retail/tasks/${id}/status`, post({ status })),
  proveTask: (id: string, photo: File) =>
    call<StaffTask>(`/retail/tasks/${id}/proof`, upload("image", photo, photo.name)),
  advice: (receipt: string, cameraId: string | null) =>
    call<RetailCard>("/retail/advice", post({ receipt, camera_id: cameraId })),
  accept: (id: string) => call<RetailCard>(`/retail/advice/${id}/accept`, { method: "POST" }),
  brief: (lang: string) => call<Brief>("/retail/brief", post({ lang })),
  receipt: (sha: string, link: string | null) =>
    call<Receipt>(`/retail/receipts/${sha}${link ? `?link=${encodeURIComponent(link)}` : ""}`),
  askVoice: (wav: Blob, lang: string | null, conversationId: string | null) => {
    const form = new FormData();
    form.append("audio", wav, "question.wav");
    if (lang) form.append("lang", lang);
    if (conversationId) form.append("conversation_id", conversationId);
    return call<{ transcript: string; lang: string; transcribe_seconds: number; answer: unknown }>(
      "/retail/ask/voice",
      { method: "POST", body: form },
    );
  },
};

/** The URL of a finding's frame, marked image or photo. */
export function fileUrl(name: string): string {
  return `${API_PROXY}/retail/files/${encodeURIComponent(name)}`;
}

/** Hex SHA-256 of UTF-8 text, in the browser (no server trust needed). */
export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Whether `sha256(prev + hashed_text)` is the receipt: the answer is the one that was logged. */
export async function receiptHolds(r: Pick<Receipt, "receipt" | "prev_sha256" | "hashed_text">) {
  return (await sha256Hex(r.prev_sha256 + r.hashed_text)) === r.receipt;
}

/** "3 min ago" style age of an ISO time, relative to `now`. */
export function age(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

/** BCP-47 voice for the browser's speech synthesis from a language tag or name. */
export function speechLang(lang: string): string {
  const key = lang.trim().toLowerCase();
  const names: Record<string, string> = {
    english: "en-US",
    spanish: "es-ES",
    español: "es-ES",
    hindi: "hi-IN",
    हिन्दी: "hi-IN",
    french: "fr-FR",
    portuguese: "pt-BR",
    arabic: "ar-SA",
    chinese: "zh-CN",
  };
  if (names[key]) return names[key];
  if (/^[a-z]{2}(-[a-z]{2})?$/i.test(key)) return key.length === 2 ? key : key;
  return "en-US";
}
