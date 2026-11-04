// Wire types of `/staff` (src/rulecam/staff/models.py and api.py).

export type Lang = "en" | "es" | "hi";
export type Source = "rule" | "crowd" | "shelf" | "review" | "drill";
export type ItemStatus = "open" | "acknowledged" | "on_my_way" | "done" | "false_alarm";
export type StaffAction = "acknowledge" | "on_my_way" | "done" | "false_alarm" | "escalate";
export type Stage =
  | "raised"
  | "verified"
  | "assigned"
  | "acknowledged"
  | "on_my_way"
  | "escalated"
  | "record"
  | "done"
  | "false_alarm";

export interface VerdictView {
  model: string;
  verdict: string;
  confidence: number | null;
  reason: string;
  late: boolean;
}

export interface TakenAction {
  action: StaffAction;
  role: string;
  ts: string;
  note: string;
  chain_hash: string | null;
}

export interface TimelineStep {
  stage: Stage;
  ts: string;
  label: string;
  role: string | null;
  after_s: number;
}

export interface StaffItem {
  item_id: string;
  source: Source;
  title: string;
  body: string;
  roles: string[];
  severity: string;
  camera_id: string | null;
  camera_name: string | null;
  rule_text: string | null;
  measurement: string | null;
  ts: string;
  status: ItemStatus;
  verdict: VerdictView | null;
  thumbnails: number;
  escalation_step: string | null;
  next_role: string | null;
  escalates_at: string | null;
  actions: TakenAction[];
  do_now: string[];
  announcements: { lang: string; text: string }[];
  sample: boolean;
  dry_run: boolean;
  timeline: TimelineStep[];
  ack_s: number | null;
  fix_s: number | null;
  retail_task_id?: string | null;
}

export interface StaffConfig {
  langs: Lang[];
  dry_run: boolean;
  ask: boolean;
  listen: boolean;
  shelf: boolean;
  speech: string[];
  near_miss_voice: boolean;
  push: boolean;
}

export interface RoleInfo {
  role: string;
  sources: string[];
}

export interface SpeechLine {
  lang: string;
  text: string;
  audio: string | null;
  engine: string | null;
  model: string | null;
}

export interface AskReply {
  heard: string;
  answer: string;
  lang: string;
  audio: string | null;
  engine: string | null;
  seconds: number;
  models: string[];
}

export interface ActionResult {
  item: StaffItem;
  did: string;
}

export interface Bucket {
  key: string;
  items: number;
  acknowledged: number;
  done: number;
  false_alarms: number;
  escalated: number;
  ack_p50_s: number | null;
  ack_p90_s: number | null;
  done_p50_s: number | null;
  phone_p50_ms: number | null;
  phone_p95_ms: number | null;
}

export interface StaffStats {
  since: string;
  timezone: string;
  shifts: Record<string, string>;
  by_role: Bucket[];
  by_shift: Bucket[];
}
