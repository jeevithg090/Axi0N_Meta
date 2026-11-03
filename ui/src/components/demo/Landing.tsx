import Link from "next/link";

import { GOLDEN_RUNS } from "@/fixtures/golden";
import {
  CAMPAIGN,
  campaignTest,
  firstEvent,
  repoFile,
  REPO_URL,
  SERVERLESS_LOAD,
} from "@/fixtures/live-campaign";

import styles from "./demo.module.css";

export const PITCH =
  "Tell it what worries you. It works out what to watch, proves the rule, and has a second " +
  "model check every alert.";

/** The four steps, in the README's words ("What it does"). */
export const STEPS = [
  {
    name: "Describe",
    text: "Say or type what worries you, in your own words and language. A camera's limits are said up front.",
  },
  {
    name: "Plan",
    text: "Muse Spark plans the job. SAM 3.1 looks at the camera, measures distances and checks it can see what the rule needs.",
  },
  {
    name: "Prove",
    text: "Rule CI attacks the rule with edge cases and broken copies before anyone can approve it.",
  },
  {
    name: "Trust",
    text: "Glimmer checks every alert on the video frames. Every decision goes into a hash-chained log you can check yourself.",
  },
] as const;

/** Which Meta model does what, with the status the README's model map gives it today. */
export const MODELS = [
  {
    model: "Muse Spark 1.3",
    job: "Plans the job, compiles the rule, answers the owner's questions (Ask)",
    status: "Live",
  },
  {
    model: "SAM 3.1",
    job: "Finds and tracks people and objects; probes what each camera can see",
    status: "Live (Meta API + our serverless GPU)",
  },
  {
    model: "Muse Glimmer 30B (open weights)",
    job: "Checks every alert on the video frames before anyone is bothered",
    status: "Live (our serverless GPU)",
  },
  { model: "Muse Voice Transcribe", job: "Spoken requests", status: "Live" },
  {
    model: "Llama Prompt Guard 2",
    job: "Screens text seen in the scene before it reaches the checker",
    status: "Wired; degraded in this deployment",
  },
  {
    model: "Muse Spark Open (open weights)",
    job: "Second opinion and plan audit",
    status: "Wired; skipped here (no Baseten key)",
  },
  { model: "Muse Image", job: "Test scenes for Rule CI", status: "Built; switched off in the app" },
] as const;

interface Headline {
  value: string;
  text: string;
  source: string;
  where: string;
}

/** Four real numbers, each read from a committed result file (see the fixtures' tests). */
export function headlines(): Headline[] {
  const t02 = firstEvent(campaignTest("T02"));
  const t04 = firstEvent(campaignTest("T04"));
  const measured = t02.measurement?.value ?? Number.NaN;
  return [
    {
      value: `${measured.toFixed(2)} m`,
      text: `A worker passed a moving forklift; the rule said 4 m. Glimmer checked the frames: ${t02.verdict.verdict}, ${t02.verdict.score.toFixed(2)}.`,
      source: CAMPAIGN.source,
      where: "live run, 1 Nov, T02",
    },
    {
      value: t04.verdict.score.toFixed(2),
      text: "The rule engine flagged the seated driver. Glimmer saw it was the driver, so no alert went out.",
      source: CAMPAIGN.source,
      where: "live run, 1 Nov, T04",
    },
    {
      value: `${t02.event_to_decision_s.toFixed(1)} s`,
      text: "From the event to a checked decision, on the deployed stack.",
      source: CAMPAIGN.source,
      where: "live run, 1 Nov, T02",
    },
    {
      value: `$${SERVERLESS_LOAD.usd_per_alert.toFixed(4)}`,
      text: `GPU cost per alert check, ${SERVERLESS_LOAD.burst} checks at once on ${SERVERLESS_LOAD.state} serverless GPUs.`,
      source: SERVERLESS_LOAD.source,
      where: "load test, 2 Nov",
    },
  ];
}

/** The judge landing page: what Rulecam is, real numbers, and the no-login demo. */
export function Landing() {
  const golden = GOLDEN_RUNS[0];
  return (
    <div className={styles.page} data-testid="landing">
      <section aria-label="Rulecam" className="stack">
        <p className={styles.kicker}>
          Meta Global AI Developer Hackathon · built 26 Oct – 4 Nov 2026
        </p>
        <h1 className={styles.title}>Rulecam</h1>
        <p className={styles.pitch}>{PITCH}</p>
        <div className={styles.buttons}>
          <Link href="/demo?demo=1" className="btn alert" data-testid="cta-demo">
            Open the demo · no login
          </Link>
          {golden && (
            <a href={golden.verify_url} className="btn o" data-testid="cta-verify">
              Check the evidence yourself
            </a>
          )}
        </div>
        <p className={styles.note}>
          The demo replays a recorded run with no model calls (labelled REPLAY), so it works with
          every GPU switched off. The verify page recomputes every SHA-256 in your browser.
        </p>
      </section>

      <section aria-label="How it works">
        <h2 className={styles.h2}>How it works</h2>
        <div className={styles.cards}>
          {STEPS.map((s, i) => (
            <div key={s.name} className={styles.card}>
              <p className={styles.step}>
                {i + 1} · {s.name}
              </p>
              <p>{s.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Measured">
        <h2 className={styles.h2}>Measured, not promised</h2>
        <div className={styles.cards}>
          {headlines().map((h) => (
            <div key={h.where + h.value} className={styles.card} data-testid="headline">
              <p className={styles.big}>{h.value}</p>
              <p>{h.text}</p>
              <p className={styles.src}>
                {h.where} · <a href={repoFile(h.source)}>{h.source}</a>
              </p>
            </div>
          ))}
        </div>
        <p className={styles.note}>
          Weak results are published too, next to these:{" "}
          <a href={`${REPO_URL}#results`}>the README results table</a>.
        </p>
      </section>

      <section aria-label="Meta models">
        <h2 className={styles.h2}>Which Meta model does what</h2>
        <div className="scroll-x">
          <table className={styles.models}>
            <thead>
              <tr>
                <th>Model</th>
                <th>Job</th>
                <th>Today</th>
              </tr>
            </thead>
            <tbody>
              {MODELS.map((m) => (
                <tr key={m.model}>
                  <td>
                    <strong>{m.model}</strong>
                  </td>
                  <td>{m.job}</td>
                  <td className={styles.status}>{m.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-label="Links" className={styles.buttons}>
        <Link href="/demo?demo=1" className="btn">
          Open the demo
        </Link>
        <a href={REPO_URL} className="btn o">
          Code on GitHub
        </a>
      </section>
    </div>
  );
}
