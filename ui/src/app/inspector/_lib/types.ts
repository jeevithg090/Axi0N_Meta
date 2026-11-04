// Shapes of the proof portal API (src/rulecam/compliance/models.py).

export type Scalar = string | number | boolean | null;
export type Audience = "inspector" | "insurer" | "hq" | "worker_rep";

export const AUDIENCES: readonly Audience[] = ["inspector", "insurer", "hq", "worker_rep"];
export const AUDIENCE_NAMES: Record<Audience, string> = {
  inspector: "Inspector",
  insurer: "Insurer",
  hq: "Head office",
  worker_rep: "Crew representative",
};

export interface Shift {
  name: string;
  start_hour: number;
}

export interface Period {
  start: string;
  end: string;
  previous_start: string;
  previous_end: string;
  timezone: string;
  shifts: Shift[];
}

export interface MetricSpec {
  op: "count" | "median_s" | "p90_s" | "max_s" | "state_s" | "two_key_unapproved";
  where: Record<string, Scalar>;
  prefix: Record<string, string>;
  to: Record<string, Scalar>[];
  rule: string | null;
  camera: string | null;
  shift: string | null;
  state: string | null;
}

export interface Metric {
  id: string;
  section: string;
  label: string;
  unit: "count" | "seconds";
  spec: MetricSpec;
  value: number | null;
  previous: number | null;
  refs: string[];
  previous_refs: string[];
}

export interface Clause {
  ref: string;
  url: string;
  quote: string;
  checked: boolean;
  detail: string;
  source: "plan" | "muse-spark-1.3";
}

export interface RuleInForce {
  rule_id: string;
  text: string;
  severity: string;
  plan_ids: string[];
  approvals: string[];
  gate: string;
  gate_reasons: string[];
  ci_detail: string | null;
  active_s: number;
  standards: string[];
  clause: Clause | null;
  clause_note: string;
  distance_rule: boolean;
}

export interface DayProof {
  day: string;
  n: number;
  root: string;
  chain_ok: boolean;
  reason: string;
  committed_root: string | null;
  committed_n: number | null;
  window_first: number | null;
  window_last: number | null;
}

export interface DisclosedRecord {
  key: string;
  day: string;
  record: Record<string, unknown>;
  proof: [string, "L" | "R"][];
}

export interface Incident {
  event_id: string;
  key: string;
  rule_id: string;
  camera_id: string;
  severity: string;
  ts: string;
  verdict: string | null;
  route: string | null;
}

export interface Summary {
  audience: Audience;
  lang: string;
  template: string[];
  text: string[];
  model: string;
  dropped: number;
}

export interface Still {
  event_id: string;
  kind: "skeleton" | "masked";
  sha256: string;
  data_uri: string;
}

export interface ProofPack {
  format: string;
  pack_id: string;
  site: string;
  created_at: string;
  title: string;
  period: Period;
  audiences: Audience[];
  crew_lang: string;
  privacy_mode: string;
  rules: RuleInForce[];
  metrics: Metric[];
  incidents: Incident[];
  days: DayProof[];
  records: DisclosedRecord[];
  summaries: Summary[];
  stills: Still[];
  notes: string[];
  disclaimer: string;
}

export interface PackEntry {
  pack_id: string;
  status: "building" | "ready" | "failed";
  created_at: string;
  site: string;
  title: string;
  error: string | null;
  pack: ProofPack | null;
  pack_hash: string | null;
}

export interface ShareLink {
  pack_id: string;
  pack_hash: string;
  token: string;
  url: string;
  expires_at: string;
  qr: string;
}

export interface SharedPack {
  pack: ProofPack;
  pack_hash: string;
  expires_at: string;
}

export interface PackRequest {
  start: string;
  end: string;
  title?: string;
  audiences?: Audience[];
  crew_lang?: string;
  models?: boolean;
  ground_rules?: number;
}
