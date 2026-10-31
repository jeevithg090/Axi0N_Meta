"use client";

import { PLANNER_STEPS, type StepStatus } from "@/lib/plan";
import type { PlannerProgress } from "@/lib/store";

const MARK: Record<StepStatus, string> = { pending: "○", running: "◐", done: "✓", failed: "✗" };

/** The ten fixed planner steps, each with its status as a symbol and a word. */
export function StepProgress({ progress }: { progress: PlannerProgress }) {
  return (
    <ol className="steps" data-testid="step-progress" aria-label="Planner steps">
      {PLANNER_STEPS.map((step) => {
        const state = progress.steps[step];
        const status: StepStatus = state?.status ?? "pending";
        return (
          <li key={step} className={`step step-${status}`} data-status={status}>
            <span aria-hidden="true">{MARK[status]}</span> {step}
            <span className="visually-hidden"> ({status})</span>
            {state?.detail ? <span className="muted"> · {state.detail}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
