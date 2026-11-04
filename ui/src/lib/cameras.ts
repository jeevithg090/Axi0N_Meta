// Camera connectors (owner-only, `/connectors`): add any camera by URL, network scan, phone or
// clip; each one's health, Muse Spark's description and SAM 3.1's survey. Every call goes
// through the same-origin rewrite with the session's CSRF token.

import { ApiError, withCsrf } from "./api";
import { API_PROXY } from "./config";

export type SourceKind = "rtsp" | "hls" | "http" | "rtmp" | "public" | "file";
export type StreamState = "connecting" | "live" | "reconnecting" | "stopped" | "ended";
export type SurveyStatus =
  | "waiting"
  | "describing"
  | "detecting"
  | "matching"
  | "done"
  | "failed"
  | "blocked"
  | "unavailable";
export type Fit = "strong" | "possible" | "poor";

export interface StreamHealth {
  state: StreamState;
  fps: number | null;
  speed: number | null;
  frames: number;
  chunks: number;
  connected_at: string | null;
  first_chunk_s: number | null;
  last_frame_at: string | null;
  last_chunk_at: string | null;
  last_chunk_id: string | null;
  restarts: number;
  errors: number;
  last_error: string | null;
  next_retry_at: string | null;
  input: { codec: string; width: number; height: number; fps: number | null } | null;
  title: string | null;
}

export interface ConceptResult {
  concept: string;
  count: number;
  max_confidence: number | null;
  mean_confidence: number | null;
  area_share: number;
  error: string | null;
}

export interface UseCaseFit {
  use_case: "supermarket" | "warehouse" | "crowd" | "other";
  fit: Fit;
  why: string;
  first_rules: string[];
}

export interface CameraSurvey {
  camera_id: string;
  status: SurveyStatus;
  chunk_id: string | null;
  snapshot: string | null;
  marked: string | null;
  look: {
    name: string;
    description: string;
    viewpoint: string;
    setting: string;
    image_problems: string;
    private_space: string;
    concepts: string[];
  } | null;
  detections: ConceptResult[];
  fit: { use_cases: UseCaseFit[]; placement_tip: string } | null;
  error: string | null;
  timings: Record<string, number>;
  models: Record<string, string>;
}

export interface ConnectedCamera {
  camera_id: string;
  name: string;
  named_by: string;
  kind: SourceKind;
  display_url: string;
  has_credentials: boolean;
  loop: boolean;
  provenance: string;
  created_at: string;
  blocked: string | null;
  health: StreamHealth;
  survey: CameraSurvey | null;
}

export interface AddCameraInput {
  url: string;
  name?: string;
  username?: string;
  password?: string;
  loop?: boolean;
}

export interface FoundDevice {
  address: string;
  via: "onvif" | "rtsp-port";
  xaddr: string | null;
  name: string | null;
  hardware: string | null;
  location: string | null;
  rtsp_ports: number[];
}

export interface ScanResult {
  devices: FoundDevice[];
  local_ip: string | null;
  subnet: string | null;
  took_s: number;
  notes: string[];
}

export interface OnvifProfile {
  token: string;
  name: string;
  encoding: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  rtsp_url: string | null;
  error: string | null;
}

export interface OnvifDevice {
  xaddr: string;
  manufacturer: string | null;
  model: string | null;
  firmware: string | null;
  profiles: OnvifProfile[];
}

/** A public live webcam for one-click demos (`GET /connectors/presets`). */
export interface LivePreset {
  preset_id: string;
  title: string;
  url: string;
  source: string;
  shows: string;
  label: string;
}

export interface BrandTemplate {
  brand: string;
  main: string;
  sub: string;
  note: string;
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

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const id = (cameraId: string): string => encodeURIComponent(cameraId);

export const cameras = {
  list: () => call<ConnectedCamera[]>("/connectors/cameras"),
  add: (input: AddCameraInput) => call<ConnectedCamera>("/connectors/cameras", post(input)),
  upload: (clip: File, name: string) => {
    const form = new FormData();
    form.append("clip", clip);
    form.append("name", name);
    return call<ConnectedCamera>("/connectors/cameras/upload", { method: "POST", body: form });
  },
  remove: (cameraId: string) =>
    call<void>(`/connectors/cameras/${id(cameraId)}`, { method: "DELETE" }),
  restart: (cameraId: string) =>
    call<ConnectedCamera>(`/connectors/cameras/${id(cameraId)}/restart`, { method: "POST" }),
  survey: (cameraId: string) =>
    call<CameraSurvey>(`/connectors/survey/${id(cameraId)}`, { method: "POST" }),
  getSurvey: (cameraId: string) => call<CameraSurvey>(`/connectors/survey/${id(cameraId)}`),
  scan: (timeoutS = 3) => call<ScanResult>("/connectors/scan", post({ timeout_s: timeoutS })),
  onvif: (target: string, username: string, password: string) =>
    call<OnvifDevice>(
      "/connectors/onvif",
      post({ target, username: username || null, password: password || null }),
    ),
  brands: () => call<BrandTemplate[]>("/connectors/brands"),
  presets: () => call<LivePreset[]>("/connectors/presets"),
};

/** The newest frame of any camera; `t` busts the browser cache so tiles refresh. */
export function snapshotUrl(cameraId: string, t: number): string {
  return `${API_PROXY}/connectors/snapshot/${id(cameraId)}.jpg?t=${t}`;
}

/** A snapshot or marked survey image. */
export function imageUrl(name: string): string {
  return `${API_PROXY}/connectors/images/${encodeURIComponent(name)}`;
}

/** A maker's URL template with the camera's address filled in. */
export function fillTemplate(template: string, host: string): string {
  return template.replace("{host}", host.trim() || "<camera-ip>");
}

/** "3 s ago" style age of a timestamp, or "never". */
export function ago(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

/** One line of a camera's health for its tile. */
export function healthLine(health: StreamHealth, now: number = Date.now()): string {
  const parts: string[] = [health.state];
  if (health.fps !== null) parts.push(`${health.fps.toFixed(1)} fps`);
  parts.push(`${health.chunks} chunks`);
  parts.push(`last frame ${ago(health.last_frame_at, now)}`);
  if (health.input) {
    const rate = health.input.fps ? ` @ ${health.input.fps}` : "";
    parts.push(`${health.input.codec} ${health.input.width}x${health.input.height}${rate}`);
  }
  if (health.restarts > 0) parts.push(`${health.restarts} reconnects`);
  return parts.join(" · ");
}

/** What a survey is doing, in words. */
export function surveyStep(status: SurveyStatus): string {
  return {
    waiting: "Waiting for the first frames…",
    describing: "Muse Spark is looking at the camera…",
    detecting: "SAM 3.1 is checking what it can detect…",
    matching: "Muse Spark is matching it to use cases…",
    done: "First look done",
    failed: "First look failed",
    blocked: "Stopped: looks into a private space",
    unavailable: "First look unavailable",
  }[status];
}

/** Use cases best fit first (strong, possible, poor). */
export function rankFits(fits: UseCaseFit[]): UseCaseFit[] {
  const order: Record<Fit, number> = { strong: 0, possible: 1, poor: 2 };
  return [...fits].sort((a, b) => order[a.fit] - order[b.fit]);
}
