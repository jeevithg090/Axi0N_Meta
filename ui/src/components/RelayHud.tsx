"use client";

import { metaCount, type RelayState } from "@/lib/relay";

/** The relay HUD strip: only models that actually ran in this segment; non-Meta parts are grey. */
export function RelayHud({ relay }: { relay: RelayState }) {
  return (
    <div className="hud" data-testid="relay-hud" aria-label="Models used in this segment">
      <span className="hud-segment">{relay.segment ?? "idle"}</span>
      <span className="hud-count" data-testid="hud-count">
        {metaCount(relay)} Meta model{metaCount(relay) === 1 ? "" : "s"}
      </span>
      {relay.models.map((m) => (
        <span
          key={m.model}
          className={m.meta ? "hud-chip" : "hud-chip hud-chip-other"}
          data-meta={m.meta}
          title={`${m.calls} call(s), last ${m.lastLatencyMs} ms`}
        >
          {m.model}
        </span>
      ))}
    </div>
  );
}
