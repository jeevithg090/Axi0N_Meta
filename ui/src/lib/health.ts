"use client";

// `GET /health` through the same-origin proxy, polled. A failed call is reported as an error with
// its time, never replaced by a remembered answer.

import { useEffect, useState } from "react";

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
  const w = entry.workers;
  const up = (w?.running ?? 0) + (w?.idle ?? 0) + (w?.initializing ?? 0);
  const workers = typeof entry.workers_max === "number" ? `${up} of ${entry.workers_max} workers up` : null;
  return { state: entry.state ?? (entry.up ? "up" : "down"), workers };
}

/** Whether the API says it is healthy and in live mode. */
export function isLive(load: HealthLoad): boolean {
  return load.phase === "ok" && load.health.ok === true && load.health.mode === "live";
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
