// Site Advisor card shapes (rulecam.advisor.api.AdviceCard, contracts.advisor) and pure helpers.

export type Provenance = "recorded" | "upload" | "public" | "simulated";

export interface SlotClaim {
  template: string;
  slots: Record<string, string>;
  grade: "A" | "B" | "C";
}

export interface Explanation {
  id: string;
  statement: string;
  status: "supported" | "partly" | "ruled_out" | "untestable";
  share_of_gap?: number | null;
}

export interface Change {
  op: string;
  params: Record<string, string>;
}

export interface Gain {
  metric: string;
  interval: [number, number];
  level: number;
  unit: string;
  model_rung: string;
  assumptions: Record<string, "pass" | "fail" | "n/a">;
}

export interface Recommendation {
  id: string;
  status: "proposed" | "accepted" | "measuring" | "adopted" | "reverted" | "rejected";
  scope_kind: string;
  scope_ids: string[];
  klass: "do" | "test" | "hold";
  provenance: Provenance;
  saw: SlotClaim[];
  why: Explanation[];
  do: Change[];
  who_role: string;
  revert: string;
  expect: Gain;
  safety: {
    rules_checked: string[];
    verdict: "pass" | "pass_with_mitigation" | "veto";
    considered_not_recommended: [string, string][];
  };
  rank_score: number;
  measurement_plan_id?: string | null;
  numbers_total: number;
  numbers_reproduced: number;
}

export interface NumberCheck {
  result_id: string;
  metric: string;
  catalogue_value: number;
  unit: string;
  recomputed: number | null;
  sql: string;
  ok: boolean;
  error: string;
}

export interface MeasurementPlan {
  id: string;
  design: "switchback" | "its" | "did";
  block_minutes: number;
  n_pairs: number;
  min_detectable_effect: number;
  smallest_possible_p: number;
  decision_rule: string;
  preregistration_hash: string;
}

export interface Verdict {
  plan_id: string;
  label: "worked" | "no_effect" | "worse" | "not_enough_data";
  effect: number;
  ci: [number, number];
  p_value: number;
  pairs_done: number;
}

export interface AdviceCard {
  recommendation: Recommendation;
  checks: NumberCheck[];
  hidden: string[];
  flagged: boolean;
  measurement_plan?: MeasurementPlan | null;
  preregistered_seq?: number | null;
  verdict?: Verdict | null;
}

export interface SealStatus {
  seal_id: string;
  sealed: boolean;
  revealed: boolean;
  matches: boolean | null;
  cause: string | null;
}

export type ClaimPart =
  | { text: string }
  | { check: NumberCheck; hidden: boolean }
  | { missing: string };

/** Split a slot template into text and numbers ("{m1}" → its check), keeping hidden numbers marked. */
export function claimParts(claim: SlotClaim, card: AdviceCard): ClaimPart[] {
  const parts: ClaimPart[] = [];
  const pattern = /\{([A-Za-z0-9_]+)\}/g;
  let last = 0;
  for (const match of claim.template.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: claim.template.slice(last, index) });
    const resultId = claim.slots[match[1] ?? ""];
    const check = card.checks.find((c) => c.result_id === resultId);
    parts.push(
      check
        ? { check, hidden: card.hidden.includes(check.result_id) }
        : { missing: match[1] ?? "" },
    );
    last = index + match[0].length;
  }
  if (last < claim.template.length) parts.push({ text: claim.template.slice(last) });
  return parts;
}

/** A number as the card prints it ("14 min"); integers stay integers, others keep one decimal. */
export function formatNumber(value: number, unit: string): string {
  const shown = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return unit ? `${shown} ${unit}` : shown;
}

export const VERDICT_TEXT: Record<Verdict["label"], string> = {
  worked: "Worked",
  no_effect: "No effect",
  worse: "Worse",
  not_enough_data: "Not enough data yet",
};

/** The verdict line, e.g. "Worked: −9 min/truck (CI −14 to −4), 6 pairs". */
export function verdictLine(verdict: Verdict, unit: string): string {
  const n = (x: number): string => (x < 0 ? `−${Math.abs(x)}` : String(x));
  const effect = `${n(verdict.effect)} ${unit}`.trim();
  return `${VERDICT_TEXT[verdict.label]}: ${effect} (CI ${n(verdict.ci[0])} to ${n(verdict.ci[1])}), ${verdict.pairs_done} pairs`;
}

/** Map a value onto [0, width] for the dot plot, given a symmetric range around zero. */
export function scaleX(value: number, range: number, width: number): number {
  const clamped = Math.max(-range, Math.min(range, value));
  return ((clamped + range) / (2 * range)) * width;
}
