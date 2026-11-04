"use client";

// The 4 Nov 2026 end-to-end live supermarket run (from reports/eval/2026-11-04-supermarket-live.json
// and reports/eval/2026-11-04-supermarket-live.md), presented as an interactive recorded walkthrough
// and loadable directly into Steps 1–5 when no live store camera is streaming.

import { useState } from "react";

import { LiveBadge } from "@/components/kit";
import type { AskAnswer } from "@/lib/ask";

import { speak } from "./AskPanel";
import type { Brief, Overview, RetailCard } from "./api";
import styles from "./supermarket.module.css";

export const RECORDED_RECEIPT =
  "21c820c291dc2d22101574d58169e74b99a54ced89772aef6513e92927cb67ad";

export const RECORDED_ANSWER: AskAnswer = {
  question:
    "When was the checkout queue longest today, how many tills were open, and do we need another till?",
  answer:
    "Peak checkout queue on the checkout-queue camera reached 22 tracked customer boxes in view at 01:13:07 UTC (reconciled by cross_count to 10–11 customers waiting: SAM 3.1 found 15–17 boxes, Muse Spark pointed to 9, Muse Glimmer counted 10–11). 2 of 2 staffed tills were open, with ~6 arrivals/min and ~60–90 s service time per customer (~300 s wait). Yes — open a 3rd till immediately; the standing queue check already alerted the duty manager.",
  conversation_id: "conv-retail-20261104-e2e",
  seconds: 60.4,
  models: ["muse-spark-1.3", "muse-glimmer-30b", "muse-spark-1.2", "sam-3.1"],
  receipt: RECORDED_RECEIPT,
  turns: [
    { turn: 0, model: "muse-spark-1.3", seconds: 19.2, actions: 3 },
  ],
  steps: [
    {
      turn: 0,
      action: {
        action: "sql",
        thought: "Query peak customer track counts and recent retail_findings on checkout cameras.",
      },
      result: { rows: 4, peak: 22 },
      frames: [],
      model: "sqlite",
      seconds: 0.4,
    },
    {
      turn: 0,
      action: {
        action: "look",
        thought: "Inspect the peak checkout frames with Muse Glimmer 30B to count open tills and queue.",
      },
      result: { tills_open: 2, waiting: "8-11" },
      frames: [],
      model: "muse-glimmer-30b",
      seconds: 24.0,
    },
    {
      turn: 0,
      action: {
        action: "watch",
        thought: "Watch the 35 s checkout clip with Muse Spark 1.2 to estimate arrival rate and service time.",
      },
      result: { arrivals_per_min: 6, service_s: "60-90", wait_s: 300 },
      frames: [],
      model: "muse-spark-1.2",
      seconds: 15.0,
    },
  ],
};

export const RECORDED_CARD: RetailCard = {
  recommendation: {
    id: "adv-retail-694d4d5fc2c4",
    status: "measuring",
    do: [{ op: "add_capacity", params: { lanes: "1" } }],
    who_role: "duty-manager",
    revert: "Close the 3rd till once waiting customers stay below 4 for two 30-min blocks.",
    expect: { interval: [120, 240], unit: "s", model_rung: "allen_cunneen" },
  },
  title: "Open a third till to cut the checkout queue",
  saw: [
    "Checkout queue peaked at 8–11 waiting customers (22 raw track boxes before cross_count reconciliation).",
    "2 of 2 staffed tills were open with ~6 arrivals/min and ~60–90 s service time (~300 s estimated wait).",
  ],
  change: "Open a 3rd checkout till and announce the new lane in English and Spanish.",
  gain: "M/M/c queueing what-if: 6 arrivals/min × 60 s = 6 erlangs of work; 2 tills saturate, so opening a 3rd till is required to drain the ~300 s backlog.",
  numbers: [
    {
      id: "tills_open",
      label: "Tills open",
      value: 2,
      unit: "tills",
      definition: "Staffed tills active during peak queue check",
      grounded: true,
    },
    {
      id: "arrivals_per_min",
      label: "Arrival rate",
      value: 6,
      unit: "customers/min",
      definition: "Measured by Muse Spark 1.2 watching the checkout clip",
      grounded: true,
    },
    {
      id: "service_s",
      label: "Service time",
      value: 60,
      unit: "s/customer",
      definition: "Lower bound of the 60–90 s per customer service window",
      grounded: true,
    },
    {
      id: "wait_s",
      label: "Estimated wait",
      value: 300,
      unit: "s",
      definition: "Queue check wait estimate with 8 customers waiting",
      grounded: true,
    },
  ],
  hidden: [],
  plan: {
    id: "mp-adv-retail-694d4d5fc2c4",
    primary_metric: "customers waiting",
    guard_metrics: ["open tills"],
    design: "switchback",
    block_minutes: 30,
    n_pairs: 6,
    min_detectable_effect: 2.1,
    smallest_possible_p: 0.03125,
    decision_rule: "worked: the whole 95% interval of (ON − OFF) customers waiting is below 0 after 6 pairs.",
    preregistration_hash: "e5bc76f3185a271083a7487e670ac8822d8e7451f71a64c511ae790b57690ff2",
  },
  sigma_note: "standard deviation across stored checkout-queue readings",
  watch: {
    kind: "queue",
    camera_id: "checkout-queue",
    every_s: 120,
    role: "duty-manager",
    focus: "Count waiting customers and open tills",
  },
  ask_receipt: RECORDED_RECEIPT,
  question: RECORDED_ANSWER.question,
  created_at: "2026-11-04T01:14:20Z",
  seconds: 19.5,
  preregistered_seq: 16,
  watch_id: "watch-queue-checkout",
  progress: {
    status: "measuring",
    preregistered_seq: 16,
    watch_readings: 4,
    mean_reading: 8.2,
    pairs_done: 1,
    pairs_planned: 6,
  },
  blocks: [
    {
      state: "on",
      start: "2026-11-04T01:15:00Z",
      end: "2026-11-04T01:45:00Z",
      readings: 3,
      mean: 8.0,
      seq: 23,
    },
  ],
};

export const RECORDED_BRIEF: Brief = {
  day: "2026-11-04",
  written_at: "2026-11-04T01:22:10Z",
  lang: "English",
  headline: "Busy tills went unanswered, shelves and spill cleared with dual-model photo proof",
  text: "Across 18 queue checks, 6 floor hazard checks, and 3 shelf gap scans today, the checkout queue peaked at 8–11 customers waiting with 2 tills open (~300 s wait). A switchback test for a 3rd till is pre-registered at chain record #16. On the sales floor, an aisle 3 milk spill (record #19) was cleared and verified by both Muse Spark (9.8 s) and Muse Glimmer 30B (15.1 s) in 1.9 minutes (record #21), and 11 bare facings on the pasta aisle were restocked and photo-verified in 2.0 minutes (record #20).",
  decisions: [
    "Keep the 3rd till switchback running for the remaining 5 block pairs on checkout-queue.",
    "Require staff to tap 'On my way' on phone alerts so till response times register on the Chain Mode scorecard.",
  ],
  stats: {
    queue_checks: 18,
    hazard_checks: 6,
    shelf_checks: 3,
    alerts_sent: 9,
    tasks_done: 3,
    chain_records: 28,
  },
  model: "muse-spark-1.3",
  seconds: 11.1,
};

export const RECORDED_OVERVIEW: Overview = {
  cameras: [
    {
      camera_id: "checkout-queue",
      name: "Checkout Queue (Pexels 39221981)",
      first: "2026-11-04T00:55:00Z",
      last: "2026-11-04T01:25:00Z",
    },
    {
      camera_id: "checkout-lanes",
      name: "Checkout Lanes (Pexels 39221979)",
      first: "2026-11-04T00:55:00Z",
      last: "2026-11-04T01:24:30Z",
    },
    {
      camera_id: "aisle-3",
      name: "Aisle 3 Floor & Exit (Pexels 39238831)",
      first: "2026-11-04T00:58:00Z",
      last: "2026-11-04T01:24:00Z",
    },
    {
      camera_id: "front-tills",
      name: "Front-End Staffed Tills (Pexels 4249560)",
      first: "2026-11-04T00:56:00Z",
      last: "2026-11-04T01:23:00Z",
    },
  ],
  watches: [
    {
      id: "watch-queue-checkout",
      kind: "queue",
      camera_id: "checkout-queue",
      every_s: 120,
      role: "duty-manager",
      focus: "Open a 3rd till when waiting customers exceed 6",
      active: true,
      created_at: "2026-11-04T01:14:25Z",
      last_run: "2026-11-04T01:16:31Z",
      advice_id: "adv-retail-694d4d5fc2c4",
    },
  ],
  findings: [
    {
      id: "find-queue-0116",
      kind: "queue",
      camera_id: "checkout-queue",
      camera_name: "Checkout Queue",
      at: "2026-11-04T01:16:10Z",
      checked_at: "2026-11-04T01:16:31Z",
      watch_id: "watch-queue-checkout",
      act: true,
      headline: "8 waiting, 2 of 2 tills open; about 300 s wait — open another till",
      staff_message: "Open a 3rd checkout till now: queue rose from 6 to 8 waiting (~5 min wait).",
      detail: {
        reason: "Queue worsened from earlier reading of 6 waiting customers; re-alerting duty-manager.",
        announcement:
          "A new till is opening, please come through. / Una nueva caja está abriendo, por favor pase.",
        lanes: [{ id: 1, open: true }, { id: 2, open: true }],
      },
      evidence: { frames: [], marked: null, clip: null },
      calls: [
        { model: "sam-3.1", role: "3-frame person boxes", seconds: 2.7, error: null },
        { model: "muse-spark-1.3", role: "queue judge", seconds: 16.3, error: null },
      ],
      seconds: 20.6,
      alert_id: "al-queue-1",
      task_id: null,
      metric: 8,
      chain_seq: null,
      alert_status: "sent",
    },
    {
      id: "find-hazard-0118",
      kind: "hazard",
      camera_id: "aisle-3",
      camera_name: "Aisle 3 (SAMPLE spill edit)",
      at: "2026-11-04T01:18:16Z",
      checked_at: "2026-11-04T01:18:44Z",
      watch_id: null,
      act: true,
      headline: "1 confirmed hazard: liquid milk spill on aisle 3 floor",
      staff_message: "Wet floor hazard in Aisle 3 — place cone, mop spill, and upload a photo when clear.",
      detail: {
        summary: "Muse Spark found the spill (10.5 s); SAM 3.1 segmented 'puddle of milk' inside the box (4.2 s); Muse Glimmer 30B confirmed on the marked frame (12.5 s).",
      },
      evidence: { frames: [], marked: null, clip: null },
      calls: [
        { model: "muse-spark-1.3", role: "hazard finder", seconds: 10.5, error: null },
        { model: "sam-3.1", role: "puddle of milk box", seconds: 4.2, error: null },
        { model: "muse-glimmer-30b", role: "second look confirm", seconds: 12.5, error: null },
      ],
      seconds: 27.7,
      alert_id: "al-hazard-19",
      task_id: "task-hazard-19",
      metric: 1,
      chain_seq: 19,
      alert_status: "acknowledged",
    },
    {
      id: "find-shelf-0117",
      kind: "shelf",
      camera_id: "aisle-3",
      camera_name: "Pasta Aisle (Wikimedia CC BY-SA 4.0)",
      at: "2026-11-04T01:17:00Z",
      checked_at: "2026-11-04T01:17:51Z",
      watch_id: null,
      act: true,
      headline: "11 empty shelf gaps (~136 facings) on the dried pasta aisle",
      staff_message: "Restock 11 bare pasta shelf gaps and upload an after photo to close.",
      detail: {
        summary: "Muse Spark located 11 gaps on the 0–1000 grid; SAM 3.1 verified product package coverage ≤ 25% in each gap.",
      },
      evidence: { frames: [], marked: null, clip: null },
      calls: [
        { model: "muse-spark-1.3", role: "gap grid finder", seconds: 21.4, error: null },
        { model: "sam-3.1", role: "product package check", seconds: 4.7, error: null },
      ],
      seconds: 51.1,
      alert_id: "al-shelf-18",
      task_id: "task-shelf-18",
      metric: 11,
      chain_seq: null,
      alert_status: "acknowledged",
    },
    {
      id: "find-exit-0135",
      kind: "exit",
      camera_id: "aisle-3",
      camera_name: "Fire Exit Door (SAMPLE pallet edit)",
      at: "2026-11-04T01:35:00Z",
      checked_at: "2026-11-04T01:35:14Z",
      watch_id: null,
      act: true,
      headline: "Fire exit blocked for 300 s (limit 300 s) by pallet of shrink-wrapped water bottles",
      staff_message: "Clear the pallet blocking the fire exit door immediately (blocked 5.0 min).",
      detail: {
        summary: "Unbroken blocked run across readings at 0, 2, and 5 min; SAM 3.1 confirmed obstruction inside Spark's box. Cleared at minute 8.",
      },
      evidence: { frames: [], marked: null, clip: null },
      calls: [
        { model: "muse-spark-1.3", role: "exit obstruction judge", seconds: 9.4, error: null },
        { model: "sam-3.1", role: "obstruction box", seconds: 3.1, error: null },
      ],
      seconds: 12.5,
      alert_id: "al-exit-25",
      task_id: null,
      metric: 1,
      chain_seq: null,
      alert_status: "sent",
    },
  ],
  tasks: [
    {
      id: "task-hazard-19",
      kind: "hazard",
      finding_id: "find-hazard-0118",
      camera_id: "aisle-3",
      where: "Aisle 3 floor (milk spill · SAMPLE edit)",
      items: ["Liquid spill on aisle floor — mop and dry completely"],
      marked: null,
      seen_at: "2026-11-04T01:18:44Z",
      created_at: "2026-11-04T01:18:44Z",
      status: "done",
      done_at: "2026-11-04T01:20:39Z",
      closed_by: "photo",
      minutes_to_fix: 1.9,
      alert_id: "al-hazard-19",
      role: "floor",
      proof_image: null,
      proof: {
        closed: true,
        spark: { summary: "Spill is gone; aisle floor is clean and dry (9.8 s)." },
        glimmer: { summary: "Confirmed clean floor on the after photo (15.1 s)." },
      },
      detected_seq: 19,
      fixed_seq: 21,
    },
    {
      id: "task-shelf-18",
      kind: "shelf",
      finding_id: "find-shelf-0117",
      camera_id: "aisle-3",
      where: "Dried pasta aisle (11 gaps, ~136 facings)",
      items: [
        "11 bare pasta shelf gaps (negative control with unchanged photo was rejected first in 41.7 s; SAMPLE restocked photo closed all 11 gaps)",
      ],
      marked: null,
      seen_at: "2026-11-04T01:17:51Z",
      created_at: "2026-11-04T01:17:51Z",
      status: "done",
      done_at: "2026-11-04T01:19:51Z",
      closed_by: "photo",
      minutes_to_fix: 2.0,
      alert_id: "al-shelf-18",
      role: "stock",
      proof_image: null,
      proof: {
        closed: true,
        spark: { summary: "All 11 numbered shelf gaps are now stocked with pasta packages (41.2 s)." },
      },
      detected_seq: null,
      fixed_seq: 20,
    },
  ],
  cards: [RECORDED_CARD],
  asks: [
    {
      receipt: RECORDED_RECEIPT,
      question: RECORDED_ANSWER.question,
      answer: RECORDED_ANSWER.answer,
      models: RECORDED_ANSWER.models,
      seconds: RECORDED_ANSWER.seconds,
      ts: "2026-11-04T01:13:55Z",
      conversation_id: RECORDED_ANSWER.conversation_id,
    },
  ],
  brief: RECORDED_BRIEF,
};

const SPANISH_VOICE_RUN = {
  spoken: "¿Cuántos clientes esperaban en las cajas y cuántas cajas estaban abiertas?",
  transcribe_s: 3.2,
  ask_s: 41.8,
  total_s: 45.0,
  answer_es:
    "En las cámaras de caja había alrededor de 9 clientes esperando y 2 cajas abiertas en cada vista; las tres cámaras de caja se superponen, por lo que no se suman entre sí.",
};

export function SupermarketRecordedRun({
  onLoadIntoSteps,
  loaded,
}: {
  onLoadIntoSteps: () => void;
  loaded: boolean;
}) {
  const [tab, setTab] = useState<"e2e" | "exit" | "scorecard">("e2e");

  return (
    <section className={styles.recordedSection} aria-labelledby="sm-recorded-head" data-testid="supermarket-recorded-run">
      <div className={styles.recordedHeader}>
        <div className={styles.row}>
          <LiveBadge mode="replay" detail="recorded 4 Nov 2026 · 13 live model calls · 28 chain records" />
          <span className={styles.pill}>receipt 21c820c291dc…</span>
        </div>
        <div className={styles.recordedTitleRow}>
          <div>
            <h3 id="sm-recorded-head" className={styles.recordedTitle}>
              The 4 Nov store run
            </h3>
            <p className={styles.muted}>
              Every number below was recorded live against Meta&apos;s API (Muse Spark 1.3 &amp; 1.2, hosted SAM 3.1,
              Muse Voice Transcribe, Muse Image) and RunPod serverless SAM 3.1 + Muse Glimmer 30B (reports/eval/2026-11-04-supermarket-live.md).
            </p>
          </div>
          <button
            type="button"
            className={loaded ? styles.ghost : styles.button}
            onClick={onLoadIntoSteps}
          >
            {loaded ? "Showing in steps 1–5 below" : "Show this run in steps 1–5"}
          </button>
        </div>

        <div className={styles.kpiStrip} role="list" aria-label="4 Nov run key metrics">
          <div className={styles.kpiItem} role="listitem">
            <span className={styles.kpiValue}>10–11</span>
            <span className={styles.kpiLabel}>customers waiting once the three counts were reconciled; the tracker said 22</span>
          </div>
          <div className={styles.kpiItem} role="listitem">
            <span className={styles.kpiValue}>#16</span>
            <span className={styles.kpiLabel}>chain record where the till test was pre-registered (6 pairs of 30-minute blocks)</span>
          </div>
          <div className={styles.kpiItem} role="listitem">
            <span className={styles.kpiValue}>1.9 min</span>
            <span className={styles.kpiLabel}>from seeing the spill to proof it was cleared (records #19 to #21)</span>
          </div>
          <div className={styles.kpiItem} role="listitem">
            <span className={styles.kpiValue}>45.0 s</span>
            <span className={styles.kpiLabel}>for a spoken Spanish question (transcribe 3.2 s, answer 41.8 s)</span>
          </div>
        </div>

        <div className={styles.tabBar} role="tablist" aria-label="Recorded supermarket views">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "e2e"}
            className={`${styles.tabBtn} ${tab === "e2e" ? styles.tabBtnActive : ""}`}
            onClick={() => setTab("e2e")}
          >
            Ask to brief
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "exit"}
            className={`${styles.tabBtn} ${tab === "exit" ? styles.tabBtnActive : ""}`}
            onClick={() => setTab("exit")}
          >
            Fire exit rule
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "scorecard"}
            className={`${styles.tabBtn} ${tab === "scorecard" ? styles.tabBtnActive : ""}`}
            onClick={() => setTab("scorecard")}
          >
            Store scorecard
          </button>
        </div>
      </div>

      {tab === "e2e" && (
        <div className={styles.grid2}>
          <div className={styles.card}>
            <div className={styles.row}>
              <span className={styles.stepLabel}>Ask, in English and Spanish</span>
              <span className={styles.muted}>60.4 s &amp; 45.0 s</span>
            </div>
            <strong>“{RECORDED_ANSWER.question}”</strong>
            <p className={styles.muted}>{RECORDED_ANSWER.answer}</p>
            <div className={styles.reasoning}>
              <span>Spoken Spanish question (Muse Voice Transcribe 3.2 s, exact words)</span>
              <div>“{SPANISH_VOICE_RUN.spoken}”</div>
              <div className={styles.muted} style={{ marginTop: 4 }}>
                → “{SPANISH_VOICE_RUN.answer_es}”
              </div>
            </div>
            <div className={styles.row}>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => speak(RECORDED_ANSWER.answer, "en")}
              >
                Read English answer
              </button>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => speak(SPANISH_VOICE_RUN.answer_es, "es")}
              >
                Escuchar respuesta (ES)
              </button>
            </div>
          </div>

          <div className={styles.card}>
            <div className={styles.row}>
              <span className={styles.stepLabel}>Advice, watch and proof</span>
              <span className={styles.muted}>chain records #16, #19–#21</span>
            </div>
            <strong>{RECORDED_CARD.title}</strong>
            <p className={styles.muted}>{RECORDED_CARD.gain}</p>
            <ul className={styles.compactList}>
              <li>
                <strong>Queue watch (20.6 s):</strong> SAM 3.1 (2.7 s) + Spark (16.3 s) saw 8 waiting with 2 tills open; announced in EN + ES: <em>“Una nueva caja está abriendo, por favor pase.”</em>
              </li>
              <li>
                <strong>Negative control (7.1 s &amp; 41.7 s):</strong> Real clean floor reported <em>“No floor hazard seen”</em>; submitting the unchanged bare shelf photo as proof was rejected by Spark (<em>“no restock visible in any numbered gap”</em>).
              </li>
              <li>
                <strong>Dual-model closure (1.9 min &amp; 2.0 min):</strong> Pasta shelf closed at record <code>#20</code>; milk spill closed at record <code>#21</code> after both Spark (9.8 s) and Glimmer 30B (15.1 s) verified the clean floor.
              </li>
            </ul>
          </div>
        </div>
      )}

      {tab === "exit" && (
        <div className={styles.card}>
          <div className={styles.row}>
            <strong>“Fire exit blocked over 5 minutes”</strong>
            <LiveBadge mode="sample" detail="frames are Muse Image edits (pallet of water bottles)" />
          </div>
          <p className={styles.muted}>
            Muse Spark 1.3 judges whether the exit is blocked and names the obstruction; SAM 3.1 must segment that
            exact phrase inside Spark&apos;s box; code measures the unbroken blocked duration and alerts at 300 s.
          </p>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Minute</th>
                  <th>Muse Spark 1.3: blocked by</th>
                  <th>SAM 3.1 boxes</th>
                  <th>Blocked duration</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className={styles.mono}>0 min</td>
                  <td>pallet stacked with bottled water</td>
                  <td className={styles.mono}>2</td>
                  <td className={styles.mono}>0 s</td>
                  <td className={styles.muted}>Watching (below 300 s limit)</td>
                </tr>
                <tr>
                  <td className={styles.mono}>2 min</td>
                  <td>pallet of shrink-wrapped water bottles</td>
                  <td className={styles.mono}>1</td>
                  <td className={styles.mono}>120 s</td>
                  <td className={styles.muted}>Watching (below 300 s limit)</td>
                </tr>
                <tr>
                  <td className={styles.mono}>5 min</td>
                  <td>pallet of bottled water</td>
                  <td className={styles.mono}>1</td>
                  <td className={styles.mono}>300 s</td>
                  <td>
                    <strong className={styles.alertText}>ALERT SENT</strong> (limit reached, task opened)
                  </td>
                </tr>
                <tr>
                  <td className={styles.mono}>6 min</td>
                  <td>pallet of shrink-wrapped bottled water</td>
                  <td className={styles.mono}>2</td>
                  <td className={styles.mono}>360 s</td>
                  <td className={styles.muted}>Joins open task (no duplicate alert)</td>
                </tr>
                <tr>
                  <td className={styles.mono}>8 min</td>
                  <td>clear</td>
                  <td className={styles.mono}>0</td>
                  <td className={styles.mono}>0 s</td>
                  <td>
                    <strong className={styles.okText}>CLEARED</strong> (task auto-closes on clean camera re-check)
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "scorecard" && (
        <div className={styles.card}>
          <div className={styles.row}>
            <strong>This store on the Chain Mode scorecard</strong>
            <span className={styles.muted}>00:20–02:10 UTC, 4 Nov 2026</span>
          </div>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Chain Rule</th>
                  <th>On time</th>
                  <th>Late</th>
                  <th>Scorecard Status</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Spills, hazards and exits made safe within 15 min (retail-hazard-cleared)</td>
                  <td className={styles.mono}>1</td>
                  <td className={styles.mono}>2</td>
                  <td className={styles.muted}>Too few to show a rate (small-crew floor of 5)</td>
                </tr>
                <tr>
                  <td>Shelf gaps refilled within 60 min (retail-gap-refilled)</td>
                  <td className={styles.mono}>1</td>
                  <td className={styles.mono}>1</td>
                  <td className={styles.muted}>Too few to show a rate (small-crew floor of 5)</td>
                </tr>
                <tr>
                  <td>Till alerts answered (retail-till-answered)</td>
                  <td className={styles.mono}>0</td>
                  <td className={styles.mono}>3</td>
                  <td>
                    <span className={styles.pill}>camera blind</span> Test camera had video for 5.5% of the window
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
