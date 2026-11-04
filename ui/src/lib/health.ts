"use client";

// `GET /health` through the same-origin proxy, polled. A failed call is reported as an error with
// its time, never replaced by a remembered answer.

import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from "react";

import { API_PROXY } from "./config";

import type { GpuHealth, ModelsInUse } from "./models";

export { gpuLine, type GpuHealth } from "./models";

/** `GET /health`, as the API sends it (only the fields the UI shows are typed). */
export interface Health {
  ok?: boolean;
  site?: string;
  mode?: string;
  runtime?: boolean;
  revision?: string;
  gpus?: Record<string, GpuHealth | string>;
  /** The model each job runs on now (`models_in_use`); older APIs do not send it. */
  models?: ModelsInUse | null;
}

export type HealthLoad =
  | { phase: "loading" }
  | { phase: "error"; at: Date }
  | { phase: "ok"; health: Health; at: Date };

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
