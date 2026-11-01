"use client";

import type { Countdown as CountdownValue } from "@/lib/ops";

const LABEL = {
  idle: "Ready",
  counting: "Keep washing",
  passed: "Done, thank you",
  failed: "Too short: wash again",
} as const;

/** The sink-tablet countdown: huge "8 / 20 s", frozen red with a text label on a fail. */
export function Countdown({ value }: { value: CountdownValue }) {
  const { state, elapsed, required } = value;
  return (
    <div
      className={`countdown countdown-${state}`}
      data-testid="countdown"
      data-state={state}
      role="status"
      aria-live="polite"
    >
      {state === "idle" ? (
        <div className="countdown-number">✓</div>
      ) : (
        <div className="countdown-number">
          {Math.floor(elapsed)} / {Math.round(required)} s
        </div>
      )}
      <div className="countdown-label">{LABEL[state]}</div>
    </div>
  );
}
