import Link from "next/link";

import { icons } from "@/components/icons";
import { ModelChip, StepFlow, VerdictPill, formatMs, type FlowStep } from "@/components/kit";
import { GOLDEN_RUNS } from "@/fixtures/golden";
import {
  CAMPAIGN,
  campaignTest,
  distanceLimit,
  firstEvent,
  repoFile,
  REPO_URL,
  SERVERLESS_LOAD,
} from "@/fixtures/live-campaign";

import { UseCaseStill } from "@/components/usecases/UseCaseStill";
import { WORLDS } from "@/components/usecases/worlds";

import { modelLabel, ROLES } from "@/lib/models";

import { ModelMap, type FixedModel } from "./ModelMap";
import { SystemStatus } from "./SystemStatus";
import styles from "./landing.module.css";

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
    text: "A second model checks every alert on the video frames. Every decision goes into a hash-chained log you can check yourself.",
  },
] as const;

/** Which models carry each step; Trust names its roles, and the model map below names their models. */
const STEP_MODELS: Record<(typeof STEPS)[number]["name"], FlowStep["models"]> = {
  Describe: ["Muse Voice Transcribe", "Muse Spark 1.3"],
  Plan: ["Muse Spark 1.3", "SAM 3.1"],
  Prove: [{ model: "Rule CI", meta: false }, { model: "Muse Image", state: "off" }],
  Trust: [ROLES.tier1, ROLES.tier2],
};

/**
 * The Meta models whose job does not depend on the deployment. The Tier 1 check, the Tier 2 review
 * and SAM 3.1 are named from `GET /health` by `ModelMap`, never from a copy here.
 */
export const MODELS: readonly FixedModel[] = [
  {
    model: "Muse Spark 1.3",
    job: "Plans the job, compiles the rule, answers the owner's questions (Ask)",
    status: "Live",
  },
  { model: "Muse Voice Transcribe", job: "Spoken requests", status: "Live" },
  {
    model: "Llama Prompt Guard 2",
    job: "Screens text seen in the scene before it reaches the checker",
    status: "Wired; degraded in this deployment",
  },
  { model: "Muse Image", job: "Test scenes for Rule CI", status: "Built; switched off in the app" },
  {
    model: "Muse Code",
    job: "Writes a new check when the rule language cannot say it; runs only after the owner approves",
    status: "Built; owner-only (Extensions)",
  },
];

export { WORLDS } from "@/components/usecases/worlds";

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

/**
 * The signature: one alert from the 1 Nov live run, proved end to end. Every value is read from
 * the committed result file; nothing is typed by hand.
 */
function AlertProof() {
  const test = campaignTest("T02");
  const event = firstEvent(test);
  const rule = test.rules[0];
  const limit = rule ? distanceLimit(rule.predicate) : null;
  const value = event.measurement?.value ?? null;
  const at = event.ts_start.slice(11, 19);
  const checker = modelLabel(event.verdict.model_version); // the model that ran on 1 Nov
  return (
    <figure className={styles.proof} aria-labelledby="proof-caption" data-testid="alert-proof">
      <div className={styles.proofHead}>
        <span className={styles.proofKind}>{event.decision.alert ? "Verified alert" : "Rejected"}</span>
        <span className={styles.proofWhen}>
          <span className="visually-hidden">Test </span>
          {test.id}
          <span aria-hidden="true"> </span>
          <time dateTime={event.ts_start}>{`${test.started_at.slice(0, 10)} ${at} UTC`}</time>
        </span>
      </div>

      <blockquote className={styles.proofRequest}>
        <p>“{test.request}”</p>
      </blockquote>

      <div className={styles.reading}>
        <p className={styles.readingValue}>
          {value !== null ? value.toFixed(2) : "—"}
          <span className={styles.readingUnit}>{event.measurement?.unit ?? "m"}</span>
        </p>
        <p className={styles.readingNote}>
          {limit !== null ? (
            <>
              Measured on the calibrated floor. The rule says under <b className="measured">{limit} m</b>.
            </>
          ) : (
            "Measured on the calibrated floor."
          )}
        </p>
      </div>

      <div className={styles.proofVerdict}>
        <VerdictPill verdict={event.verdict.verdict} confidence={event.verdict.score} model={checker} />
        <p className={styles.proofReason}>{event.verdict.reason}</p>
      </div>

      <ul className={styles.proofChain} aria-label="Models in this alert">
        <li>
          <ModelChip model="Muse Spark 1.3" job="planned the rule" ms={test.plan_s * 1000} />
        </li>
        <li>
          <ModelChip model="SAM 3.1" job="measured the gap" />
        </li>
        <li>
          <ModelChip model={checker} job="checked the frames" ms={event.verdict.latency_ms} />
        </li>
      </ul>

      <figcaption id="proof-caption" className={styles.proofCaption}>
        <span>
          Event to checked decision <b className="measured">{formatMs(event.event_to_decision_s * 1000)}</b>.
          Recorded on the deployed stack, checked by {checker} on our own GPUs; not live now.
        </span>
        <a href={repoFile(CAMPAIGN.source)} className={styles.proofSource}>
          The run&apos;s record
        </a>
      </figcaption>
    </figure>
  );
}

/** The judge landing page: what Rulecam is, real numbers, and the no-login demo. */
export function Landing() {
  const golden = GOLDEN_RUNS[0];
  return (
    <div className={styles.page} data-testid="landing">
      <section aria-labelledby="hero-title" className={styles.hero}>
        <div className={`container ${styles.heroGrid}`}>
          <div className={styles.heroText}>
            <h1 id="hero-title" className={styles.display}>
              Plain words in.
              <br />
              Verified alerts out.
            </h1>
            <p className={styles.pitch}>{PITCH}</p>
            <div className={styles.actions}>
              <Link href="/demo?demo=1" className="btn primary lg" data-testid="cta-demo">
                Open the demo
                {icons.arrowRight}
              </Link>
              {golden && (
                <a href={golden.verify_url} className="btn lg" data-testid="cta-verify">
                  Check the evidence yourself
                </a>
              )}
            </div>
            <p className={styles.note}>
              The demo replays a recorded run with no model calls (labelled REPLAY), so it works with
              every GPU switched off. The verify page recomputes every SHA-256 in your browser.
            </p>
          </div>
          <AlertProof />
        </div>
      </section>

      <SystemStatus />

      <section id="how" aria-labelledby="how-title" className={styles.section}>
        <div className="container">
          <div className={styles.head}>
            <h2 id="how-title" className={styles.h2}>
              From a worry to a proven rule
            </h2>
            <p className={styles.headLine}>Nothing runs until a person approves a plan that passed every check.</p>
          </div>
          <StepFlow
            label="How a rule goes live"
            steps={STEPS.map((s) => ({ name: s.name, text: s.text, models: STEP_MODELS[s.name] }))}
          />
        </div>
      </section>

      <section id="worlds" aria-labelledby="worlds-title" className={`${styles.section} ${styles.band}`}>
        <div className={`container ${styles.split}`}>
          <div className={styles.splitHead}>
            <h2 id="worlds-title" className={styles.h2}>
              One engine, three worlds
            </h2>
            <p className={styles.headLine}>
              The same planner, Rule CI and verifier, pointed at three kinds of site.
            </p>
          </div>
          <ul className={styles.worlds}>
            {WORLDS.map((w) => (
              <li key={w.slug} className={styles.world}>
                <UseCaseStill world={w} className={styles.worldStill} />
                <Link href={`/usecases/${w.slug}`} className={styles.worldLink} data-testid={`world-${w.slug}`}>
                  <span className={styles.worldName}>
                    <span className={styles.worldIcon}>{icons[w.icon]}</span>
                    {w.name}
                  </span>
                  <span className={styles.worldPromise}>{w.promise}</span>
                  <span className={styles.worldLine}>{w.line}</span>
                  <span className={styles.worldModels}>
                    {w.models.map((m) => (
                      <ModelChip key={m} model={m} />
                    ))}
                  </span>
                  <span className={styles.worldGo} aria-hidden="true">
                    {icons.arrowRight}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section id="models" aria-labelledby="models-title" className={styles.section}>
        <div className="container">
          <div className={styles.head}>
            <h2 id="models-title" className={styles.h2}>
              Which Meta model does what
            </h2>
            <p className={styles.headLine}>What runs in the deployed app today, said plainly.</p>
          </div>
          <ModelMap fixed={MODELS} />
        </div>
      </section>

      <section id="measured" aria-labelledby="measured-title" className={`${styles.section} ${styles.sectionLast}`}>
        <div className="container">
          <div className={styles.head}>
            <h2 id="measured-title" className={styles.h2}>
              Measured, not promised
            </h2>
            <p className={styles.headLine}>
              Each figure is read from a committed result file. Weak results are published too:{" "}
              <a href={`${REPO_URL}#results`}>the README results table</a>.
            </p>
          </div>
          <ul className={styles.figures}>
            {headlines().map((h) => (
              <li key={h.where + h.value} className={styles.figure} data-testid="headline">
                <p className={styles.figureValue}>{h.value}</p>
                <p className={styles.figureText}>{h.text}</p>
                <p className={styles.figureSource}>
                  <span>{h.where}</span>
                  <a href={repoFile(h.source)}>{h.source}</a>
                </p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <footer className={styles.footer}>
        <div className={`container ${styles.footerGrid}`}>
          <p className={styles.footerMark}>Rulecam</p>
          <nav aria-label="Rulecam links" className={styles.footerLinks}>
            <Link href="/demo?demo=1">Golden replay</Link>
            {golden && <a href={golden.verify_url}>Verify the evidence</a>}
            <Link href="/monitor">The console</Link>
            <a href={REPO_URL}>Code on GitHub</a>
          </nav>
          <p className={styles.footerNote}>
            Built for the Meta Global AI Developer Hackathon, 26 Oct to 4 Nov 2026. No faces are
            recognised; the crew can see every rule and dispute any alert.
          </p>
        </div>
      </footer>
    </div>
  );
}
