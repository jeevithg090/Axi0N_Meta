"use client";

import type { SystemBadge } from "@/lib/health";
import { badgeLabel, type UiState } from "@/lib/store";

/** What each badge means, for the tooltip: the badge itself stays one word. */
const MEANING: Record<SystemBadge, string> = {
  LIVE: "The deployed Rulecam API answers /health and runs live",
  REPLAY: "A recorded run, replayed with no model calls",
  OFFLINE: "The Rulecam API did not answer /health: nothing on screen is live",
  "…": "Checking the Rulecam API",
};

/**
 * The LIVE / REPLAY badge. Rendered on every screen; it never disappears or switches silently.
 * The shell passes `label` (from `GET /health`, or REPLAY on a golden replay); without it the
 * badge falls back to the event stream's own state.
 */
export function ModeBadge({ state, label }: { state?: UiState; label?: SystemBadge }) {
  const shown: SystemBadge = label ?? (state ? badgeLabel(state) : "…");
  const tone = shown === "LIVE" ? "live" : shown === "REPLAY" ? "replay" : "offline";
  return (
    <span
      className={`badge badge-${tone}`}
      data-testid="mode-badge"
      role="status"
      aria-live="polite"
      title={MEANING[shown]}
      aria-label={shown === "…" ? "Checking" : undefined}
    >
      {shown}
    </span>
  );
}
