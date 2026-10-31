// Recorded-style UC1 plan for `?demo=1` and screen tests. Version 1 is the approved plan;
// version 2 is a proposal from the self-repair loop (tighter glove rule, one suggested rule).

import type { AcceptanceCheck, PlannerStepFrame, Rule, SitePlan } from "@/lib/plan";
import { PLANNER_STEPS } from "@/lib/plan";

const wash: Rule = {
  id: "wash-before-line",
  version: 1,
  source: {
    kind: "photo",
    text: "Wash hands for 20 s before entering the line",
    lang: "es",
    standard: ["Food Code 2026 §2-301.12", "§2-103.11(G)"],
  },
  predicate: { op: "sequence", semantics: "past", require_within_s: 300 },
  severity: "medium",
  actions: [
    { kind: "coach_station", lang: "crew" },
    { kind: "notify", role: "shift_lead" },
  ],
};

const gloves: Rule = {
  id: "gloves-rte",
  version: 1,
  source: {
    kind: "voice",
    text: "Gloves for ready-to-eat prep",
    lang: "es",
    standard: ["§3-301.11"],
  },
  predicate: { op: "persist", seconds: 3 },
  severity: "low",
  actions: [{ kind: "coach_station", lang: "crew" }],
};

const checks = (n: number, failing: string[] = []): AcceptanceCheck[] =>
  Array.from({ length: n }, (_, i) => {
    const check = [
      "zone_in_view",
      "detect_lb>=0.8",
      "ci_green",
      "nonvacuous",
      "citation_text_match",
    ][i % 5]!;
    const item = i < 9 ? "wash-before-line" : i < 18 ? "gloves-rte" : "plan";
    const name = `${item}:${check}:${i}`;
    return { item, check, passed: !failing.includes(name), detail: "" };
  });

export const demoPlanV1: SitePlan = {
  plan_id: "main-st-kitchen",
  version: 1,
  site_name: "Main St kitchen",
  setting_inferred: "commercial_kitchen",
  languages: ["es", "en"],
  inventory: [
    { concept: "sink", camera_id: "cam1", count: 1, mean_confidence: 0.94 },
    { concept: "hands", camera_id: "cam1", count: 2, mean_confidence: 0.88 },
    { concept: "gloves", camera_id: "cam2", count: 1, mean_confidence: 0.81 },
  ],
  feasibility: [
    {
      concern: "Handwashing for 20 s before the line",
      verdict: "observable",
      says: "I can see the sink and hands clearly on cam 1.",
      evidence: [
        {
          concept: "hands",
          camera_id: "cam1",
          n_frames: 12,
          detect_rate: 0.92,
          detect_lb: 0.86,
          median_px_height: 48,
        },
      ],
    },
    {
      concern: "Gloves for ready-to-eat prep",
      verdict: "caveat",
      says: "I can see gloves, but the prep counter is at the edge of cam 2.",
      evidence: [
        {
          concept: "gloves",
          camera_id: "cam2",
          n_frames: 12,
          detect_rate: 0.75,
          detect_lb: 0.62,
          median_px_height: 22,
        },
      ],
      alternative: "Move cam 2 about 0.5 m toward the prep counter.",
    },
    {
      concern: "Cold holding at 41 °F",
      verdict: "not_observable",
      says: "I can't see a thermometer, so I can't check cold holding.",
      evidence: [],
      alternative: "Point a phone at the dial, or add a temperature sensor.",
    },
    {
      concern: "Tell me if a cook is sick",
      verdict: "declined",
      says: "I won't judge anyone's health from video.",
      evidence: [],
      reason: "Clinical inference is a guardrail (MP §3.6).",
      alternative: "A shift-start reminder of your written illness policy.",
    },
  ],
  rules: [wash, gloves],
  privacy_mode: "anonymous",
  retention_days: 30,
  acceptance: { checks: checks(27), rounds: 1 },
  audit: [
    {
      by: "spark-open",
      text: "The back-door sink isn't covered by any camera.",
      reasoning_ref: "so_3f1a9c2b7d4e6a01",
    },
  ],
  back_translation:
    "Lávate las manos 20 segundos antes de entrar a la línea. Guantes para preparar comida lista.",
  standards: ["Food Code 2026"],
};

export const demoPlanV2: SitePlan = {
  ...demoPlanV1,
  version: 2,
  rules: [
    wash,
    { ...gloves, version: 2, predicate: { op: "persist", seconds: 2 } },
    {
      id: "spill-no-sign",
      version: 1,
      source: {
        kind: "surprise_event",
        text: "Spill with no wet-floor sign for over 60 s",
        standard: [],
      },
      predicate: { op: "persist", seconds: 60 },
      severity: "low",
      actions: [{ kind: "notify", role: "shift_lead" }],
    },
  ],
  retention_days: 14,
  acceptance: { checks: checks(30, ["plan:ci_green:22"]), rounds: 2 },
};

export const demoPlannerSteps: PlannerStepFrame[] = PLANNER_STEPS.map((step, index) => ({
  plan_id: "main-st-kitchen",
  step,
  status: "done",
  detail: index === 7 ? "27/27 checks" : "",
}));
