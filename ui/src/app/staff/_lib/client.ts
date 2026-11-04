// Calls to `/staff` through the UI's same-origin rewrite (`/rulecam-api`), with the owner
// session's CSRF header on writes (`withCsrf`), like every other page.

import { ApiError, withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";

import type {
  ActionResult,
  AskReply,
  Lang,
  RoleInfo,
  SpeechLine,
  StaffAction,
  StaffConfig,
  StaffItem,
  StaffStats,
} from "./types";

export const STAFF_BASE = `${API_PROXY}/staff`;

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${STAFF_BASE}${path}`, withCsrf({ cache: "no-store", ...init }));
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

const form = (fields: Record<string, string | Blob | null | undefined>): RequestInit => {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value !== null && value !== undefined) data.append(key, value);
  }
  return { method: "POST", body: data };
};

const id = (itemId: string): string => encodeURIComponent(itemId);

export const staffApi = {
  config: () => call<StaffConfig>("/config"),
  roles: () => call<RoleInfo[]>("/roles"),
  feed: (role: string | null) =>
    call<StaffItem[]>(role ? `/feed?role=${encodeURIComponent(role)}` : "/feed"),
  act: (
    itemId: string,
    action: StaffAction,
    role: string,
    extra: { note?: string; to_role?: string } = {},
  ) => call<ActionResult>(`/items/${id(itemId)}/actions`, post({ action, role, ...extra })),
  done: (itemId: string, role: string, photo: Blob | null, note = "") =>
    call<ActionResult>(`/items/${id(itemId)}/done`, form({ role, note, photo })),
  seen: (itemId: string, role: string, clientMs: number | null) =>
    call<{ first: boolean; alert_to_phone_ms: number | null }>(
      `/items/${id(itemId)}/seen`,
      post({ role, client_ms: clientMs }),
    ),
  speech: (itemId: string, lang: Lang, role: string) =>
    call<SpeechLine>(`/items/${id(itemId)}/speech?lang=${lang}&role=${encodeURIComponent(role)}`),
  stats: (hours = 24) => call<StaffStats>(`/stats?hours=${hours}`),
  ask: (role: string, lang: Lang, audio: Blob | null, question = "") =>
    call<AskReply>("/ask", form({ role, lang, question, audio })),
  shelf: (role: string, photo: Blob) => call<StaffItem[]>("/shelf", form({ role, photo })),
};

/** A worker's spoken near-miss report (the warehouse's `POST /warehouse/reports/voice`). */
export interface NearMissReport {
  report_id: string;
  transcript: string;
  what_happened: string;
  matches_event_id: string | null;
  match_reason: string;
  measured_m: number | null;
  model: string;
}

export async function reportNearMiss(audio: Blob, lang: Lang): Promise<NearMissReport> {
  const response = await fetch(
    `${API_PROXY}/warehouse/reports/voice?lang=${lang}`,
    withCsrf({ method: "POST", headers: { "Content-Type": "audio/wav" }, body: audio }),
  );
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
  return (await response.json()) as NearMissReport;
}

export const mediaUrl = (itemId: string, index: number): string =>
  `${STAFF_BASE}/media/${id(itemId)}/${index}`;
export const audioUrl = (name: string): string => `${STAFF_BASE}/audio/${encodeURIComponent(name)}`;
export const streamUrl = (role: string | null): string =>
  role ? `${STAFF_BASE}/stream?role=${encodeURIComponent(role)}` : `${STAFF_BASE}/stream`;
