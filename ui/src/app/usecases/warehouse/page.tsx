"use client";

// Warehouse forklift safety: the connected story on live data. A spoken or typed rule (any
// language) → a plan that says what it cannot check → Rule CI → approval → a live near miss →
// the verifier's verdict → the spoken warning and the phone alert → evidence → the OSHA
// refresher training it makes due → the toolbox talk → the worker's dispute → a permanent
// regression. Every number shown comes from the API (`/warehouse/*`, docs/usecases/warehouse.md).
// The routes are owner-only: a visitor gets the owner-only notice and the recorded run instead.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { IncidentPanel } from "./IncidentPanel";
import { NearMissPanel } from "./NearMissPanel";
import { ShiftPanel, VoiceReportPanel } from "./ReportsPanel";
import { RequestPanel } from "./RequestPanel";
import { StoryPanel } from "./StoryPanel";
import { WarehouseProof } from "./WarehouseProof";
import {
  accessOf,
  warehouse,
  type Access,
  type Heatmap,
  type NearMiss,
  type WarehouseStory,
} from "./warehouse";
import styles from "./warehouse.module.css";

const POLL_MS = 4000;
const PLANNING_POLL_MS = 1500; // while a plan is being planned, its steps show up quickly
const RECORDED_RUN = "/demo?demo=worker_passes_4m";

function Notice({ access }: { access: Exclude<Access, "ok" | "loading"> }) {
  const text = {
    owner: (
      <>
        The live warehouse story is for the site owner. <Link href="/login">Log in</Link> to run it.
      </>
    ),
    missing: <>The warehouse story is not available on this server yet.</>,
    down: <>The Rulecam API is not reachable right now.</>,
  }[access];
  return (
    <section className={styles.card} data-testid={`notice-${access}`}>
      <p>{text}</p>
      <p className={styles.muted}>
        Meanwhile, the same forklift story recorded live on 2 Nov (a worker 2.9 m from a moving
        forklift, confirmed by Muse Glimmer, and a seated driver correctly not alerted) replays with
        no model calls: <Link href={RECORDED_RUN}>open the recorded run (REPLAY)</Link>.
      </p>
    </section>
  );
}

export default function WarehousePage() {
  const [planId, setPlanId] = useState<string | null>(null);
  const [story, setStory] = useState<WarehouseStory | null>(null);
  const [access, setAccess] = useState<Access>("loading");
  const [rows, setRows] = useState<NearMiss[]>([]);
  const [heat, setHeat] = useState<Heatmap | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await warehouse.story(planId);
      setStory(next);
      setAccess("ok");
      const all = await warehouse.nearMisses();
      setRows(all);
      if (all.length > 0) {
        const camera = all.find((r) => r.event_id === (selected ?? next.event_id))?.camera_id;
        setHeat(await warehouse.heatmap(camera ?? null).catch(() => null));
      }
    } catch (failure) {
      const next = accessOf(failure);
      // Once shown, a missed poll keeps the page; only a refusal (logged out) hides it.
      setAccess((current) => (current === "ok" && next !== "owner" ? current : next));
    }
  }, [planId, selected]);

  const planning = story?.planning !== undefined && story?.planning?.total_s === null;
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), planning ? PLANNING_POLL_MS : POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh, planning]);

  const followedId = selected ?? story?.event_id ?? null;
  const followed = rows.find((r) => r.event_id === followedId) ?? null;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h2>Warehouse forklift safety</h2>
        <p className={styles.muted}>
          Every step below is read from what Rulecam recorded on this site today. Other safety
          cameras ask you to trust their numbers; Rulecam proves every rule before it goes live and
          lets the crew check every alert.
        </p>
      </header>
      <WarehouseProof />
      {access === "loading" && <p className={styles.muted}>Loading the story…</p>}
      {access !== "ok" && access !== "loading" && <Notice access={access} />}
      {access === "ok" && <StoryPanel story={story} onApproved={() => void refresh()} />}
      {access === "ok" && (
        <div className={styles.grid}>
          <div className={styles.column}>
            <RequestPanel onPlan={setPlanId} />
            <VoiceReportPanel onPlan={setPlanId} />
          </div>
          <div className={styles.column}>
            {followed ? (
              <IncidentPanel key={followed.event_id} near={followed} planId={followed.plan_id} />
            ) : (
              <section className={styles.card}>
                <h2>The near miss, and what happens next</h2>
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
            <ShiftPanel />
          </div>
        </div>
      )}
    </div>
  );
}
