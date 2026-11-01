"use client";

import { useEffect, useState } from "react";

import { api, type CostReport } from "@/lib/api";
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

/** Polls `/runtime/metrics` for the cost report (none in demo mode or without a runtime). */
export function useCost(pollMs: number = COST_POLL_MS): CostReport | null {
  const demo = useDemo();
  const [cost, setCost] = useState<CostReport | null>(null);
  useEffect(() => {
    if (demo !== false) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const metrics = await api.runtimeMetrics();
        if (!cancelled) setCost(metrics.cost ?? null);
      } catch {
        if (!cancelled) setCost(null); // no runtime, or the API is unreachable: no badge
      }
    };
    void load();
    const timer = setInterval(() => void load(), pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [demo, pollMs]);
  return cost;
}
