// Crowd early warning: the `/crowd/*` API (src/rulecam/api/crowd.py; contract in
// docs/usecases/crowd.md), the live status hook (SSE with a polling fallback) and small helpers.

"use client";

import { useEffect, useRef, useState } from "react";

import { withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";

export type Level = "normal" | "watch" | "warning" | "critical";
export type Pt = [number, number];

export interface ZoneReading {
  zone: string;
  people: number;
  area_m2?: number | null;
  density_ppm2?: number | null;
  speed_mps?: number | null;
  coherence?: number | null;
  counterflow_share?: number | null;
  pressure?: number | null;
}

export interface CellReading {
  row: number;
  col: number;
  people: number;
  area_m2: number;
  density_ppm2: number | null;
  flow_px?: Pt | null;
  speed_mps?: number | null;
  counterflow_share?: number | null;
  pressure?: number | null;
}

export interface CrowdReading {
  camera_id: string;
  at: string;
  concept: string;
  people: number;
  calibration: string;
  calibration_err_m?: number | null;
  zones: ZoneReading[];
  cells: CellReading[];
  peak_density_ppm2?: number | null;
  peak_cell?: Pt | null;
  heatmap?: string | null;
  frame?: string | null;
  notes: string[];
}

export interface CrowdZone {
  name: string;
  polygon_px: Pt[];
}

export interface FloorPoints {
  image_px: Pt[];
  ground_m: Pt[];
}

export interface Watch {
  camera_id: string;
  instruction: string;
  languages: string[];
  role: string;
  since: string;
  floor?: FloorPoints | null;
  zones: CrowdZone[];
  concept?: string | null;
  grid?: number | null;
  escalate_to: string[];
  ack_within_s: number;
}

export interface ZoneForecast {
  zone: string;
  density_ppm2?: number | null;
  per_min?: number | null;
  inflow_per_min?: number | null;
  people?: number | null;
  window_s: number;
  points: number;
  eta_s: Record<string, number | null>;
  above_s: Record<string, number>;
  peak_ppm2?: number | null;
  in_per_min?: number | null;
  out_per_min?: number | null;
  fill_eta_s?: Record<string, number | null>;
  phase?: PhaseMeter | null;
}

export interface PhaseMeter {
  window_s: number;
  speeds: number[];
  speed_mps: number | null;
  speed_per_min: number | null;
  swings: number;
  swing_share: number | null;
  pressure: number | null;
  pressure_peak: number | null;
}

export interface ZoneTally {
  camera_id: string;
  zone: string;
  at: string;
  sam: number;
  spark: number | null;
  spark_said: number | null;
  glimmer: number | null;
  glimmer_note: string;
  low: number;
  high: number;
  spread: number;
  disagree: boolean;
  crop: string | null;
  crop_box: [number, number, number, number] | null;
  points_px: Pt[];
  seconds: number;
  notes: string[];
}

export interface RouteCheck {
  action: string;
  to: string;
  measured: boolean;
  camera_id?: string | null;
  zone?: string | null;
  density_ppm2?: number | null;
  per_min?: number | null;
  filling: boolean;
  note: string;
  replaced: boolean;
}

export interface VenueAction {
  do: string;
  target: string;
  role: string;
  why: string;
  to?: string;
}

export interface Assessment {
  level: Level;
  zones: string[];
  why: string;
  forecast: string;
  minutes_to_danger: number | null;
  alert: boolean;
  staff_actions: string[];
  actions: VenueAction[];
  announce: { lang: string; text: string }[];
  check: string;
  phase?: string;
}

export interface CrowdCheck {
  agrees: boolean;
  seen: string;
  confidence: number;
}

export interface CrowdAlert {
  alert_id: string;
  camera_id: string;
  at: string;
  level: Level;
  assessment: Assessment;
  check: CrowdCheck | null;
  sent: boolean;
  held_reason?: string | null;
  check_note?: string | null;
  heatmap?: string | null;
  audio: {
    lang: string;
    text: string;
    audio?: string;
    heard?: string;
    wer?: string;
    meaning?: "same" | "different" | "unchecked";
    meaning_note?: string;
  }[];
  chain_hash?: string | null;
  notified?: string | null;
  assessed_at?: string | null;
  sent_at?: string | null;
  notified_at?: string | null;
  roles: string[];
  route_checks: RouteCheck[];
}

export interface AckState {
  alert_id: string;
  status: "open" | "acknowledged" | "false_alarm" | "none";
  escalating: boolean;
  notified: { role: string; at: string; status: string }[];
  taps: { role: string; button: string; action: string; at: string }[];
  arrival: {
    concept: string;
    zone: string;
    since: string;
    checks: number;
    seen_at: string | null;
    people: number;
    gave_up: boolean;
  } | null;
}

export interface Place {
  name: string;
  kind?: string;
  leads_to?: string[];
  camera_id?: string | null;
  zone?: string | null;
  note?: string;
  now?: {
    at: string;
    people: number;
    density_ppm2?: number | null;
    speed_mps?: number | null;
  };
}

export interface Venue {
  description: string;
  places: Place[];
  sample: boolean;
}

export interface CameraStatus {
  camera_id: string;
  name: string;
  watched: Watch | null;
  latest: CrowdReading | null;
  series: Record<string, (number | string | null)[]>;
  zone_series: Record<string, { t: string[]; people: number[]; density: (number | null)[] }>;
  forecast: ZoneForecast[];
  tally: ZoneTally | null;
  assessment: Assessment | null;
  assessed_at: string | null;
  assess_seconds: number | null;
  last_alert: CrowdAlert | null;
}

export interface CrowdStatus {
  cameras: CameraStatus[];
  alerts: CrowdAlert[];
  acks: Record<string, AckState>;
  venue: Venue;
  places: Place[];
}

export interface SetupProposal {
  camera_id: string;
  instruction: string;
  frame: string;
  width: number;
  height: number;
  seconds: number;
  draft: {
    summary: string;
    view: string;
    concept: string;
    grid: number;
    languages: string[];
    role: string;
    escalate_to: string[];
    calibration: string;
    zones: { name: string; why: string }[];
    places: Place[];
  };
  zones: CrowdZone[];
  role: string;
  escalate_to: string[];
}

export interface WatchRequest {
  camera_id: string;
  instruction: string;
  languages: string[];
  role: string;
  floor?: FloorPoints | null;
  zones: CrowdZone[];
  concept?: string | null;
  grid?: number | null;
  escalate_to: string[];
  ack_within_s: number;
}

export interface TruthZone {
  zone: string;
  readings: number;
  count_ratio: number | null;
  count_mae: number | null;
  density_ratio_median: number | null;
  sys_peak_ppm2: number | null;
  true_peak_ppm2: number | null;
  true_peak_clip_s: number | null;
  clip_s: number[];
  sys_density: (number | null)[];
  true_density: (number | null)[];
  truth_curve_s: number[];
  truth_curve_density: (number | null)[];
}

export interface EventReport {
  camera_id: string;
  name: string;
  clip_start: string | null;
  instruction: string | null;
  readings: number;
  reference_ppm2: number[];
  zones: {
    zone: string;
    readings: number;
    peak_density_ppm2: number | null;
    peak_at: string | null;
    peak_people: number | null;
    peak_heatmap: string | null;
    mean_density_ppm2: number | null;
    above_s: Record<string, number>;
  }[];
  timeline: {
    at: string;
    kind: string;
    level: string | null;
    text: string;
    ref: string | null;
    alert_id?: string | null;
    role?: string | null;
    seq?: number | null;
    hash?: string | null;
  }[];
  unknowns: string[];
  latency: {
    alert_id: string;
    level: string;
    sent: boolean;
    reading_at: string;
    assess_s: number | null;
    alert_s: number | null;
    checked: string;
  }[];
  evidence: {
    chain_ok: boolean | null;
    chain_files: string[];
    problem: string | null;
    records: { seq: number; kind: string; hash: string }[];
  };
  truth: TruthZone[] | null;
  truth_source: string | null;
  after_action: {
    headline: string;
    narrative: string;
    worked: string[];
    change: string[];
    cited: { text: string; records: number[] }[];
  } | null;
}

/** A file the crowd watch wrote (heatmap, recorded frame or announcement WAV). */
export function fileUrl(name: string): string {
  return `${API_PROXY}/crowd/files/${encodeURIComponent(name)}`;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PROXY}${path}`, withCsrf(init));
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body.detail !== undefined) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // keep the status line
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const crowdApi = {
  status: () => call<CrowdStatus>("/crowd/status"),
  setup: (camera_id: string, instruction: string) =>
    call<SetupProposal>("/crowd/setup", send("POST", { camera_id, instruction })),
  watch: (body: WatchRequest) => call<Watch>("/crowd/watch", send("POST", body)),
  unwatch: (camera_id: string) =>
    call<{ stopped: boolean }>(`/crowd/watch/${encodeURIComponent(camera_id)}`, {
      method: "DELETE",
    }),
  setVenue: (venue: Venue) => call<Venue>("/crowd/venue", send("PUT", venue)),
  ack: (alert_id: string, role: string, button: string) =>
    call<AckState>(
      `/crowd/alerts/${encodeURIComponent(alert_id)}/ack`,
      send("POST", { role, button }),
    ),
  report: (camera_id: string, narrate: boolean) =>
    call<EventReport>(
      `/crowd/report?camera_id=${encodeURIComponent(camera_id)}&narrate=${narrate ? "true" : "false"}`,
    ),
  setTruth: (camera_id: string, truth: unknown) =>
    call<{ zones: string[] }>(`/crowd/truth/${encodeURIComponent(camera_id)}`, send("PUT", truth)),
};

export type Link = "stream" | "polling" | "offline";

/**
 * Whether the page can show the live crowd watch: `owner` means the API refused a visitor (log
 * in), `unavailable` that this API has no crowd routes, `off` that crowd watch is switched off,
 * `offline` that the API did not answer within `ANSWER_WITHIN_MS`.
 */
export type Access = "loading" | "ok" | "owner" | "unavailable" | "off" | "offline";

export const ANSWER_WITHIN_MS = 8000;

/** What an HTTP status from `/crowd/status` means for the page. */
export function accessFor(status: number): Access {
  if (status === 401 || status === 403) return "owner";
  if (status === 404) return "unavailable";
  if (status === 503) return "off";
  return "offline";
}

/** The live crowd status: the SSE stream when it delivers, polling every 3 s otherwise. */
export function useCrowdStatus(): {
  status: CrowdStatus | null;
  link: Link;
  access: Access;
} {
  const [status, setStatus] = useState<CrowdStatus | null>(null);
  const [link, setLink] = useState<Link>("offline");
  const [access, setAccess] = useState<Access>("loading");
  const lastEvent = useRef(0);

  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;
    let timer = 0;
    const stop = (): void => {
      closed = true;
      window.clearInterval(timer);
      source?.close();
    };
    const poll = async (): Promise<void> => {
      if (closed || Date.now() - lastEvent.current < ANSWER_WITHIN_MS) return;
      let response: Response;
      try {
        response = await fetch(`${API_PROXY}/crowd/status`, { cache: "no-store" });
      } catch {
        setLink("offline");
        setAccess("offline");
        return;
      }
      if (!response.ok) {
        const found = accessFor(response.status);
        setLink("offline");
        setAccess(found);
        if (found === "owner" || found === "unavailable") stop(); // no point retrying
        return;
      }
      setStatus((await response.json()) as CrowdStatus);
      setLink("polling");
      setAccess("ok");
      if (source === null && typeof EventSource !== "undefined") {
        source = new EventSource(`${API_PROXY}/crowd/stream`);
        source.addEventListener("status", (event) => {
          lastEvent.current = Date.now();
          setStatus(JSON.parse((event as MessageEvent<string>).data) as CrowdStatus);
          setLink("stream");
          setAccess("ok");
        });
      }
    };
    const deadline = window.setTimeout(
      () => setAccess((now) => (now === "loading" ? "offline" : now)),
      ANSWER_WITHIN_MS,
    );
    void poll();
    timer = window.setInterval(() => void poll(), 3000);
    return () => {
      window.clearTimeout(deadline);
      stop();
    };
  }, []);

  return { status, link, access };
}

export const LEVEL_COLOUR: Record<string, string> = {
  normal: "#2e7d32",
  watch: "#b8860b",
  warning: "#e65100",
  critical: "#c62828",
};

/** Colour for a density in people per m² (display only; blue low to red high, top at 6). */
export function densityColour(value: number | null | undefined, alpha = 1): string {
  if (value === null || value === undefined) return `rgba(128,128,128,${alpha * 0.3})`;
  const x = Math.max(0, Math.min(1, value / 6));
  const hue = 220 - 220 * x;
  return `hsla(${hue.toFixed(0)}, 85%, 50%, ${alpha})`;
}

export function fmt(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "–" : value.toFixed(digits);
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "–";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleTimeString([], { hour12: false });
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  if (value < 90) return `${Math.round(value)} s`;
  return `${(value / 60).toFixed(1)} min`;
}
