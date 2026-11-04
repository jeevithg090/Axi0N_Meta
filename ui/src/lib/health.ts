"use client";

// `GET /health` through the same-origin proxy, polled. A failed call is reported as an error with
// its time, never replaced by a remembered answer.

import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from "react";

import { API_PROXY } from "./config";

export interface GpuHealth {
  state?: string;
  up?: boolean;
  serverless?: boolean;
  workers_max?: number;
  workers?: { running?: number; idle?: number; initializing?: number };
  error?: string | null;
}

/** `GET /health`, as the API sends it (only the fields the UI shows are typed). */
export interface Health {
  ok?: boolean;
  site?: string;
  mode?: string;
  runtime?: boolean;
  revision?: string;
  gpus?: Record<string, GpuHealth | string>;
}

export type HealthLoad =
  | { phase: "loading" }
  | { phase: "error"; at: Date }
  | { phase: "ok"; health: Health; at: Date };

/** A GPU line: its state as the API wrote it, and its workers when it is serverless. */
export function gpuLine(entry: GpuHealth | string | undefined): { state: string; workers: string | null } {
  if (entry === undefined) return { state: "unknown", workers: null };
  if (typeof entry === "string") return { state: entry, workers: null };
  // The serverless counts exactly as the API reports them (they can exceed workers_max while
  // RunPod scales), never added up into a figure the API did not give.
  const w = entry.workers;
  const workers = w
    ? [`${w.running ?? 0} running`, `${w.idle ?? 0} idle`, ...(w.initializing ? [`${w.initializing} starting`] : [])].join(", ")
    : null;
  return { state: entry.state ?? (entry.up ? "up" : "down"), workers };
}

/** The header badge's words: the API's own badge strings, plus OFFLINE and "…" (not known yet). */
export type SystemBadge = "LIVE" | "REPLAY" | "OFFLINE" | "…";

/**
 * The header badge from `GET /health`, mapped the way the API's `badge_for` maps a mode: `replay`
 * is REPLAY, any other mode (`live`, `record`) is LIVE. No answer, or `ok` not true, is OFFLINE;
 * no answer yet is "…". The event socket never decides this.
 */
export function healthBadge(load: HealthLoad): SystemBadge {
  if (load.phase === "loading") return "…";
  if (load.phase === "error" || load.health.ok !== true) return "OFFLINE";
  return load.health.mode === "replay" ? "REPLAY" : "LIVE";
}

const HealthContext = createContext<HealthLoad | null>(null);

/**
 * One `GET /health` poll for the whole page (the header badge, the sidebar's GPU lines, the
 * landing's status band), so every place says the same thing. Disabled on a golden replay.
 */
export function HealthProvider({
  enabled,
  everyMs = 10000,
  children,
}: {
  enabled: boolean;
  everyMs?: number;
  children: ReactNode;
}) {
  const load = useHealth(everyMs, enabled);
  return createElement(HealthContext.Provider, { value: enabled ? load : null }, children);
}

/** The page's shared `/health` poll, or a poll of its own outside a HealthProvider. */
export function useSharedHealth(enabled = true, everyMs = 15000): HealthLoad {
  const shared = useContext(HealthContext);
  const own = useHealth(everyMs, enabled && shared === null);
  return shared ?? own;
}

/** Polls `GET /health` every `everyMs` (15 s by default); `enabled: false` polls nothing. */
export function useHealth(everyMs = 15000, enabled = true): HealthLoad {
  const [load, setLoad] = useState<HealthLoad>({ phase: "loading" });
  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    const poll = async () => {
      try {
        const res = await fetch(`${API_PROXY}/health`, { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const health = (await res.json()) as Health;
        if (active) setLoad({ phase: "ok", health, at: new Date() });
      } catch {
        if (active) setLoad({ phase: "error", at: new Date() });
      }
    };
    void poll();
    const id = window.setInterval(() => void poll(), everyMs);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [everyMs, enabled]);
  return load;
}
