// A recorded-style UC1 sequence for screen tests (`?demo=1` replays the golden runs instead): an
// 8 s wash fails, Glimmer confirms, coaching plays, then a tray blocks cam 1 and its card goes
// BLIND. Labelled REPLAY.

import type { Frame } from "@/lib/events";

import { demoPlannerSteps, demoPlanV2 } from "./demo-plan";

const T0 = "2026-11-01T18:00:00Z";

export const demoFrames: Frame[] = [
  { topic: "mode.changed", payload: { mode: "replay", badge: "REPLAY" } },
  ...demoPlannerSteps.map((payload): Frame => ({ topic: "planner.step", payload })),
  { topic: "plan.updated", payload: demoPlanV2 },
  {
    topic: "relay.model_used",
    payload: { model: "sam-3.1", segment: "perception", latency_ms: 410 },
  },
  {
    topic: "candidate.new",
    payload: {
      event_id: "ev-uc1-0001",
      rule_id: "wash-before-line",
      camera_id: "cam1",
      ts_start: T0,
      ts_end: "2026-11-01T18:00:08Z",
      severity: "medium",
      measurement: { value: 8, lo: 7.6, hi: 8.4, unit: "s" },
      predicate_trace: { subject: "cam1:person:1", "requirement.dwell_s": 8, required_s: 20 },
      strip_paths: [],
    },
  },
  {
    topic: "relay.model_used",
    payload: { model: "muse-glimmer-30b", segment: "verify", latency_ms: 1840 },
  },
  {
    topic: "verdict.new",
    payload: {
      event_id: "ev-uc1-0001",
      verdict: {
        tier: "glimmer",
        verdict: "violation",
        score: 0.91,
        reason: "Hands leave the sink after about 8 seconds, then the person steps onto the line.",
        reasoning: null,
        scene_text: "WASH HANDS",
        tamper: false,
        latency_ms: 1840,
        model_version: "muse-glimmer-30b",
      },
    },
  },
  {
    topic: "relay.model_used",
    payload: { model: "mms-tts-spa", segment: "verify", latency_ms: 35 },
  },
  {
    topic: "action.done",
    payload: {
      event_id: "ev-uc1-0001",
      action: {
        kind: "coach_station",
        role: null,
        gate: "auto",
        ts: T0,
        ok: true,
        detail: "played es.wav, en.wav; station display",
      },
    },
  },
  {
    topic: "action.done",
    payload: {
      event_id: "ev-uc1-0001",
      action: {
        kind: "notify",
        role: "shift_lead",
        gate: "auto",
        ts: T0,
        ok: true,
        detail: "notified shift_lead (al-1)",
      },
    },
  },
  {
    topic: "coverage.changed",
    payload: {
      camera_id: "cam1",
      state: "blind",
      reason: "blocked",
      moved: false,
      ts: "2026-11-01T18:00:30Z",
    },
  },
];
