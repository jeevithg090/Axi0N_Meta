"use client";

import { badgeLabel, type UiState } from "@/lib/store";

/** What each badge means, for the tooltip: the badge itself stays one word. */
const MEANING: Record<ReturnType<typeof badgeLabel>, string> = {
  LIVE: "Live events from the deployed stack",
  REPLAY: "A recorded run, replayed with no model calls",
  OFFLINE: "The live event socket is not connected: nothing on screen is live",
  "…": "Connecting to the live event socket",
};

/** The LIVE / REPLAY badge. Rendered on every screen; it never disappears or switches silently. */
export function ModeBadge({ state }: { state: UiState }) {
  const label = badgeLabel(state);
  const tone = label === "LIVE" ? "live" : label === "REPLAY" ? "replay" : "offline";
  return (
    <span
      className={`badge badge-${tone}`}
      data-testid="mode-badge"
      role="status"
      aria-live="polite"
      title={MEANING[label]}
      aria-label={label === "…" ? "Connecting" : undefined}
    >
      {label}
    </span>
  );
}
