// Shapes of the frames `/ws` sends: {"topic": <bus topic>, "payload": <model as JSON>}.
// They mirror the Python models (rulecam.contracts, replay.cache.ModeChanged, clients.relay.ModelUsed,
// runtime.loop.VerdictNew / ActionDone). Only the fields the UI reads are typed.

export type Mode = "live" | "record" | "replay";
export type Badge = "LIVE" | "REPLAY";
export type Severity = "low" | "medium" | "high";

export interface ModeChanged {
  mode: Mode;
  badge: Badge;
}

export interface ModelUsed {
  model: string;
  segment: string;
  latency_ms: number;
  camera_id?: string | null;
  attempts?: number;
}

export interface Measurement {
  value: number;
  lo?: number | null;
  hi?: number | null;
  unit: string;
}

export interface CandidateEvent {
  event_id: string;
  rule_id: string;
  camera_id: string;
  ts_start: string;
  ts_end: string;
  severity: Severity;
  measurement?: Measurement | null;
  predicate_trace: Record<string, number | string | boolean>;
  strip_paths: string[];
}

export interface VerifierVerdict {
  tier: "glimmer" | "spark_open";
  verdict: "violation" | "no_violation" | "unsure";
  score: number;
  reason: string;
  reasoning?: string | null;
  scene_text: string;
  tamper: boolean;
  latency_ms: number;
  model_version: string;
}

export interface VerdictNew {
  event_id: string;
  verdict: VerifierVerdict;
}

export interface ActionRecord {
  kind: string;
  role?: string | null;
  gate: "auto" | "human";
  ts: string;
  ok: boolean;
  detail: string;
}

export interface ActionDone {
  event_id: string;
  action: ActionRecord;
}

export interface CoverageChanged {
  camera_id: string;
  state: string; // "OK" | "BLIND" | … from perception.coverage
}

export type Frame =
  | { topic: "mode.changed"; payload: ModeChanged }
  | { topic: "relay.model_used"; payload: ModelUsed }
  | { topic: "candidate.new"; payload: CandidateEvent }
  | { topic: "verdict.new"; payload: VerdictNew }
  | { topic: "action.done"; payload: ActionDone }
  | { topic: "coverage.changed"; payload: CoverageChanged }
  | { topic: string; payload: unknown };

/** Parse a raw WebSocket message; returns null for anything that is not a frame. */
export function parseFrame(data: string): Frame | null {
  try {
    const value: unknown = JSON.parse(data);
    if (
      typeof value === "object" &&
      value !== null &&
      "topic" in value &&
      typeof (value as { topic: unknown }).topic === "string" &&
      "payload" in value
    ) {
      return value as Frame;
    }
  } catch {
    return null;
  }
  return null;
}
