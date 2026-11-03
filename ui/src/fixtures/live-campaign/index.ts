// The 1 Nov 2026 live test campaign, run 2 (prototype mode, on the deployed stack): the records of
// T02, T03 and T04, copied verbatim from reports/eval/2026-11-01-live-test-campaign.json, plus
// their clip manifests (eval/clips/<folder>/clip.yaml) and one figure from
// reports/eval/2026-11-02-serverless-load.json. A generator copied them; nothing here is typed by
// hand. live-campaign.test.ts re-derives the same projection from the result files and fails if
// they drift.
//
// This is a RECORDED live run. It is neither the golden REPLAY (`?demo=1`) nor live now.

import run2 from "./run2.json";
import load from "./serverless-load.json";

/** The repository, for links to the result files. */
export const REPO_URL = "https://github.com/jeevithg090/Axi0N_Meta";

/** A repository file on GitHub (main). */
export function repoFile(path: string): string {
  return `${REPO_URL}/blob/main/${path}`;
}

export interface CampaignVerdict {
  verdict: string;
  score: number;
  reason: string;
  latency_ms: number;
  tier: string;
  model_version: string;
  at: string;
}

export interface CampaignEvent {
  event_id: string;
  rule_id: string;
  ts_start: string;
  ts_end: string;
  measurement: { value: number; unit: string; lo: number | null; hi: number | null } | null;
  calibration: string;
  cal_err: number;
  verdict: CampaignVerdict;
  decision: { alert: boolean; route: string; reason: string };
  event_to_candidate_s: number;
  event_to_decision_s: number;
  actions: string[];
}

/** A Rule IR predicate node, as recorded (only the fields this page reads are typed). */
export interface CampaignPredicate {
  op: string;
  of?: CampaignPredicate[];
  lt?: number | null;
  gt?: number | null;
  [key: string]: unknown;
}

export interface CampaignTest {
  id: string;
  clip: string;
  request: string;
  camera_id: string;
  plan_id: string;
  plan_s: number;
  started_at: string;
  finished_at: string;
  back_translation: string;
  metrics: Record<string, number>;
  rules: { id: string; severity: string; predicate: CampaignPredicate }[];
  events: CampaignEvent[];
}

export interface CampaignClip {
  folder: string;
  description: string;
  license: string;
  duration_s: number;
  source_url: string;
}

export interface CampaignFile {
  source: string;
  tests: CampaignTest[];
  clips: Record<string, CampaignClip>;
}

export interface LoadFigure {
  source: string;
  scenario: number;
  burst: number;
  state: string;
  usd_per_alert: number;
}

export const CAMPAIGN = run2 as CampaignFile;

/** GPU cost per alert check with 20 checks at once on warm serverless GPUs (load test, 2 Nov). */
export const SERVERLESS_LOAD = load as LoadFigure;

/** The human-readable report next to the JSON (inputs, outputs and latencies per test). */
export const CAMPAIGN_REPORT_MD = "reports/eval/2026-11-01-live-test-campaign.md";

/** One test of the campaign by id; throws if the fixture lacks it (a generator bug). */
export function campaignTest(id: string): CampaignTest {
  const test = CAMPAIGN.tests.find((t) => t.id === id);
  if (!test) throw new Error(`campaign test ${id} is not in the fixture`);
  return test;
}

/** The test's single event (each of T02–T04 raised exactly one candidate). */
export function firstEvent(test: CampaignTest): CampaignEvent {
  const event = test.events[0];
  if (!event) throw new Error(`campaign test ${test.id} has no event`);
  return event;
}

/** The `lt` bound of the first `distance_m` node in a predicate, or `null`. */
export function distanceLimit(node: CampaignPredicate): number | null {
  if (node.op === "distance_m") return typeof node.lt === "number" ? node.lt : null;
  for (const child of node.of ?? []) {
    const found = distanceLimit(child);
    if (found !== null) return found;
  }
  return null;
}
