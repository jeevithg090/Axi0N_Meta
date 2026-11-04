"use client";

// The supermarket story on one page, live: ask → advice → accept (pre-register + watch) →
// staff alerted with the marked photo → task fixed with proof → the owner's brief.

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { errorText } from "@/lib/api";
import type { AskAnswer } from "@/lib/ask";

import { AskPanel } from "./AskPanel";
import { AdvicePanel, BriefPanel, TasksPanel, WatchPanel } from "./StoryPanels";
import { type Brief, type Overview, receiptHolds, retail, type RetailCard } from "./api";
import styles from "./supermarket.module.css";

const POLL_MS = 5000;

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

  const refresh = useCallback(async () => {
    try {
      setOverview(await retail.overview());
      setError("");
    } catch (e) {
      setError(errorText(e, "The Rulecam API did not answer."));
    }
  }, []);

  useEffect(() => {
    const sha = new URLSearchParams(window.location.search).get("receipt");
    if (sha && /^[0-9a-f]{64}$/.test(sha)) setCheckReceipt(sha);
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const cameras = overview?.cameras ?? [];
  const latestCard =
    card ??
    overview?.cards.find((c) => c.preregistered_seq !== null) ??
    overview?.cards.at(-1) ??
    null;
  const shownCard =
    latestCard && overview
      ? (overview.cards.find((c) => c.recommendation.id === latestCard.recommendation.id) ??
        latestCard)
      : latestCard;
  const receipt = answer?.receipt || overview?.asks[0]?.receipt || null;
  const alerted = overview?.findings.filter((f) => f.alert_id) ?? [];
  const tasks = overview?.tasks ?? [];

  return (
    <main className={styles.page}>
      <header>
        <h1>Supermarket</h1>
        <p className={styles.lead}>
          Your cameras already see the queue, the empty shelf and the spill. Ask anything in your
          own language; Muse Spark answers from what SAM 3.1 tracked and Muse Glimmer saw, turns the
          answer into a change you can test, watches for it, tells the right person with the photo,
          checks the fix twice and keeps a receipt. Everything below is live on this store’s
          cameras.
        </p>
        {error && <p className={styles.error}>{error}</p>}
        {checkReceipt && <ReceiptCheck sha={checkReceipt} />}
      </header>
      <div className={styles.steps}>
        <Step
          n={1}
          title="Ask"
          done={answer !== null}
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
            watches={overview?.watches ?? []}
            findings={overview?.findings ?? []}
            onChange={() => void refresh()}
          />
        </Step>
        <Step
          n={4}
          title="Fixed, and proven fixed"
          done={tasks.some((t) => t.status === "done")}
          note="a staff photo checked by Spark and Glimmer, or a clean camera re-check; time to fix goes on the evidence chain"
        >
          <TasksPanel tasks={tasks} onChange={() => void refresh()} />
        </Step>
        <Step
          n={5}
          title="Owner’s brief"
          done={(brief ?? overview?.brief) != null}
          note="written by Muse Spark from the stored checks, tasks, questions and the running test"
        >
          <BriefPanel
            brief={brief ?? overview?.brief ?? null}
            onBrief={(b) => {
              setBrief(b);
              void refresh();
            }}
          />
        </Step>
      </div>
    </main>
  );
}
