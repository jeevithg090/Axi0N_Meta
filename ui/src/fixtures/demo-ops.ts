// Recorded-style frames for the operations screens in `?demo=1`: UC3 escalation alerts, the UC1
// sink station, UC2 Rule CI ("18 lookalikes, fooled 1 (strap)") and a UC4 surprise (stack topple).

import type { Frame } from "@/lib/events";
import type { Alert, CIResult, SurpriseEvent } from "@/lib/ops";

import ciResults from "./ci-results.json";

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

// Two real `ci.result` payloads: `CIResult.of(report).model_dump(mode="json")` from the Python
// models (tests/rule_ci/test_ui_fixture.py checks they still validate and round-trip).
export const demoCiResults = ciResults as CIResult[];

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
  ...demoCiResults.map((payload): Frame => ({ topic: "ci.result", payload })),
  { topic: "surprise.new", payload: demoSurprise },
];
