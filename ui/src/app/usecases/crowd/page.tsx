"use client";

import { useState } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";

import { AlertsPanel } from "./AlertsPanel";
import { CrowdJourney } from "./CrowdJourney";
import { CrowdProof } from "./CrowdProof";
import styles from "./crowd.module.css";
import type { Access, CrowdStatus, Link } from "./crowdApi";
import { LEVEL_COLOUR, useCrowdStatus } from "./crowdApi";
import { LiveFrame } from "./LiveFrame";
import { RecordedRun } from "./RecordedRun";
import { ReportPanel } from "./ReportPanel";
import { SetupPanel } from "./SetupPanel";
import { TallyCard } from "./TallyCard";
import { VenueMap } from "./VenueMap";
import { AssessmentCard, ZonePanel } from "./ZonePanel";

const NOTICE: Record<Exclude<Access, "ok" | "owner">, string> = {
  loading: "Connecting to the crowd watch…",
  unavailable: "The live crowd watch is not available on this server.",
  off: "The live crowd watch is switched off on this server.",
  offline: "The Rulecam API did not answer, so the live crowd watch cannot be shown.",
};

function linkText(link: Link, access: Access): string {
  if (access !== "ok") return "not live";
  return link === "stream" ? "live (stream)" : link === "polling" ? "live (polling)" : "offline";
}

/**
 * Crowd early warning: set up from plain words, watch live (density, flow, the measured
 * countdown, Muse Spark's judgement with venue-tied actions, the Tier 1 check, the three-way
 * count, steward acks and announcements), then the report with its evidence and the ground
 * truth. Visitors, and anyone while no crowd camera is live, see a recorded run.
 */
export default function CrowdPage() {
  const { status, link, access } = useCrowdStatus();
  const live = access === "ok" && status !== null && status.cameras.length > 0;
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <span className={styles.small}>
            SAM 3.1 counts. Muse Spark judges and plans. The Tier 1 model checks the frames. Voice
            Transcribe hears the announcements back. Every alert is chained.
          </span>
        </div>
        <span className={styles.link} data-link={access === "ok" ? link : "none"}>
          {linkText(link, access)}
        </span>
      </header>
      <CrowdProof />
      <CrowdJourney />
      {access === "owner" && (
        <OwnerOnly checking={false} what="The live crowd watch (cameras, alerts and controls)" />
      )}
      {access !== "ok" && access !== "owner" && (
        <p className={styles.muted} role="status">
          {NOTICE[access]}
        </p>
      )}
      {access === "ok" && !live && (
        <p className={styles.muted} role="status">
          No crowd camera is live right now.
        </p>
      )}
      {live && status !== null && <Live status={status} />}
      {!live && access !== "loading" && <RecordedRun />}
    </div>
  );
}

function Live({ status }: { status: CrowdStatus }) {
  const [chosen, setChosen] = useState("");
  const cameras = status.cameras;
  const camera =
    cameras.find((c) => c.camera_id === chosen) ??
    cameras.find((c) => c.watched && c.latest) ??
    cameras.find((c) => c.latest) ??
    cameras[0];
  return (
    <>
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
            <TallyCard tally={camera.tally} />
            <div className={styles.card}>
              <h2>Venue</h2>
              <VenueMap
                places={status.places}
                sample={status.venue.sample}
                assessment={camera.assessment}
                routes={camera.last_alert?.route_checks ?? []}
              />
            </div>
            <AlertsPanel alerts={status.alerts} acks={status.acks} />
          </div>
        </div>
      )}
      <div className={styles.grid}>
        <SetupPanel cameras={cameras} current={camera?.camera_id} venue={status.venue} />
        <ReportPanel
          cameraIds={cameras.map((c) => ({ id: c.camera_id, name: c.name }))}
          current={camera?.camera_id}
        />
      </div>
    </>
  );
}
