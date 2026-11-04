"use client";

import { LiveBadge, Skeleton } from "@/components/kit";
import { healthBadge, useSharedHealth } from "@/lib/health";
import { modelRows } from "@/lib/models";
import { useDemo } from "@/lib/stream";

import styles from "./usecases.module.css";

/**
 * The system's state in one line for a use-case page: REPLAY on a golden replay (no polling),
 * otherwise LIVE or OFFLINE from `GET /health` with the Tier 1 check's model and state.
 */
export function LiveStatusChip() {
  const demo = useDemo();
  const load = useSharedHealth(demo === false);

  if (demo) {
    return (
      <p className={styles.status}>
        <LiveBadge mode="replay" detail="a recorded run, no model calls" />
      </p>
    );
  }
  if (demo === null || load.phase === "loading") {
    return (
      <p className={styles.status}>
        <Skeleton label="Checking the API" />
      </p>
    );
  }
  const tier1 = load.phase === "ok" ? modelRows(load.health)[0] : undefined;
  return (
    <p className={styles.status} data-testid="usecase-status">
      <LiveBadge mode={healthBadge(load) === "LIVE" ? "live" : healthBadge(load) === "REPLAY" ? "replay" : "offline"} />
      <span className={styles.statusText}>
        {load.phase === "error" ? (
          "API not reachable just now"
        ) : (
          <>
            {tier1?.model ? `Tier 1 · ${tier1.model}` : "Tier 1 check"} <b>{tier1?.status ?? "unknown"}</b>
          </>
        )}
      </span>
    </p>
  );
}
