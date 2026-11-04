"use client";

import { LiveBadge, Skeleton } from "@/components/kit";
import { gpuLine, healthBadge, useSharedHealth } from "@/lib/health";

import styles from "./landing.module.css";

const GPUS: { key: string; name: string }[] = [
  { key: "glimmer", name: "Glimmer GPU" },
  { key: "sam", name: "SAM 3.1 GPU" },
];

/** The live system, from `GET /health`, polled every 15 s. Unreachable says so; nothing is cached. */
export function SystemStatus() {
  const load = useSharedHealth();
  const health = load.phase === "ok" ? load.health : null;

  return (
    <section className={styles.status} aria-labelledby="status-title" data-testid="system-status">
      <div className={`container ${styles.statusGrid}`}>
        <div className={styles.statusHead}>
          <h2 id="status-title" className={styles.statusTitle}>
            The system right now
          </h2>
          {load.phase === "loading" ? (
            <Skeleton label="Checking the API" />
          ) : (
            <LiveBadge
              mode={healthBadge(load) === "LIVE" ? "live" : healthBadge(load) === "REPLAY" ? "replay" : "offline"}
              detail={`checked ${load.at.toISOString().slice(11, 19)} UTC`}
            />
          )}
        </div>

        {load.phase === "loading" && <Skeleton lines={2} label="Loading system status" />}

        {load.phase === "error" && (
          <p className={styles.statusError}>
            The API did not answer just now. The demo still works: it replays a recorded run with
            no model calls.
          </p>
        )}

        {health && (
          <dl className={styles.statusList}>
            <div>
              <dt>API</dt>
              <dd data-tone={health.ok ? "ok" : "bad"}>{health.ok ? "ok" : "not ok"}</dd>
            </div>
            <div>
              <dt>Mode</dt>
              <dd>{health.mode ?? "unknown"}</dd>
            </div>
            {GPUS.map((g) => {
              const line = gpuLine(health.gpus?.[g.key]);
              return (
                <div key={g.key}>
                  <dt>{g.name}</dt>
                  <dd>
                    {line.state}
                    {line.workers && <span className={styles.statusSub}>{line.workers}</span>}
                  </dd>
                </div>
              );
            })}
            <div>
              <dt>Revision</dt>
              <dd>{health.revision ? health.revision.slice(0, 7) : "unknown"}</dd>
            </div>
          </dl>
        )}
      </div>
    </section>
  );
}
