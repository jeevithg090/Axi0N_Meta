"use client";

// What the crowd watch proved in the recorded run (4 Nov): one reading at display size, Muse
// Spark's judgement and Glimmer's check beside it, then the alert loop as a timeline and the
// announcement as it was heard back. Every value is read from recorded-run.json.

import { Timeline, TimelineItem, type TimelineTone } from "@/components/kit";
import { UseCaseProof } from "@/components/usecases/UseCaseProof";

import styles from "./crowd.module.css";
import recorded from "./recorded-run.json";

interface Recorded {
  report: {
    zones: { zone: string; peak_density_ppm2: number | null; peak_people: number | null }[];
    truth?: { true_peak_ppm2: number; count_ratio: number }[];
    latency?: { assess_s: number }[];
    after_action?: { headline?: string };
  };
  alert: {
    at: string;
    assessed_at?: string;
    sent_at?: string;
    notified_at?: string;
    check?: { agrees?: boolean; confidence?: number };
    audio?: { lang: string; text: string; heard?: string }[];
  };
  ack: { notified: { role: string; at: string }[]; taps: { role: string; button: string; at: string }[]; arrival?: { concept: string; zone: string; seen_at: string } };
  assessment: { level: string; why: string; staff_actions?: string[] };
}

const run = recorded as unknown as Recorded;
const role = (r: string) => r.replace(/_/g, " ");
const LANG: Record<string, string> = { hi: "Hindi", en: "English" };

/** "cleared by 01:37:58Z" from the after-action headline, as an ISO time on the run's day. */
function clearedAt(): string | null {
  const m = /cleared by (\d{2}:\d{2}:\d{2})Z/.exec(run.report.after_action?.headline ?? "");
  return m ? `${run.alert.at.slice(0, 10)}T${m[1]}Z` : null;
}

interface LoopEntry {
  time: string;
  title: string;
  tone: TimelineTone;
  meta?: string;
}

/** The alert loop in time order, from the run's own timestamps. */
function loop(peak: number | null, zone: string | undefined, cleared: string | null): LoopEntry[] {
  const iso = (t: string) => t.replace("+00:00", "Z");
  const entries: LoopEntry[] = [
    { time: run.alert.at, title: `Reading: ${peak?.toFixed(1) ?? "?"} people/m²`, tone: "instrument", meta: zone },
  ];
  if (run.alert.assessed_at)
    entries.push({ time: run.alert.assessed_at, title: `Muse Spark: ${run.assessment.level}`, tone: "instrument", meta: run.assessment.staff_actions?.[0] });
  for (const n of run.ack.notified) entries.push({ time: n.at, title: `${role(n.role)} alerted`, tone: "neutral" });
  if (run.alert.sent_at) entries.push({ time: run.alert.sent_at, title: "Glimmer confirmed; the alert is chained", tone: "instrument" });
  for (const t of run.ack.taps) entries.push({ time: iso(t.at), title: `${role(t.role)}: “${t.button}”`, tone: "person" });
  if (run.ack.arrival)
    entries.push({ time: run.ack.arrival.seen_at, title: `Staff seen at the gate (${run.ack.arrival.concept})`, tone: "instrument", meta: run.ack.arrival.zone });
  if (cleared) entries.push({ time: cleared, title: "Cleared", tone: "ok" });
  return entries.sort((a, b) => a.time.localeCompare(b.time));
}

export function CrowdProof() {
  const gate = run.report.zones.find((z) => z.zone.startsWith("gate")) ?? run.report.zones[0];
  const truth = run.report.truth?.[0];
  const assess = run.report.latency?.[0]?.assess_s;
  const cleared = clearedAt();
  const confidence = run.alert.check?.confidence;
  return (
    <UseCaseProof
      id="crowd-proof"
      testId="crowd-proof"
      title="What the crowd watch proved"
      badge={{ mode: "replay", detail: "recorded 4 Nov 2026" }}
      reading={{
        value: gate?.peak_density_ppm2 != null ? gate.peak_density_ppm2.toFixed(1) : "unknown",
        unit: "people/m²",
        caption: `${gate?.peak_people ?? "unknown"} people in the ${gate?.zone ?? "gate"} at the peak, counted by SAM 3.1 on research footage (the Jülich bottleneck experiment).`,
      }}
      verdict={
        <p className={styles.proofLine}>
          <span className="state bad">{run.assessment.level}</span>{" "}
          {run.alert.check?.agrees ? "Glimmer confirmed it on the frames" : "Glimmer did not confirm it"}
          {typeof confidence === "number" ? ` (${confidence.toFixed(2)})` : ""}.
        </p>
      }
      reason={run.assessment.why}
      chips={[
        { model: "SAM 3.1", job: "counted heads" },
        { model: "Muse Spark 1.3", job: "judged and planned", ms: typeof assess === "number" ? assess * 1000 : null },
        { model: "Muse Glimmer 30B", job: "checked the frames" },
      ]}
      footnote={
        truth
          ? `Against the experiment's tracked heads: true peak ${truth.true_peak_ppm2.toFixed(1)} people/m², count ratio ${truth.count_ratio.toFixed(3)}.`
          : undefined
      }
    >
      <section className={styles.card} aria-labelledby="crowd-loop">
        <h3 id="crowd-loop">From the reading to a cleared gate</h3>
        <Timeline label="The crowd alert, minute by minute">
          {loop(gate?.peak_density_ppm2 ?? null, gate?.zone, cleared).map((e) => (
            <TimelineItem key={`${e.time}-${e.title}`} time={e.time} title={e.title} tone={e.tone} meta={e.meta} />
          ))}
        </Timeline>
      </section>
      <section className={styles.card} aria-labelledby="crowd-heard">
        <h3 id="crowd-heard">The announcement, heard back</h3>
        <ul className={styles.list}>
          {(run.alert.audio ?? []).map((a) => (
            <li key={a.lang}>
              <span className={styles.small}>{LANG[a.lang] ?? a.lang}</span>
              <br />“{a.text}”
              {a.heard && <span className={styles.small}> Heard back by Muse Voice Transcribe.</span>}
            </li>
          ))}
        </ul>
      </section>
    </UseCaseProof>
  );
}
