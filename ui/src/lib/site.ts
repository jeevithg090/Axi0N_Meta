// Site tools (owner-only): live site context from search-grounded Muse Spark (`/context`),
// supermarket shelf gaps checked by SAM 3.1 (`/shelf`) and festival crowd density checked on
// video (`/crowd`). Every call goes through the same-origin rewrite with the session's CSRF token.

import { ApiError, withCsrf } from "./api";
import { API_PROXY } from "./config";

export type Vertical = "festival" | "supermarket" | "general";

/** One checked context item: its source was one of the pages the search returned. */
export interface ContextItem {
  kind: string;
  title: string;
  summary: string;
  when: string;
  source_url: string;
  source_title: string;
  watch: string;
  cited: boolean;
  relinked: boolean;
}

export interface DroppedItem {
  title: string;
  source_url: string;
  reason: string;
}

/** `GET` / `POST /context/{vertical}`. */
export interface SiteContext {
  vertical: Vertical;
  site_name: string;
  location: { city: string; region: string; country: string; timezone: string };
  asked_at: string;
  model: string;
  items: ContextItem[];
  dropped: DroppedItem[];
  searches: number;
  pages_seen: number;
}

export interface ShelfGap {
  number: number;
  box_xyxy: [number, number, number, number];
  section: string;
  neighbours: string;
  facings: number;
  product_cover: number;
}

/** `POST /shelf/scan-image` and `/shelf/scan`. */
export interface ShelfReport {
  camera_id: string;
  at: string | null;
  marked_image: string | null;
  width: number;
  height: number;
  products_seen: number;
  gaps: ShelfGap[];
  rejected: { box_xyxy: [number, number, number, number]; section: string; reason: string }[];
  model: string;
}

export interface CrowdEpisode {
  episode: {
    start: string;
    end: string;
    level: "normal" | "busy" | "crowded" | "critical";
    peak_density: number;
    peak_ts: string;
    peak_people: number;
  };
  check: {
    dense: boolean;
    movement: string;
    signs: { sign: string; at_s: number; what: string }[];
    summary: string;
    confidence: number;
  } | null;
  check_error: string | null;
  moments: { at: string; sign: string; what: string }[];
  escalate: boolean;
  disputed: boolean;
  reasons: string[];
}

/** `POST /crowd/scan`. */
export interface CrowdReport {
  camera_id: string;
  start: string;
  end: string;
  keyframes: number;
  calibrated: boolean;
  episodes: CrowdEpisode[];
  notes: string[];
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

export const site = {
  /** The last context, or `null` before the first refresh. */
  latestContext: async (vertical: Vertical): Promise<SiteContext | null> => {
    try {
      return await call<SiteContext>(`/context/${vertical}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },
  refreshContext: (vertical: Vertical, notes: string) =>
    call<SiteContext>(`/context/${vertical}`, post({ notes })),
  scanShelfImage: (file: File) => {
    const form = new FormData();
    form.append("image", file);
    return call<ShelfReport>("/shelf/scan-image", { method: "POST", body: form });
  },
  scanCrowd: (camera_id: string, start: string, end: string) =>
    call<CrowdReport>("/crowd/scan", post({ camera_id, start, end, verify: true })),
};

/** The marked image's URL for a shelf report (`null` without confirmed gaps). */
export function markedUrl(report: ShelfReport): string | null {
  if (!report.marked_image) return null;
  const name = report.marked_image.split(/[\\/]/).pop() ?? "";
  return `${API_PROXY}/shelf/marked/${encodeURIComponent(name)}`;
}

/** How a context item's source was checked, in words. */
export function sourceNote(item: ContextItem): string {
  if (item.relinked) return "source picked from the pages the search returned";
  if (item.cited) return "retrieved and cited";
  return "retrieved by the search";
}

/** The highest level reached by any episode, for the headline. */
export function worstLevel(report: CrowdReport): CrowdEpisode["episode"]["level"] {
  const rank = { normal: 0, busy: 1, crowded: 2, critical: 3 } as const;
  let worst: CrowdEpisode["episode"]["level"] = "normal";
  for (const e of report.episodes) if (rank[e.episode.level] > rank[worst]) worst = e.episode.level;
  return worst;
}
