"use client";

import { useEffect, useState } from "react";

import { api, type CostReport, type RuntimeMetrics } from "@/lib/api";
import { useDemo } from "@/lib/stream";

export const COST_POLL_MS = 15_000;

/** The spend in the header: $ per camera-hour, and a red badge while a camera is over budget. */
export function CostBadge({ cost }: { cost: CostReport | null }) {
  if (cost === null) return null;
  const rate = cost.usd_per_camera_hour;
  const label = rate === null ? "$ —/cam-h" : `$${rate.toFixed(2)}/cam-h`;
  if (!cost.alarm) {
    return (
      <span className="pill" data-testid="cost-badge" title="Model and GPU spend per camera-hour">
        {label}
      </span>
    );
  }
  const cameras = cost.over_budget.join(", ");
  return (
    <span
      className="badge badge-alarm"
      data-testid="cost-badge"
      role="alert"
      title={`Over $${cost.budget_usd_per_camera_hour.toFixed(2)}/camera-hour: ${cameras}`}
    >
      <span className="badge-dot" aria-hidden="true" />
      BUDGET · {cameras}
    </span>
  );
}

/** Polls `/runtime/metrics` (none in demo mode or without a runtime: `null`). */
export function useRuntimeMetrics(pollMs: number = COST_POLL_MS): RuntimeMetrics | null {
  const demo = useDemo();
  const [metrics, setMetrics] = useState<RuntimeMetrics | null>(null);
  useEffect(() => {
    if (demo !== false) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const report = await api.runtimeMetrics();
        if (!cancelled) setMetrics(report);
      } catch {
        if (!cancelled) setMetrics(null); // no runtime, or the API is unreachable: no badges
      }
    };
    void load();
    const timer = setInterval(() => void load(), pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [demo, pollMs]);
  return metrics;
}

/** The cost report from `/runtime/metrics`. */
export function useCost(pollMs: number = COST_POLL_MS): CostReport | null {
  return useRuntimeMetrics(pollMs)?.cost ?? null;
}
