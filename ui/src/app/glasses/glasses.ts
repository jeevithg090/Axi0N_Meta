// Glasses mode (`/wearables`, owner-only): the wearer's point of view goes to Rulecam one frame at
// a time, with a push-to-talk question or none (patrol). Muse Glimmer looks, SAM 3.1 counts and
// Muse Spark answers; the answer comes back ready to speak. Safety walks collect the frames and
// end in a report chained as evidence.

import { ApiError, withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";
import type { Alert } from "@/lib/ops";

export type Mode = "aisle" | "warehouse" | "crowd" | "general";
export type Source = "phone-camera" | "meta-glasses" | "mock-device" | "upload";
export type Severity = "none" | "info" | "caution" | "danger";

export interface Hazard {
  what: string;
  where: string;
  severity: Exclude<Severity, "none">;
}

export interface Stage {
  name: string;
  model: string;
  ms: number;
  ok: boolean;
  note: string;
}

export interface Count {
  concept: string;
  count: number;
  boxes: number[][];
}

export interface Gap {
  number: number;
  section: string;
  neighbours: string;
  facings: number;
}

/** `POST /wearables/look`. */
export interface PovAnswer {
  answer_id: string;
  at: string;
  mode: Mode;
  kind: "question" | "patrol";
  question: string | null;
  heard: string | null;
  spoken: string;
  detail: string;
  alert: boolean;
  verified: boolean;
  verified_by: string[];
  severity: Severity;
  confidence: number;
  hazards: Hazard[];
  counts: Count[];
  gaps: Gap[];
  look: {
    scene: string;
    answer: string;
    hazards: string[];
    alert: boolean;
    count: string[];
  } | null;
  looked_by: string;
  frame: string;
  marked: string | null;
  audio: string | null;
  voice: string | null;
  stages: Stage[];
  total_ms: number;
  walk_id: string | null;
  source: Source;
}

export interface Walk {
  walk_id: string;
  mode: Mode;
  note: string;
  started_at: string;
  finished_at: string | null;
  frames: number;
}

export interface WalkHazard {
  what: string;
  where: string;
  severity: Exclude<Severity, "none">;
  answer_ids: string[];
  action: string;
}

/** `POST /wearables/walks/{id}/finish`. */
export interface WalkReport {
  walk: Walk;
  summary: { summary: string; spoken: string; hazards: WalkHazard[]; restock: string[] };
  answers: PovAnswer[];
  chain_hashes: string[];
  model: string;
  ms: number;
}

/** `GET /wearables/status`. */
export interface WearablesStatus {
  ready: boolean;
  look: string | null;
  compose: string;
  count: string;
  transcribe: string | null;
  voice: string | null;
  modes: Mode[];
  glimmer_timeout_s: number;
  glimmer: GlimmerState;
}

export type GlimmerState = "unknown" | "checking" | "up" | "down" | "none";

/** The HUD chip for Glimmer's state. */
export function glimmerLabel(state: GlimmerState): string {
  switch (state) {
    case "up":
      return "Glimmer up";
    case "down":
      return "Glimmer down · Spark looks";
    case "checking":
      return "Waking Glimmer…";
    case "none":
      return "No Glimmer · Spark looks";
    default:
      return "Glimmer ?";
  }
}

export const MODES: { mode: Mode; label: string; hint: string }[] = [
  { mode: "aisle", label: "Aisle", hint: "Shelf gaps, spills, blocked aisles" },
  { mode: "warehouse", label: "Warehouse", hint: "Forklifts near people, lanes, exits" },
  { mode: "crowd", label: "Crowd", hint: "How dense it is ahead, pushing, exits" },
  { mode: "general", label: "General", hint: "Ask anything about what you see" },
];

export const SOURCE_LABELS: Record<Source, string> = {
  "phone-camera": "Phone camera standing in for the glasses",
  "meta-glasses": "Meta AI glasses camera",
  "mock-device": "Meta SDK mock device (simulated glasses)",
  upload: "Uploaded photo",
};

/** The longest side a frame is scaled to before it is sent. */
export const MAX_FRAME_PX = 960;

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

export interface LookInput {
  frame: Blob;
  mode: Mode;
  source: Source;
  question?: string;
  audio?: Blob | null;
  walkId?: string | null;
  lang?: string;
  speak?: boolean;
}

/** The multipart body of `POST /wearables/look`. */
export function lookForm(input: LookInput): FormData {
  const form = new FormData();
  const type = input.frame.type === "image/png" ? "png" : "jpg";
  form.append("image", input.frame, `frame.${type}`);
  if (input.audio) form.append("audio", input.audio, "question.wav");
  const question = input.question?.trim();
  if (question) form.append("question", question);
  form.append("mode", input.mode);
  form.append("source", input.source);
  if (input.walkId) form.append("walk_id", input.walkId);
  form.append("lang", input.lang ?? "en");
  form.append("speak", input.speak === false ? "false" : "true");
  // Start the work and poll for it: a proxy in between may not hold a slow request open.
  form.append("wait", "false");
  return form;
}

/** `GET /wearables/jobs/{id}`: a look or walk report started with `wait=false`. */
export interface Job {
  job_id: string;
  status: "running" | "done" | "failed";
  answer: PovAnswer | null;
  report: WalkReport | null;
  error: string | null;
  stages?: Stage[];
}

export const POLL_MS = 800;
export const POLL_LIMIT_MS = 240_000;

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll a job until it is done; throws its error, or after `limitMs`. */
export async function untilDone(
  started: Job,
  fetchJob: (id: string) => Promise<Job>,
  {
    every = POLL_MS,
    limitMs = POLL_LIMIT_MS,
    wait = sleep,
    onProgress,
  }: {
    every?: number;
    limitMs?: number;
    wait?: (ms: number) => Promise<void>;
    onProgress?: (job: Job) => void;
  } = {},
): Promise<Job> {
  let job = started;
  for (let waited = 0; job.status === "running"; waited += every) {
    if (waited >= limitMs) throw new ApiError(504, "No answer in time; try again.");
    await wait(every);
    job = await fetchJob(job.job_id);
    if (job.status === "running") onProgress?.(job);
  }
  if (job.status === "failed") throw new ApiError(502, job.error ?? "The models did not answer.");
  return job;
}

const job = (id: string) => call<Job>(`/wearables/jobs/${encodeURIComponent(id)}`);

export const wearables = {
  status: () => call<WearablesStatus>("/wearables/status"),
  look: async (input: LookInput, onProgress?: (job: Job) => void): Promise<PovAnswer> => {
    const started = await call<Job>("/wearables/look", { method: "POST", body: lookForm(input) });
    const done = await untilDone(started, job, { onProgress });
    if (!done.answer) throw new ApiError(502, "The job ended without an answer.");
    return done.answer;
  },
  startWalk: (mode: Mode, note = "") =>
    call<Walk>("/wearables/walks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, note }),
    }),
  finishWalk: async (walkId: string): Promise<WalkReport> => {
    const path = `/wearables/walks/${encodeURIComponent(walkId)}/finish?wait=false`;
    const started = await call<Job | WalkReport>(path, { method: "POST" });
    if (!("job_id" in started)) return started; // already finished earlier
    const done = await untilDone(started, job);
    if (!done.report) throw new ApiError(502, "The job ended without a report.");
    return done.report;
  },
};

export const frameUrl = (name: string): string =>
  `${API_PROXY}/wearables/frames/${encodeURIComponent(name)}`;

export const audioUrl = (name: string): string =>
  `${API_PROXY}/wearables/audio/${encodeURIComponent(name)}`;

/** Friendly names for the models on the answer card. */
export function modelName(model: string): string {
  if (model.startsWith("muse-glimmer")) return "Glimmer";
  if (model.startsWith("muse-spark")) return "Spark";
  if (model.startsWith("sam-")) return "SAM 3.1";
  if (model.startsWith("muse-voice")) return "Voice Transcribe";
  return model;
}

/** The badge text: who backs the alert, or why it is not verified. */
export function verdictLabel(answer: PovAnswer): string {
  if (!answer.alert) return answer.severity === "none" ? "No hazard confirmed" : "Noted, no alert";
  if (answer.verified) return `Verified alert · ${answer.verified_by.map(modelName).join(" + ")}`;
  return "Unverified alert · only one model looked";
}

/** Whether the page should say this answer out loud (the server already decided what to say). */
export function shouldSpeak(answer: PovAnswer): boolean {
  return answer.spoken.trim().length > 0;
}

/** The last finished stage of a running look, for the HUD: "Glimmer look 10.2 s ✓". */
export function progressLabel(job: Job): string | null {
  const last = job.stages?.at(-1);
  if (!last) return null;
  return `${modelName(last.model)} ${last.name} ${(last.ms / 1000).toFixed(1)} s ${last.ok ? "✓" : "✕"}`;
}

/** "look 3.1 s · count 1.9 s · compose 5.7 s" for the latency line. */
export function stageLine(stages: Stage[]): string {
  return stages
    .map((s) => `${s.name} ${(s.ms / 1000).toFixed(1)} s${s.ok ? "" : " ✕"}`)
    .join(" · ");
}

/** The width and height a frame is drawn at so its longest side is at most `max`. */
export function scaledSize(width: number, height: number, max = MAX_FRAME_PX): [number, number] {
  if (width <= 0 || height <= 0) return [0, 0];
  const scale = Math.min(1, max / Math.max(width, height));
  return [Math.round(width * scale), Math.round(height * scale)];
}

/** What to say for a site alert raised by the fixed cameras. */
export function alertLine(alert: Alert): string {
  const body = alert.body.split("\n")[0]?.trim() ?? "";
  return `Site alert. ${alert.title}.${body ? ` ${body}` : ""}`.replace(/\.\./g, ".");
}

/** Open site alerts not spoken yet, oldest first, and the ids to remember. */
export function unspokenAlerts(alerts: Alert[], spoken: ReadonlySet<string>): Alert[] {
  return alerts.filter((a) => a.status === "open" && !spoken.has(a.alert_id)).reverse();
}
