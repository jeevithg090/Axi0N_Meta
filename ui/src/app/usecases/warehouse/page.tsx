"use client";

// Warehouse forklift safety: the connected story on live data. A spoken or typed rule (any
// language) → a plan that says what it cannot check → Rule CI → approval → a live near miss →
// the verifier's verdict → the spoken warning and the phone alert → evidence → the OSHA
// refresher training it makes due → the toolbox talk → the worker's dispute → a permanent
// regression. Every number shown comes from the API (`/warehouse/*`, docs/usecases/warehouse.md).

import { useCallback, useEffect, useState } from "react";

import { IncidentPanel } from "./IncidentPanel";
import { NearMissPanel } from "./NearMissPanel";
import { ShiftPanel, VoiceReportPanel } from "./ReportsPanel";
import { RequestPanel } from "./RequestPanel";
import { StoryPanel } from "./StoryPanel";
import { warehouse, type Heatmap, type NearMiss, type WarehouseStory } from "./warehouse";
import styles from "./warehouse.module.css";

const POLL_MS = 4000;

export default function WarehousePage() {
  const [planId, setPlanId] = useState<string | null>(null);
  const [story, setStory] = useState<WarehouseStory | null>(null);
  const [rows, setRows] = useState<NearMiss[]>([]);
  const [heat, setHeat] = useState<Heatmap | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await warehouse.story(planId);
      setStory(next);
      setError(null);
      const all = await warehouse.nearMisses();
      setRows(all);
      if (all.length > 0) {
        const camera = all.find((r) => r.event_id === (selected ?? next.event_id))?.camera_id;
        setHeat(await warehouse.heatmap(camera ?? null).catch(() => null));
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The API is not reachable.");
    }
  }, [planId, selected]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const followedId = selected ?? story?.event_id ?? null;
  const followed = rows.find((r) => r.event_id === followedId) ?? null;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>Warehouse forklift safety</h1>
        <p className={styles.muted}>
          Live: every step below is read from what Rulecam recorded on this site today. Other safety
          cameras ask you to trust their numbers; Rulecam proves every rule before it goes live and
          lets the crew check every alert.
        </p>
        {error && <p className={styles.error}>{error}</p>}
      </header>
      <div className={styles.grid}>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <RequestPanel onPlan={setPlanId} />
          <StoryPanel story={story} onApproved={() => void refresh()} />
        </div>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          {followed ? (
            <IncidentPanel key={followed.event_id} near={followed} planId={followed.plan_id} />
          ) : (
            <section className={styles.card}>
              <h2>3. The near miss, and what happens next</h2>
              <p className={styles.muted}>
                Waiting for a near miss on a live camera (pair a phone or feed a clip).
              </p>
            </section>
          )}
          <NearMissPanel
            rows={rows}
            heat={heat}
            frameUrl={heat?.background ? warehouse.frameUrl(heat.background) : null}
            selected={followedId}
            onSelect={setSelected}
          />
          <VoiceReportPanel onPlan={setPlanId} />
          <ShiftPanel />
        </div>
      </div>
    </main>
  );
}
