import type { ReactNode } from "react";

import {
  CAMPAIGN,
  CAMPAIGN_REPORT_MD,
  distanceLimit,
  firstEvent,
  repoFile,
  type CampaignTest,
} from "@/fixtures/live-campaign";

import styles from "./demo.module.css";

/** "violation" → "violation", "no_violation" → "no violation". */
export function verdictWords(verdict: string): string {
  return verdict.replace(/_/g, " ");
}

/** Seconds with one decimal, e.g. 12891 ms → "12.9 s". */
export function seconds(value: number, unit: "s" | "ms" = "s"): string {
  return `${(unit === "ms" ? value / 1000 : value).toFixed(1)} s`;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className={styles.kv}>
      <span>{label}</span>
      <span>{children}</span>
    </p>
  );
}

/** One recorded test: the request, what the engine measured, Glimmer's verdict and the outcome. */
export function LiveTestCard({ test }: { test: CampaignTest }) {
  const event = firstEvent(test);
  const rule = test.rules[0];
  const limit = rule ? distanceLimit(rule.predicate) : null;
  const clip = CAMPAIGN.clips[test.clip];
  const alert = event.decision.alert;
  return (
    <article
      className={`${styles.card} ${alert ? styles.alertCard : styles.rejectCard}`}
      data-testid={`live-${test.id}`}
    >
      <p className={styles.src}>
        {test.id} · {test.started_at.slice(0, 10)} {test.started_at.slice(11, 16)} UTC ·{" "}
        {test.camera_id}
      </p>
      <p className={styles.step}>{alert ? "Verified alert" : "Rejected by Glimmer · no alert"}</p>
      <p className={styles.quote}>“{test.request}”</p>
      {event.measurement && (
        <Row label="Distance the engine measured">
          {event.measurement.value.toFixed(2)} {event.measurement.unit}
          {limit !== null ? ` (the rule says under ${limit} m)` : ""}
        </Row>
      )}
      <Row label={`Glimmer (${event.verdict.model_version})`}>
        {verdictWords(event.verdict.verdict)} · P(violation) {event.verdict.score.toFixed(2)}
      </Row>
      <Row label="Glimmer's reasoning">
        <span className={styles.quote}>“{event.verdict.reason}”</span>
      </Row>
      <Row label="Decision">
        {alert ? "alert" : "no alert"} ({event.decision.reason})
      </Row>
      <Row label="Event → checked decision">
        {seconds(event.event_to_decision_s)} (Glimmer took {seconds(event.verdict.latency_ms, "ms")}
        )
      </Row>
      <Row label="Actions recorded (dry run, nothing was sent)">{event.actions.join("; ")}</Row>
      {clip && (
        <p className={styles.src}>
          Clip: {clip.description} ({clip.duration_s} s,{" "}
          <a href={clip.source_url}>{clip.folder.replace("pexels_", "Pexels ")}</a>, {clip.license})
        </p>
      )}
    </article>
  );
}

/**
 * The 1 Nov live campaign's forklift tests (run 2): a verified alert in English, the same rule
 * asked in Spanish, and the seated driver Glimmer rejected. Recorded, never shown as live.
 */
export function LiveAlerts() {
  return (
    <section className={styles.live} aria-label="Recorded live run" data-testid="live-alerts">
      <span className={styles.recorded}>
        RECORDED LIVE RUN · 1 NOV 2026 · NOT A REPLAY, NOT LIVE NOW
      </span>
      <h2 className={styles.h2}>Verified alerts from a live run</h2>
      <p>
        These three tests ran on the deployed stack on 1 Nov 2026 (the campaign&apos;s run 2, in
        prototype mode): a real stock video played into Rulecam as a phone camera, the planner built
        the rule, and Glimmer checked every candidate on the video frames. Every value below is
        copied from the run&apos;s own records.
      </p>
      <div className={styles.cards}>
        {CAMPAIGN.tests.map((test) => (
          <LiveTestCard key={test.id} test={test} />
        ))}
      </div>
      <p className={styles.src}>
        Source: <a href={repoFile(CAMPAIGN.source)}>{CAMPAIGN.source}</a> (the full write-up:{" "}
        <a href={repoFile(CAMPAIGN_REPORT_MD)}>{CAMPAIGN_REPORT_MD}</a>). No evidence strips from
        this run are published.
      </p>
    </section>
  );
}
