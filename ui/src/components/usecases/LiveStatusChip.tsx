"use client";

import { LiveBadge, Skeleton } from "@/components/kit";
import { gpuLine, isLive, useHealth } from "@/lib/health";
import { useDemo } from "@/lib/stream";

import styles from "./usecases.module.css";

/**
 * The system's state in one line for a use-case page: REPLAY on a golden replay (no polling),
 * otherwise LIVE or OFFLINE from `GET /health` with the verifier GPU's state.
 */
export function LiveStatusChip() {
  const demo = useDemo();
  const load = useHealth(15000, demo === false);

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
  const glimmer = load.phase === "ok" ? gpuLine(load.health.gpus?.glimmer) : null;
  return (
    <p className={styles.status} data-testid="usecase-status">
      <LiveBadge mode={isLive(load) ? "live" : "offline"} />
      <span className={styles.statusText}>
        {load.phase === "error" ? (
          "API not reachable just now"
        ) : (
          <>
            Glimmer GPU <b>{glimmer?.state ?? "unknown"}</b>
          </>
        )}
      </span>
    </p>
  );
}
