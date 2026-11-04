"use client";

import { badgeLabel, type UiState } from "@/lib/store";

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
    >
      {label}
    </span>
  );
}
