"use client";

import { useState } from "react";

import { AlertsPanel } from "./AlertsPanel";
import styles from "./crowd.module.css";
import { LEVEL_COLOUR, useCrowdStatus } from "./crowdApi";
import { LiveFrame } from "./LiveFrame";
import { ReportPanel } from "./ReportPanel";
import { SetupPanel } from "./SetupPanel";
import { VenueMap } from "./VenueMap";
import { AssessmentCard, ZonePanel } from "./ZonePanel";

/**
 * Crowd early warning: set up from plain words, watch live (density, flow, the measured
 * countdown, Muse Spark's judgement with venue-tied actions, Glimmer's check, steward acks and
 * announcements), then the report with its evidence and the ground-truth score.
 */
export default function CrowdPage() {
  const { status, link, error } = useCrowdStatus();
  const [chosen, setChosen] = useState("");
  const cameras = status?.cameras ?? [];
  const camera =
    cameras.find((c) => c.camera_id === chosen) ?? cameras.find((c) => c.watched) ?? cameras[0];
  const alerts = status?.alerts ?? [];

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Crowd early warning</h1>
          <span className={styles.small}>
            SAM 3.1 counts · Muse Spark judges and plans · Muse Glimmer checks the frames · MMS-TTS
            speaks · every alert chained
          </span>
        </div>
        <span className={styles.small}>
          {link === "stream" ? "live (stream)" : link === "polling" ? "live (polling)" : "offline"}
          {error ? ` · ${error}` : ""}
        </span>
      </header>

      <div className={styles.tabs}>
        {cameras.map((c) => (
          <button
            key={c.camera_id}
            type="button"
            className={camera?.camera_id === c.camera_id ? styles.tabActive : styles.tab}
            onClick={() => setChosen(c.camera_id)}
          >
            {c.name}
            {c.assessment && (
              <span
                className={styles.badge}
                style={{ background: LEVEL_COLOUR[c.assessment.level], marginLeft: 6 }}
              >
                {c.assessment.level}
              </span>
            )}
          </button>
        ))}
        {cameras.length === 0 && (
          <span className={styles.muted}>
            No crowd camera yet: pair a phone or feed a clip (scripts/crowd/feed_clip.py).
          </span>
        )}
      </div>

      {camera && (
        <div className={styles.grid}>
          <div className={styles.card}>
            <h2>{camera.name}</h2>
            {camera.watched && (
              <span className={styles.small}>
                Instruction: “{camera.watched.instruction}” · alerts {camera.watched.role}
                {camera.watched.escalate_to.length
                  ? ` then ${camera.watched.escalate_to.join(", ")}`
                  : ""}{" "}
                after {camera.watched.ack_within_s} s without a tap
              </span>
            )}
            <LiveFrame camera={camera} />
            <ZonePanel camera={camera} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
            <AssessmentCard camera={camera} />
            <div className={styles.card}>
              <h2>Venue</h2>
              <VenueMap
                places={status?.places ?? []}
                sample={status?.venue.sample ?? false}
                assessment={camera.assessment}
                routes={camera.last_alert?.route_checks ?? []}
              />
            </div>
            <AlertsPanel alerts={alerts} acks={status?.acks ?? {}} />
          </div>
        </div>
      )}

      <div className={styles.grid}>
        <SetupPanel
          cameras={cameras}
          venue={status?.venue ?? { description: "", places: [], sample: false }}
        />
        <ReportPanel cameraIds={cameras.map((c) => ({ id: c.camera_id, name: c.name }))} />
      </div>
    </main>
  );
}
