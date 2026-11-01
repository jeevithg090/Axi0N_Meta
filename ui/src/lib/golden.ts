// Pure helpers for the golden demo page: everything shown is derived from the recorded run.

import type { GoldenRun, GoldenVerification } from "@/fixtures/golden";

import type { ActionDone, CandidateEvent } from "./events";

export const VERDICT_TEXT: Record<string, string> = {
  violation: "violation",
  no_violation: "no violation",
  unsure: "unsure",
};

/** The run's candidates, from its replayed `candidate.new` frames. */
export function goldenCandidates(run: GoldenRun): CandidateEvent[] {
  return run.frames
    .filter((f) => f.topic === "candidate.new")
    .map((f) => f.payload as CandidateEvent);
}

/** The actions recorded for one candidate, in chain order. */
export function goldenActions(run: GoldenRun, eventId: string): ActionDone["action"][] {
  return run.frames
    .filter((f) => f.topic === "action.done")
    .map((f) => f.payload as ActionDone)
    .filter((a) => a.event_id === eventId)
    .map((a) => a.action);
}

/** What the policy did with one candidate, in words derived from the recorded decision. */
export function decisionText(v: GoldenVerification): string {
  const { decision } = v;
  const verdict = VERDICT_TEXT[v.tier1.verdict] ?? v.tier1.verdict;
  const first = decision.act_first
    ? `Severity ${v.severity.toUpperCase()} acts before verification; Glimmer then said ${verdict}`
    : `Glimmer said ${verdict} before anything ran`;
  const route =
    decision.route === "human_queue" ? "a person reviews it (human queue)" : "handled automatically";
  return `${first}, so ${route}: “${decision.reason}”.`;
}

/** "1 candidate", "2 alerts": a count with its noun. */
export function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}
