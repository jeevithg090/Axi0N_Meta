"use client";

import { modelLabel } from "@/lib/models";

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

/** The model that checked this run's frames, as its chained verdict records it (Muse Glimmer 30B). */
const checker =
  modelLabel(
    (run.report.evidence.records as { kind: string; tier?: string }[]).find((r) => r.kind === "verdict")?.tier,
  ) || undefined;

function speakCrowd(text: string, lang: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang === "hi" ? "hi-IN" : "en-US";
  window.speechSynthesis.speak(u);
}

/**
 * A run the live system recorded (4 Nov 2026), shown to visitors and whenever no crowd camera is
 * live: Spark's decision with the route guard, the three-way count, the steward loop, the
 * announcements heard back, and the report with its ground-truth score. Labelled as recorded.
 */
export function RecordedRun() {
  const peak = run.tallies.find((t) => t.camera_id === run.alert.camera_id) ?? null;
  const audioList = (run.alert as { audio?: { lang: string; text: string; heard?: string }[] }).audio ?? [];

  return (
    <section id="crowd-workspace" className={styles.card} aria-label="Recorded live run">
      <div className={styles.row}>
        <span className={styles.recorded}>{run.label}</span>
        <span className={styles.link} data-link="stream">
          {run.report.evidence.records.length} chain records verified
        </span>
      </div>
      <p className={styles.small} style={{ margin: 0 }}>
        {run.about}
      </p>

      {audioList.length > 0 && (
        <div className={styles.audioStrip}>
          <div>
            <strong>Announcements, as spoken and heard back</strong>
            <div className={styles.small}>
              Only announcements whose meaning survives round-trip transcription are broadcast (live, a Hindi announcement where “gate” came back as “crowd” was automatically stopped).
            </div>
          </div>
          <div className={styles.row}>
            {audioList.map((a) => (
              <button
                key={a.lang}
                type="button"
                className={styles.button}
                onClick={() => speakCrowd(a.text, a.lang)}
              >
                Play {a.lang === "hi" ? "Hindi (हिन्दी)" : "English"} announcement
              </button>
            ))}
          </div>
        </div>
      )}

      <div className={styles.grid}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          <AssessmentView
            assessment={run.assessment}
            routes={run.alert.route_checks}
            readingAt={run.alert.at}
            seconds={null}
          />
          <TallyCard tally={peak} checker={checker} />
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
            checker={checker}
          />
        </div>
      </div>
      <h2>The report after the run</h2>
      <ReportView report={run.report} />
    </section>
  );
}
