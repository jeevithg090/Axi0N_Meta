// Calls to the proof portal (`/proof`, src/rulecam/compliance/api.py) through the same-origin
// API proxy. Owner calls ride the owner's session (and CSRF token) like every other owner call;
// the shared pack and the day's hash list are public.

import { ApiError, withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";

import type { PackEntry, PackRequest, ShareLink, SharedPack } from "./types";

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

export const createPack = (request: PackRequest) => call<PackEntry>("/proof/packs", post(request));
export const listPacks = () => call<PackEntry[]>("/proof/packs");
export const getPack = (id: string) => call<PackEntry>(`/proof/packs/${encodeURIComponent(id)}`);
export const sharePack = (id: string, hours: number) =>
  call<ShareLink>(
    `/proof/packs/${encodeURIComponent(id)}/share`,
    post({ hours, ui_origin: window.location.origin }),
  );
export const revokePack = (id: string) =>
  call<{ revoked: boolean }>(`/proof/packs/${encodeURIComponent(id)}/revoke`, post({}));
export const getShared = (token: string) =>
  call<SharedPack>(`/proof/shared/${encodeURIComponent(token)}`);
export const listDays = () => call<string[]>("/evidence/days");

export function documentUrl(id: string, format: "pdf" | "html"): string {
  return `${API_PROXY}/proof/packs/${encodeURIComponent(id)}/document?format=${format}`;
}

export function sharedDocumentUrl(token: string, format: "pdf" | "html"): string {
  return `${API_PROXY}/proof/shared/${encodeURIComponent(token)}/document?format=${format}`;
}

/** The site's live record hashes for a day (public), or null when the site cannot be reached. */
export async function dayHashes(day: string): Promise<string[] | null> {
  try {
    const body = await call<{ n: number; hashes: string[] }>(`/evidence/${day}/hashes`);
    return Array.isArray(body.hashes) && body.n === body.hashes.length ? body.hashes : null;
  } catch {
    return null;
  }
}
