// SitePlan shapes (rulecam.contracts.plan) and the pure helpers the plan screens use.

import { stringify } from "yaml";

export type Verdict = "observable" | "caveat" | "not_observable" | "declined";

export interface Probe {
  concept: string;
  camera_id: string;
  n_frames: number;
  detect_rate: number;
  detect_lb: number;
  median_px_height: number;
}

export interface FeasibilityVerdict {
  concern: string;
  verdict: Verdict;
  says: string;
  evidence: Probe[];
  alternative?: string | null;
  reason?: string | null;
}

export interface AcceptanceCheck {
  item: string;
  check: string;
  passed: boolean;
  detail: string;
}

export interface AuditFinding {
  by: "spark-open";
  text: string;
  reasoning_ref?: string | null;
}

export interface RuleAction {
  kind: string;
  role?: string | null;
  lang?: string | null;
  after_s?: number | null;
}

export interface Rule {
  id: string;
  version: number;
  source: { kind: string; text?: string | null; lang?: string | null; standard: string[] };
  predicate: Record<string, unknown>;
  severity: "low" | "medium" | "high";
  actions: RuleAction[];
  [key: string]: unknown;
}

export interface InventoryItem {
  concept: string;
  camera_id: string;
  count: number;
  mean_confidence: number;
}

export interface SitePlan {
  plan_id: string;
  version: number;
  site_name: string;
  setting_inferred: string;
  languages: string[];
  inventory: InventoryItem[];
  feasibility: FeasibilityVerdict[];
  rules: Rule[];
  privacy_mode: "standard" | "anonymous" | "strict";
  retention_days: number;
  acceptance: { checks: AcceptanceCheck[]; rounds: number };
  audit: AuditFinding[];
  back_translation: string;
  standards: string[];
  [key: string]: unknown;
}

export interface PlanSummary {
  plan_id: string;
  version: number;
  site_name: string;
  approved: boolean;
}

/** Colour family and text label of a feasibility verdict (never colour alone). */
export const VERDICT_STYLE: Record<
  Verdict,
  { tone: "green" | "amber" | "red" | "grey"; label: string }
> = {
  observable: { tone: "green", label: "Observable" },
  caveat: { tone: "amber", label: "Caveat" },
  not_observable: { tone: "red", label: "Not observable" },
  declined: { tone: "grey", label: "Declined" },
};

export interface AcceptanceSummary {
  total: number;
  passed: number;
  failed: AcceptanceCheck[];
  rounds: number;
  allPassed: boolean;
}

export function acceptanceSummary(plan: SitePlan): AcceptanceSummary {
  const { checks, rounds } = plan.acceptance;
  const failed = checks.filter((c) => !c.passed);
  return {
    total: checks.length,
    passed: checks.length - failed.length,
    failed,
    rounds,
    allPassed: failed.length === 0,
  };
}

/** One line per rule in plain words: what it watches, how serious, what happens. */
export function ruleSentence(rule: Rule): string {
  const what = rule.source.text?.trim() || rule.id;
  const actions = rule.actions.map((a) => (a.role ? `${a.kind} ${a.role}` : a.kind)).join(", ");
  const cited = rule.source.standard.length ? ` (${rule.source.standard.join("; ")})` : "";
  return `${what}${cited}: ${rule.severity} severity, then ${actions || "log"}.`;
}

/** The plan as YAML, the same shape the planner saves to plans/<id>.plan.yaml. */
export function planYaml(plan: SitePlan): string {
  return stringify(plan, { sortMapEntries: false, lineWidth: 100 });
}

export interface PlanDiff {
  added: Rule[];
  removed: Rule[];
  changed: { before: Rule; after: Rule }[];
  settings: { field: string; before: unknown; after: unknown }[];
}

const SETTING_FIELDS = [
  "privacy_mode",
  "retention_days",
  "languages",
  "protected_two_key",
] as const;

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** What changes between the approved plan and a proposed version (rules by id, plus settings). */
export function diffPlans(before: SitePlan, after: SitePlan): PlanDiff {
  const oldRules = new Map(before.rules.map((r) => [r.id, r]));
  const newRules = new Map(after.rules.map((r) => [r.id, r]));
  const changed: PlanDiff["changed"] = [];
  for (const [id, rule] of newRules) {
    const old = oldRules.get(id);
    if (old && !same(old, rule)) changed.push({ before: old, after: rule });
  }
  return {
    added: after.rules.filter((r) => !oldRules.has(r.id)),
    removed: before.rules.filter((r) => !newRules.has(r.id)),
    changed,
    settings: SETTING_FIELDS.filter((f) => !same(before[f], after[f])).map((field) => ({
      field,
      before: before[field],
      after: after[field],
    })),
  };
}

export function isEmptyDiff(diff: PlanDiff): boolean {
  return (
    !diff.added.length && !diff.removed.length && !diff.changed.length && !diff.settings.length
  );
}

/** The fixed Site Planner pipeline (task book P2-06), in order. */
export const PLANNER_STEPS = [
  "understand",
  "survey",
  "probe",
  "gauge",
  "ask",
  "compile",
  "lint",
  "accept",
  "audit",
  "prove",
] as const;

export type PlannerStepName = (typeof PLANNER_STEPS)[number];
export type StepStatus = "pending" | "running" | "done" | "failed";

export interface PlannerStepFrame {
  plan_id: string;
  step: string;
  status: Exclude<StepStatus, "pending">;
  detail?: string;
}
