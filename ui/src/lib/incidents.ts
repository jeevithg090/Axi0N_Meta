// Incident reports (add-on 5.2): owner-only reads of one alert's chained records, and the
// download link for its PDF (or printable HTML when the API host has no Chromium).

import { ApiError } from "./api";
import { API_PROXY } from "./config";

/** One row of `GET /incidents`. */
export interface IncidentSummary {
  event_id: string;
  rule_id: string;
  camera_id: string;
  severity: string;
  ts: string;
  verdict: string | null;
}

/** One chained record of an incident, in chain order. */
export interface TimelineEntry {
  seq: number;
  ts: string;
  kind: string;
  summary: string;
  hash: string;
}

/** `GET /incidents/{event_id}`. */
export interface IncidentReport {
  event_id: string;
  site: string;
  day: string;
  rule_id: string;
  rule_text: string;
  clauses: string[];
  severity: string;
  camera_id: string;
  ts_start: string;
  ts_end: string;
  measurement: string | null;
  privacy_mode: string;
  timeline: TimelineEntry[];
  stills: number;
  verify_url: string;
  chain_records: number;
  chain_ok: boolean;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_PROXY}${path}`);
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

export const incidents = {
  list: (day?: string) =>
    get<IncidentSummary[]>(`/incidents${day ? `?day=${encodeURIComponent(day)}` : ""}`),
  get: (eventId: string) => get<IncidentReport>(`/incidents/${encodeURIComponent(eventId)}`),
  /** The report download: a PDF, or printable HTML when the server cannot print PDFs. */
  reportUrl: (eventId: string, format: "pdf" | "html" = "pdf") =>
    `${API_PROXY}/incidents/${encodeURIComponent(eventId)}/report` +
    (format === "html" ? "?format=html" : ""),
};
