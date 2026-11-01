// Golden runs the site replays on `?demo=1` (or `?demo=<name>`): live record runs on the API pod,
// frozen by `rulecam golden capture` and exported by `rulecam golden export` after
// `rulecam golden verify` passed. Nothing here is hand-written: the JSON is generated.
// To add a run (e.g. the owner's positive clip), export it and list it in GOLDEN_RUNS.

import type { Frame, VerifierVerdict } from "@/lib/events";
import type { AcceptanceCheck, SitePlan } from "@/lib/plan";

import warehouseDriver from "./warehouse_driver.json";

export interface GoldenPlannerStep {
  step: string;
  status: string;
  detail: string;
}

export interface GoldenDecision {
  alert: boolean;
  route: "auto" | "human_queue";
  act_first: boolean;
  reason: string;
}

export interface GoldenVerification {
  event_id: string;
  rule_id: string;
  severity: string;
  measurement: { value: number; unit: string } | null;
  strip_frames: number;
  act_first: boolean;
  tier1: Omit<VerifierVerdict, "latency_ms">;
  tier1_matches_chain: boolean;
  decision: GoldenDecision;
  decision_matches_chain: boolean;
}

/** The verified outputs of `rulecam golden verify`, one per step kind. */
export interface GoldenSteps {
  plan: {
    plan_id: string;
    request: string | null;
    rule_hashes_match_run: boolean;
    rules: { id: string; severity: string; canonical: boolean; rule_ir_hash: string }[];
    acceptance: AcceptanceCheck[];
    planner_steps: GoldenPlannerStep[];
  };
  rule_ci: {
    rule_id: string;
    summary: string;
    ci_green: boolean;
    nonvacuous: boolean;
    failures: string[];
    matches_plan: boolean;
  }[];
  evidence: {
    ok: boolean;
    n: number;
    root: string;
    candidates: number;
    reproduced: number;
    run_seqs: number[];
  };
  verify: GoldenVerification[];
  score: {
    clip: string;
    status: string;
    candidates: number;
    alerts: number;
    false_alerts_on_negative: number;
    tier1: (string | null)[];
  };
}

export interface GoldenRun {
  name: string;
  description: string;
  recorded_at: string;
  camera_id: string;
  clip: { name?: string; description?: string; license?: string; source?: string | null };
  plan: SitePlan;
  frames: Frame[];
  steps: GoldenSteps;
  chain_url: string;
  verify_url: string;
}

/** A golden run that is planned but not recorded yet (the slot is shown on the demo page). */
export interface PendingRun {
  name: string;
  title: string;
  needs: string;
}

export const GOLDEN_RUNS: GoldenRun[] = [warehouseDriver as unknown as GoldenRun];

export const PENDING_RUNS: PendingRun[] = [
  {
    name: "owner_fl_pos_1m",
    title: "Positive: a pedestrian passes a moving forklift at about 1 m",
    needs:
      "The owner's clip (eval/clips/owner_fl_pos_1m). Same pipeline: record run, " +
      "`rulecam golden capture`, `golden record`, `golden export`.",
  },
];

export const DEFAULT_GOLDEN = GOLDEN_RUNS[0]!.name;

/**
 * The golden run a `demo` URL parameter selects: `1` is the default run, a name selects that run,
 * anything else (or no parameter) is not a demo.
 */
export function goldenRun(param: string | null): GoldenRun | null {
  if (param === null) return null;
  const name = param === "1" ? DEFAULT_GOLDEN : param;
  return GOLDEN_RUNS.find((run) => run.name === name) ?? null;
}
