"use client";

// The 4 Nov 2026 live warehouse campaign (reports/eval/2026-11-04-warehouse-live.md) as a recorded
// showcase. Measurements, verdicts, timings and quotes are copied from that report. Event ids and
// clock times in the near-miss table are labels for the three measured passes, not log entries.

import { useState } from "react";

import { LiveBadge } from "@/components/kit";

import type { NearMiss, WarehouseStory } from "./warehouse";
import styles from "./warehouse.module.css";

const REPORT = "reports/eval/2026-11-04-warehouse-live.md";

const BASE: Omit<NearMiss, "event_id" | "ts" | "lang" | "rule_id" | "rule_text" | "separation_m" | "closest_m" | "vehicle_speed_mps" | "verdict_score" | "verdict_reason" | "actions" | "acked_by" | "disputes" | "regression" | "training_done" | "ttc_s"> = {
  camera_id: "phone-6",
  plan_id: null,
  severity: "high",
  limit_m: 4,
  calibration: "person height 1.7 m",
  calibration_err_m: 0.15,
  verdict: "violation",
  verifier: "muse-glimmer-30b",
  alert: true,
  route: "auto",
  foot_px: null,
};

export const RECORDED_NEAR_MISSES: NearMiss[] = [
  {
    ...BASE,
    event_id: "run 5 · 3.28 m pass",
    ts: "2026-11-04T01:07:00Z",
    lang: "es",
    rule_id: "forklift 4 m (es)",
    rule_text: "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento.",
    separation_m: 3.28,
    closest_m: null,
    vehicle_speed_mps: null,
    ttc_s: null,
    verdict_score: 0.99,
    verdict_reason:
      "Glimmer 30B confirmed a violation in 17.1 s. Spark read the forklift's painted unit number, K 35 (frames 2–3), and said refresher training applies.",
    actions: ["phone alert to safety_officer, On my way tapped and chained", "refresher training marked done and chained"],
    acked_by: ["safety_officer"],
    disputes: [],
    regression: false,
    training_done: true,
  },
  {
    ...BASE,
    event_id: "run 5 · 3.996 m pass",
    ts: "2026-11-04T01:07:30Z",
    lang: "es",
    rule_id: "forklift 4 m (es)",
    rule_text: "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento.",
    separation_m: 3.996,
    closest_m: null,
    vehicle_speed_mps: null,
    ttc_s: null,
    verdict_score: 0.99,
    verdict_reason:
      "Confirmed by Glimmer in 12.5 s. A worker then disputed it from the QR page and Muse Spark 1.3 upheld the dispute in 28.3 s, because the strip's ruler read 4.9–5.1 m. That exposed a ruler bug, since fixed.",
    actions: ["worker dispute upheld by Muse Spark 1.3", "false_alarm label and a permanent Rule CI regression stored"],
    acked_by: [],
    disputes: ["upheld"],
    regression: true,
    training_done: false,
  },
  {
    ...BASE,
    event_id: "run 7 · 3.22 m pass (after the ruler fix)",
    ts: "2026-11-04T01:42:00Z",
    lang: "en",
    rule_id: "forklift 4 m (en)",
    rule_text: "Keep people at least 4 metres away from a moving forklift.",
    separation_m: 3.22,
    closest_m: null,
    vehicle_speed_mps: null,
    ttc_s: null,
    verdict_score: 0.98,
    verdict_reason: "Confirmed by Glimmer 30B; the strip's ruler now reads 3.1–3.3 m, matching the measurement.",
    actions: ["strip ruler checked against the measurement"],
    acked_by: [],
    disputes: [],
    regression: false,
    training_done: false,
  },
];

export const RECORDED_WAREHOUSE_STORY: WarehouseStory = {
  plan_id: null,
  approved: true,
  request: "Mantener a las personas a al menos 4 metros de una carretilla elevadora en movimiento.",
  request_lang: "es",
  rules: [{ id: "forklift 4 m", severity: "high" }],
  cant_check: [
    {
      concern: "Keep people 4 m from a moving forklift",
      status: "caveat",
      explanation: "I can check this, with a caveat: camera 'phone-6' calibration error 0.15 m > 0.10 m",
    },
  ],
  event_id: RECORDED_NEAR_MISSES[0]!.event_id,
  planning: null,
  steps: [
    { key: "request", title: "Say the rule (Spanish)", status: "done", detail: "Spoken or typed; Voice Transcribe writes it down.", refs: {} },
    { key: "plan", title: "Plan, with its caveats", status: "done", detail: "Warm stage camera: 41.8 s from request to plan (309.6 s before the speed-up).", refs: {} },
    { key: "rule_ci", title: "Rule CI", status: "done", detail: "Boundary 16/16, mutations 6/6, in 0.1–0.2 s.", refs: {} },
    { key: "approve", title: "A person approves", status: "done", detail: "Nothing ran before this.", refs: {} },
    { key: "near_miss", title: "A live near miss", status: "done", detail: "9 candidates on 5 cameras: 3.28–4.00 m on the worker clip, 0.0 m on the driver clip.", refs: {} },
    { key: "verdict", title: "Glimmer's verdict", status: "done", detail: "4 of 4 confirmed, 0.98–0.99, in 12.5–21.7 s.", refs: {} },
    { key: "warning", title: "Spoken warning", status: "done", detail: "Spark wrote it, MMS-TTS spoke it, Voice Transcribe heard it back.", refs: {} },
    { key: "duty", title: "OSHA refresher duty", status: "done", detail: "29 CFR 1910.178(l)(4)(ii)(B); forklift unit K 35 read from the frames.", refs: {} },
    { key: "talk", title: "Toolbox talk", status: "done", detail: "Two-minute Spanish talk with a Muse Image drawing, 32.3–38.7 s.", refs: {} },
    { key: "dispute", title: "Worker dispute and regression", status: "done", detail: "Upheld by Muse Spark 1.3 in 28.3 s; stored as a permanent regression.", refs: {} },
  ],
  near_misses: RECORDED_NEAR_MISSES,
};

type Tab = "warning" | "osha" | "dispute" | "shift";

const TABS: { id: Tab; label: string }[] = [
  { id: "warning", label: "Spoken warning, heard back" },
  { id: "osha", label: "OSHA refresher duty" },
  { id: "dispute", label: "Worker dispute" },
  { id: "shift", label: "Voice report and shift report" },
];

export function WarehouseRecordedRun({
  useRecorded,
  onToggleRecorded,
  canUseLive,
}: {
  useRecorded: boolean;
  onToggleRecorded?: (next: boolean) => void;
  canUseLive: boolean;
}) {
  const [tab, setTab] = useState<Tab>("warning");

  return (
    <section className={styles.recordedBox} aria-labelledby="wh-recorded-head" data-testid="warehouse-recorded-run">
      <div className={styles.recordedTop}>
        <div className={styles.row}>
          <LiveBadge mode="replay" detail="recorded 4 Nov 2026, live models" />
          <span className={styles.chip}>chain intact, 287 records</span>
        </div>
        <div className={styles.recordedTitleBar}>
          <div>
            <h2 id="wh-recorded-head" style={{ margin: 0 }}>
              The 4 Nov live campaign
            </h2>
            <p className={styles.muted} style={{ margin: "4px 0 0" }}>
              Real stock forklift video fed in as paired phone cameras, against the live Meta models and our
              own GPUs. Every number is quoted from {REPORT}; the event names in the table below are labels,
              not log ids.
            </p>
          </div>
          {canUseLive && onToggleRecorded && (
            <div className={styles.row}>
              <button
                type="button"
                className={`${styles.button} ${useRecorded ? styles.primary : ""}`}
                aria-pressed={useRecorded}
                onClick={() => onToggleRecorded(true)}
              >
                Recorded run
              </button>
              <button
                type="button"
                className={`${styles.button} ${!useRecorded ? styles.primary : ""}`}
                aria-pressed={!useRecorded}
                onClick={() => onToggleRecorded(false)}
              >
                Live server
              </button>
            </div>
          )}
        </div>

        <div className={styles.kpiRow} role="list" aria-label="Recorded warehouse results">
          <div className={styles.kpiCard} role="listitem">
            <span className={styles.kpiNum}>3.28 m</span>
            <span className={styles.kpiSub}>A worker pass on 4 Nov, confirmed by Glimmer 30B at 0.99 in 17.1 s</span>
          </div>
          <div className={styles.kpiCard} role="listitem">
            <span className={styles.kpiNum}>41.8 s</span>
            <span className={styles.kpiSub}>Request to proven plan on a warm camera, from 309.6 s</span>
          </div>
          <div className={styles.kpiCard} role="listitem">
            <span className={styles.kpiNum}>16/16 · 6/6</span>
            <span className={styles.kpiSub}>Rule CI boundary traces and mutants, on all nine plans</span>
          </div>
          <div className={styles.kpiCard} role="listitem">
            <span className={styles.kpiNum}>28.3 s</span>
            <span className={styles.kpiSub}>A worker's dispute upheld, which exposed a ruler bug</span>
          </div>
        </div>

        <div className={styles.tabRow} role="tablist" aria-label="Recorded warehouse detail">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`${styles.tabPill} ${tab === t.id ? styles.tabPillActive : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "warning" && (
        <div className={styles.grid}>
          <div className={styles.subCard}>
            <h3>Spoken, then heard back</h3>
            <p>
              Spark writes the warning, Meta MMS-TTS speaks it and Muse Voice Transcribe listens back. The
              Spanish warnings passed at word error rates of 0.09, 0.15 and 0.18 (13.3–13.6 s each).
            </p>
            <blockquote className={styles.quote}>
              Bilingual warning, Spanish then English in one WAV (16.5 s): the Spanish was heard back word for
              word (WER 0.00, passed). In English, “forklift” was heard as “for Cliff” (WER 0.27) and the check{" "}
              <strong>flagged it as failed</strong>. That is what the ear-check is for.
            </blockquote>
          </div>
          <div className={styles.subCard}>
            <h3>The WhatsApp voice note</h3>
            <p>
              The bilingual note is 32.8 KB of Ogg/Opus (limit 512 KB). No recipient was configured, so it
              ran as a dry run and chained “would send the spoken warning (es+en) as a WhatsApp voice note to
              supervisor”. The phone alert went to the safety officer over the LAN; tapping On my way chained
              the acknowledgement.
            </p>
          </div>
        </div>
      )}

      {tab === "osha" && (
        <div className={styles.grid}>
          <div className={styles.subCard}>
            <h3>
              Refresher training <span className={styles.chip}>done and chained</span>
            </h3>
            <p>
              Spark found 29 CFR 1910.178(l)(4)(ii)(B) with web search, read the incident and its frames, and
              said the clause applies. On the 3.28 m near miss it read the forklift&apos;s painted unit number,{" "}
              <strong>K 35</strong> (“white marking on side panel, frames 2–3”). Marking the training done chained a
              refresher_training_done record (17.5–31.8 s per call).
            </p>
          </div>
          <div className={styles.subCard}>
            <h3>The model decides, a person signs off</h3>
            <p>
              On 3.99 m near misses Spark said “due” three times and once “not required: a marginal 4-m
              separation breach rather than an accident or imminent-collision near miss”. On the 3.28 m one it
              said due. The judgement varies at the margin, which is why a person marks the training done.
            </p>
          </div>
        </div>
      )}

      {tab === "dispute" && (
        <div className={styles.subCard}>
          <h3>A worker disputes an alert, and finds a bug</h3>
          <p>
            On the 3.996 m alert (4 mm inside the limit) a worker disputed from the QR page. Muse Spark 1.3
            re-reviewed four frames and upheld it in 28.3 s:
          </p>
          <blockquote className={styles.quote}>
            “the annotation legend in the frames reports the yellow ruler distance as 5.1 m then 4.9 m … all &gt; 4 m
            … The IR measured value of 3.996 m is borderline by 4 mm”
          </blockquote>
          <p>
            The strip&apos;s ruler measured to the middle of the forklift&apos;s bottom edge while the rule measures to
            the nearest point of its footprint, about 1 m apart on a wide forklift. It now ends at the footprint
            point (runtime/strips.py, e7a483d). On run 7 the ruler reads 3.9–4.2 m for a 3.989 m pass. The upheld
            dispute and its regression stay on the chain as they happened.
          </p>
        </div>
      )}

      {tab === "shift" && (
        <div className={styles.grid}>
          <div className={styles.subCard}>
            <h3>A spoken report from the floor</h3>
            <p>
              A worker reported a near miss in Spanish, “about three metres” (21.3 s). Spark matched it to the
              3.28 m camera event and proposed a rule, which planned with Rule CI green (163 s). With only the
              0.0 m driver-clip candidate in the window it refused to match, which is the careful behaviour.
            </p>
          </div>
          <div className={styles.subCard}>
            <h3>The shift report (7.2–8.3 s)</h3>
            <p>
              9 near misses, 9 alerts, 4 to a person, 1 acknowledged, 1 dispute upheld; the closest was 0.0 m,
              the driver clip. Counted from the chain; Spark only wrote the words.
            </p>
            <p className={styles.muted}>
              Honest limits: for the first hour the RTX 4090 pool had no GPU, so candidates went to a person
              instead of passing silently, and precision and recall were not measured.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
