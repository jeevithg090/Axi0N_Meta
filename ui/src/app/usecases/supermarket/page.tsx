"use client";

// The supermarket story on one page, live: ask → advice → accept (pre-register + watch) →
// staff alerted with the marked photo → task fixed with proof → the owner's brief.

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { ApiError, errorText } from "@/lib/api";
import type { AskAnswer } from "@/lib/ask";

import { UseCaseJourney } from "@/components/usecases/UseCaseJourney";

import { AskPanel } from "./AskPanel";
import { AdvicePanel, BriefPanel, StoreRowPanel, TasksPanel, WatchPanel } from "./StoryPanels";
import {
  type Brief,
  type Camera,
  type Overview,
  receiptHolds,
  retail,
  type RetailCard,
} from "./api";
import {
  RECORDED_ANSWER,
  RECORDED_BRIEF,
  RECORDED_CARD,
  RECORDED_OVERVIEW,
  SupermarketRecordedRun,
} from "./SupermarketRecordedRun";
import { SupermarketProof } from "./SupermarketProof";
import styles from "./supermarket.module.css";

const POLL_MS = 5000;

/** Why the page has no live data, in words a visitor understands. */
function overviewProblem(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401 || e.status === 403) return OWNER_ONLY;
    if (e.status === 404)
      return "This Rulecam API has no supermarket routes yet (it runs an older version).";
    if (e.status === 503) return `The supermarket tools are off on this API: ${e.message}`;
    return `The Rulecam API answered ${e.status}: ${e.message}`;
  }
  return errorText(e, "The Rulecam API is unreachable, so nothing live can be shown.");
}

const OWNER_ONLY =
  "Owner login required: this page shows the store's cameras and staff alerts, so it is for the site owner.";
const MAX_CAMERAS = 6;

/** Cameras with the newest video first, at most `MAX_CAMERAS` (old pairings drop off). */
function newestCard(cards: RetailCard[]): RetailCard | null {
  return [...cards].sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;
}

function newestFirst(cameras: Camera[]): Camera[] {
  return [...cameras].sort((a, b) => b.last.localeCompare(a.last)).slice(0, MAX_CAMERAS);
}

function Step({
  n,
  title,
  done,
  children,
  note,
}: {
  n: number;
  title: string;
  done: boolean;
  children: ReactNode;
  note: string;
}) {
  return (
    <section className={styles.step} aria-labelledby={`step-${n}`}>
      <div className={styles.stepHead}>
        <span className={`${styles.num} ${done ? styles.done : ""}`}>{done ? "✓" : n}</span>
        <h2 id={`step-${n}`} className={styles.title}>
          {title}
        </h2>
        <span className={styles.muted}>{note}</span>
      </div>
      {children}
    </section>
  );
}

function ReceiptCheck({ sha }: { sha: string }) {
  const [text, setText] = useState("Checking the receipt in this browser…");
  useEffect(() => {
    let live = true;
    retail
      .receipt(sha, null)
      .then(async (r) => {
        const ok = await receiptHolds(r);
        if (live)
          setText(
            ok
              ? `✓ Receipt ${sha.slice(0, 12)}… holds: “${r.question}” → “${r.answer.slice(0, 200)}” (${r.models.join(", ")}, ${r.ts}). Run log: ${r.rows_verified ?? "?"} rows intact.`
              : `✗ Receipt ${sha.slice(0, 12)}… does not match its logged text.`,
          );
      })
      .catch((e: unknown) => live && setText(errorText(e, "No such receipt.")));
    return () => {
      live = false;
    };
  }, [sha]);
  return <p className={styles.card}>{text}</p>;
}

export default function SupermarketPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [card, setCard] = useState<RetailCard | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [checkReceipt, setCheckReceipt] = useState<string | null>(null);
  const [useRecorded, setUseRecorded] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const next = await retail.overview();
      setOverview(next);
      setError("");
      if (next.cameras.length > 0 || next.findings.length > 0 || next.tasks.length > 0) {
        setUseRecorded(false);
      }
    } catch (e) {
      setError(overviewProblem(e));
    }
  }, []);

  useEffect(() => {
    const sha = new URLSearchParams(window.location.search).get("receipt");
    if (sha && /^[0-9a-f]{64}$/.test(sha)) setCheckReceipt(sha);
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const activeOverview = useRecorded ? RECORDED_OVERVIEW : overview;
  const activeAnswer = useRecorded ? (answer ?? RECORDED_ANSWER) : answer;
  const cameras = newestFirst(activeOverview?.cameras ?? []);
  const latestCard =
    card ?? (useRecorded ? RECORDED_CARD : null) ?? newestCard(activeOverview?.cards ?? []) ?? null;
  const shownCard =
    latestCard && activeOverview
      ? (activeOverview.cards.find((c) => c.recommendation.id === latestCard.recommendation.id) ??
        latestCard)
      : latestCard;
  const receipt = activeAnswer?.receipt || activeOverview?.asks[0]?.receipt || null;
  const alerted = activeOverview?.findings.filter((f) => f.alert_id) ?? [];
  const tasks = activeOverview?.tasks ?? [];
  const activeBrief = brief ?? activeOverview?.brief ?? (useRecorded ? RECORDED_BRIEF : null);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <p className={styles.lead}>
          Your cameras already see the queue, the empty shelf and the spill. Ask anything in your
          own language; Muse Spark answers from what SAM 3.1 tracked and the Tier 1 model saw, turns the
          answer into a change you can test, watches for it, tells the right person with the photo,
          checks the fix twice and keeps a receipt. Everything below runs on this store’s cameras
          or replays the 4 Nov live store run.
        </p>
        {error && (
          <p className={styles.card} role="status">
            {error} {error === OWNER_ONLY && <a href="/login">Log in as the owner</a>}
            {useRecorded && " The recorded 4 Nov run is shown below instead."}
          </p>
        )}
        {!overview && !error && <p className={styles.muted}>Connecting to the store’s cameras…</p>}
        {checkReceipt && <ReceiptCheck sha={checkReceipt} />}
      </header>
      <SupermarketProof />
      <UseCaseJourney
        id="sm-journey"
        title="From a question to a fixed problem"
        label="The supermarket story, step by step"
        columns={5}
        steps={[
          { name: "Ask", text: "Typed or spoken, any language; the answer is read aloud.", state: activeAnswer !== null ? "done" : "todo" },
          { name: "Advice", text: "A card with a pre-registered check; its numbers must be in the answer's evidence.", state: shownCard !== null ? "done" : "todo" },
          { name: "Watch", text: "Queue, hazard and shelf checks tell the right person, with the photo.", state: alerted.length > 0 ? "done" : "todo" },
          { name: "Fixed", text: "A staff photo checked by Spark and the Tier 1 model closes the task; the time goes on the chain.", state: tasks.some((t) => t.status === "done") ? "done" : "todo" },
          { name: "Brief", text: "Muse Spark writes the owner's brief from the stored checks and tasks.", state: activeBrief != null ? "done" : "todo" },
        ]}
      />
      <div id="sm-workspace" className={styles.steps}>
        <SupermarketRecordedRun
          loaded={useRecorded}
          onLoadIntoSteps={() => setUseRecorded((v) => !v)}
        />
        <div className={styles.workspaceBar}>
          <div>
            <strong>
              {useRecorded
                ? "Steps 1–5 below are showing the recorded 4 Nov 2026 live store run (REPLAY)"
                : "Steps 1–5 below are connected to the live local store API"}
            </strong>
            <div className={styles.muted}>
              {useRecorded
                ? "4 cameras, 1 pre-registered till switchback (#16), 4 findings (queue, spill #19, pasta shelf #18, fire exit #25), 2 photo-verified tasks (#20, #21), and the owner's brief."
                : `${overview?.cameras.length ?? 0} live cameras, ${overview?.findings.length ?? 0} findings, ${overview?.tasks.length ?? 0} tasks on this server today.`}
            </div>
          </div>
          <div className={styles.row}>
            <button
              type="button"
              className={useRecorded ? styles.button : styles.ghost}
              onClick={() => setUseRecorded(true)}
            >
              4 Nov Recorded Run (REPLAY)
            </button>
            <button
              type="button"
              className={!useRecorded ? styles.button : styles.ghost}
              onClick={() => setUseRecorded(false)}
            >
              Live Store API ({overview?.cameras.length ?? 0} cameras)
            </button>
          </div>
        </div>
        <Step
          n={1}
          title="Ask"
          done={activeAnswer !== null}
          note="typed or spoken, any language; answer read aloud"
        >
          <AskPanel
            onAnswer={(a) => {
              setAnswer(a);
              void refresh();
            }}
          />
        </Step>
        <Step
          n={2}
          title="Advice with a pre-registered check"
          done={shownCard !== null}
          note="Site Advisor card from the answer; numbers must appear in the answer's evidence"
        >
          <AdvicePanel
            receipt={receipt}
            cameras={cameras}
            card={shownCard}
            onCard={(c) => {
              setCard(c);
              void refresh();
            }}
          />
        </Step>
        <Step
          n={3}
          title="The watch tells staff, with the photo"
          done={alerted.length > 0}
          note="queue / hazard / shelf checks; alerts go to the staff alerts app (and Telegram or WhatsApp when set up)"
        >
          <WatchPanel
            cameras={cameras}
            watches={activeOverview?.watches ?? []}
            findings={activeOverview?.findings ?? []}
            onChange={() => void refresh()}
          />
        </Step>
        <Step
          n={4}
          title="Fixed, and proven fixed"
          done={tasks.some((t) => t.status === "done")}
          note="a staff photo checked by Spark and the Tier 1 model, or a clean camera re-check; time to fix goes on the evidence chain"
        >
          <TasksPanel tasks={tasks} onChange={() => void refresh()} />
        </Step>
        <Step
          n={5}
          title="Owner’s brief"
          done={activeBrief != null}
          note="written by Muse Spark from the stored checks, tasks, questions and the running test"
        >
          <BriefPanel
            brief={activeBrief}
            onBrief={(b) => {
              setBrief(b);
              void refresh();
            }}
          />
          <StoreRowPanel />
        </Step>
      </div>
    </div>
  );
}
