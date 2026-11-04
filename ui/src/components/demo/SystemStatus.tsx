"use client";

import { LiveBadge, Skeleton } from "@/components/kit";
import { healthBadge, useSharedHealth } from "@/lib/health";
import { modelRows } from "@/lib/models";

import styles from "./landing.module.css";

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
            {modelRows(health).map((m) => (
              <div key={m.key}>
                <dt>{m.role}</dt>
                <dd>
                  {m.model && m.model !== m.role ? `${m.model} · ${m.status}` : m.status}
                  {m.workers && <span className={styles.statusSub}>{m.workers}</span>}
                </dd>
              </div>
            ))}
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
