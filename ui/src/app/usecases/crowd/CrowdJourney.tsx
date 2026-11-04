"use client";

import { LiveBadge, StepFlow, type FlowStep } from "@/components/kit";

import type { CrowdAlert, EventReport } from "./crowdApi";
import styles from "./crowd.module.css";
import recorded from "./recorded-run.json";

interface Recorded {
  label: string;
  report: EventReport & {
    truth?: { zone: string; count_ratio: number; count_mae: number; true_peak_ppm2: number }[];
    latency?: { assess_s: number; alert_s: number; checked: string }[];
    evidence?: { chain_ok: boolean; records: unknown[] };
  };
  alert: CrowdAlert & {
    check?: { agrees?: boolean };
    audio?: { lang: string; text: string; heard?: string }[];
    chain_hash?: string;
  };
  ack: { notified: { role: string; status: string }[] };
  assessment: { staff_actions?: string[] };
}

const run = recorded as unknown as Recorded;

const fmt = (n: number | null | undefined, digits = 1): string =>
  typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "unknown";

const cap = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

/** The seven steps of the recorded run, each said with the value the system recorded. */
export function crowdJourney(r: Recorded = run): FlowStep[] {
  const rep = r.report;
  const gate = rep.zones.find((z) => z.zone.startsWith("gate")) ?? rep.zones[0];
  const truth = rep.truth?.[0];
  const latency = rep.latency?.[0];
  const acks = r.ack.notified.filter((n) => n.status === "acknowledged").map((n) => n.role.replace(/_/g, " "));
  const langs = (r.alert.audio ?? []).map((a) => (a.lang === "hi" ? "Hindi" : a.lang === "en" ? "English" : a.lang));
  const heard = (r.alert.audio ?? []).some((a) => a.heard);
  const records = rep.evidence?.records.length ?? null;
  return [
    {
      name: "Describe",
      text: "Asked in Hindi: warn us before the crowd at the main gate turns dangerous; if it fills, turn the corridor toward side door B.",
      models: ["Muse Spark 1.3"],
    },
    {
      name: "Plan",
      text: `Zones on the overhead view: ${rep.zones
        .filter((z) => z.zone !== "view")
        .map((z) => z.zone)
        .join(", ")}; side door B as the other route.`,
      models: ["Muse Spark 1.3"],
    },
    {
      name: "Prove",
      text: truth
        ? `Counts checked against the experiment's tracked heads: ratio ${fmt(truth.count_ratio, 3)}, off by ${fmt(truth.count_mae)} person on average.`
        : "No ground truth for this run.",
      models: [{ model: "SAM 3.1", job: "counted heads" }],
    },
    {
      name: "Watch",
      text: `Peak ${fmt(gate?.peak_density_ppm2)} people/m² at the ${gate?.zone ?? "gate"}, ${gate?.peak_people ?? "unknown"} people.`,
      models: [{ model: "SAM 3.1", job: "density" }],
    },
    {
      name: "Verify",
      text: `Glimmer looked at the frames and ${r.alert.check?.agrees ? "agreed it was critical" : "did not agree"}${latency ? `; the alert went out ${fmt(latency.alert_s)} s after the reading.` : "."}`,
      models: [
        { model: "Muse Spark 1.3", job: "judged", ms: latency ? latency.assess_s * 1000 : null },
        "Muse Glimmer 30B",
      ],
    },
    {
      name: "Act",
      text: `${cap(r.assessment.staff_actions?.[0] ?? "stewards told what to do")}; announced in ${langs.join(" and ")}${heard ? " and heard back" : ""}; ${acks.join(" and ")} acknowledged.`,
      models: heard ? ["Muse Voice Transcribe"] : [],
    },
    {
      name: "Evidence",
      text: `${rep.evidence?.chain_ok ? "Chain intact" : "Chain not verified"}${records !== null ? `, ${records} records` : ""}; alert hash ${r.alert.chain_hash ? `${r.alert.chain_hash.slice(0, 10)}…` : "unknown"}.`,
    },
  ];
}

/** Describe → Evidence for the recorded run, labelled as recorded; every value is the run's own. */
export function CrowdJourney() {
  return (
    <section className={styles.journey} aria-labelledby="crowd-journey-title">
      <div className={styles.journeyHead}>
        <h2 id="crowd-journey-title" className={styles.sectionTitle}>
          One crush warning, step by step
        </h2>
        <LiveBadge mode="replay" detail="recorded 4 Nov 2026" />
      </div>
      <StepFlow steps={crowdJourney()} columns={4} size="compact" as="h3" label="The crowd warning, step by step" current={7} />
    </section>
  );
}
