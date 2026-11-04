"use client";

import { AlertsPanel } from "./AlertsPanel";
import styles from "./crowd.module.css";
import type {
  AckState,
  Assessment,
  CrowdAlert,
  EventReport,
  PhaseMeter,
  Place,
  Venue,
  ZoneTally,
} from "./crowdApi";
import recorded from "./recorded-run.json";
import { ReportView } from "./ReportPanel";
import { TallyCard } from "./TallyCard";
import { VenueMap } from "./VenueMap";
import { AssessmentView, PhaseView } from "./ZonePanel";

interface Recorded {
  label: string;
  about: string;
  report: EventReport;
  alert: CrowdAlert;
  ack: AckState;
  assessment: Assessment;
  side_alerts: CrowdAlert[];
  tallies: ZoneTally[];
  places: Place[];
  venue: Venue;
  phase: { main: PhaseMeter | null; side: PhaseMeter | null };
}

const run = recorded as unknown as Recorded;

/**
 * A run the live system recorded (4 Nov 2026), shown to visitors and whenever no crowd camera is
 * live: Spark's decision with the route guard, the three-way count, the steward loop, the
 * announcements heard back, and the report with its ground-truth score. Labelled as recorded.
 */
export function RecordedRun() {
  const peak = run.tallies.find((t) => t.camera_id === run.alert.camera_id) ?? null;
  return (
    <section className={styles.card} aria-label="Recorded live run">
      <div className={styles.row}>
        <span className={styles.recorded}>{run.label}</span>
      </div>
      <p className={styles.small} style={{ margin: 0 }}>
        {run.about}
      </p>
      <div className={styles.grid}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          <AssessmentView
            assessment={run.assessment}
            routes={run.alert.route_checks}
            readingAt={run.alert.at}
            seconds={null}
          />
          <TallyCard tally={peak} />
          {run.phase.main && (
            <div className={styles.card}>
              <h3>Main gate approach</h3>
              <PhaseView phase={run.phase.main} />
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          <div className={styles.card}>
            <h2>Venue</h2>
            <VenueMap
              places={run.places}
              sample={run.venue.sample}
              assessment={run.assessment}
              routes={run.alert.route_checks}
            />
          </div>
          <AlertsPanel
            alerts={[run.alert, ...run.side_alerts]}
            acks={{ [run.alert.alert_id]: run.ack }}
            readOnly
          />
        </div>
      </div>
      <h2>The report after the run</h2>
      <ReportView report={run.report} />
    </section>
  );
}
