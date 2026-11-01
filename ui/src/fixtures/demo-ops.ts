// Recorded-style frames for the operations screens in `?demo=1`: UC3 escalation alerts, the UC1
// sink station, UC2 Rule CI ("18 lookalikes, fooled 1 (strap)") and a UC4 surprise (stack topple).

import type { Frame } from "@/lib/events";
import type { Alert, RuleCIReport, SurpriseEvent } from "@/lib/ops";

const T = (s: number): string => new Date(Date.UTC(2026, 10, 1, 22, 0, s)).toISOString();

export const demoAlerts: Alert[] = [
  {
    alert_id: "al-1",
    event_id: "ev-uc3-0007",
    rule_id: "person-down",
    role: "aide",
    title: "Alert: Person down for more than 10 s",
    body: "Camera corridor-east at 22:00:12 UTC (high)",
    clip_path: null,
    buttons: ["On my way", "False alarm", "Escalate"],
    ts: T(12),
    status: "open",
  },
];

const lookalikes = [
  "shadow",
  "poster person",
  "backpack strap",
  "wet glove",
  "reflective vest on a chair",
  "mannequin",
  "hard hat on a shelf",
  "safety cone",
  "coat on a hook",
  "ladder in storage",
  "person on a monitor",
  "orange bucket",
  "hi-vis bag",
  "glove on a table",
  "harness on a hook",
  "rope coil",
  "broom",
  "child's drawing",
].map((name) => ({
  name,
  fooled: name === "backpack strap",
  fix: name === "backpack strap" ? 'Add the negative concept "backpack strap"' : null,
}));

export const demoCiReports: RuleCIReport[] = [
  {
    rule_id: "wash-before-line",
    traces_passed: 3,
    traces_total: 3,
    mutants_killed: 5,
    mutants_total: 5,
    failures: [],
    scenes: { passed: 11, total: 12 },
    lookalikes: lookalikes.slice(0, 6).map((l) => ({ ...l, fooled: false, fix: null })),
    injection: { variants: 6, still_alerted: 6, tamper_flagged: 5 },
    forecast: { alerts_per_shift: 2.4, budget_per_shift: 5 },
  },
  {
    rule_id: "harness-at-height",
    traces_passed: 4,
    traces_total: 4,
    mutants_killed: 6,
    mutants_total: 7,
    failures: ["mutant drop_not survived"],
    lookalikes,
    injection: { variants: 6, still_alerted: 6, tamper_flagged: 6 },
    forecast: { alerts_per_shift: 7.1, budget_per_shift: 5 },
  },
];

export const demoSurprise: SurpriseEvent = {
  event_id: "sp-uc4-0001",
  camera_id: "dock-cam",
  windows: [0.3, 0.1, 0.5, 0.2, 0.4, 0.6, 0.3, 4.9, 5.6, 3.1, 0.8, 0.4].map((z, i) => ({
    camera_id: "dock-cam",
    window_start: T(i * 2),
    score: 0.1 + z / 10,
    z,
  })),
  heatmap_path: "data/surprise/sp-uc4-0001.png",
  labels: ["cardboard box", "shelf"],
  peak_xy: [412, 188],
};

export const demoOpsFrames: Frame[] = [
  ...demoAlerts.map((payload): Frame => ({ topic: "alert.new", payload })),
  {
    topic: "action.done",
    payload: {
      event_id: "ev-uc3-0007",
      action: {
        kind: "notify",
        role: "aide",
        gate: "auto",
        ts: T(12),
        ok: true,
        detail: "step 1/3",
      },
    },
  },
  {
    topic: "station.coach",
    payload: {
      event_id: "ev-uc1-0001",
      rule_id: "wash-before-line",
      camera_id: "cam1",
      text: "Lávate las manos 20 segundos, por favor.",
      lang: "es",
    },
  },
  ...demoCiReports.map(
    (report): Frame => ({ topic: "ci.result", payload: { rule_id: report.rule_id, report } }),
  ),
  { topic: "surprise.new", payload: demoSurprise },
];
