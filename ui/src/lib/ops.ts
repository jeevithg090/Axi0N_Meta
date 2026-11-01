// Shapes for the operations screens: alerts (P3-06), the station display, Rule CI (P2-08/09)
// and Surprise Lens (P4-03). Only the fields the UI reads are typed, except Rule CI (exact).

export interface Alert {
  alert_id: string;
  event_id: string | null;
  rule_id?: string | null;
  role: string;
  title: string;
  body: string;
  clip_path: string | null;
  buttons: string[];
  ts: string;
  status: "open" | "acknowledged" | "false_alarm";
}

export interface StationCoach {
  event_id: string;
  rule_id: string;
  camera_id: string;
  text: string;
  lang?: string | null;
}

/** `station.progress`: a timed requirement in progress at a station (e.g. a hand wash). */
export interface StationProgress {
  camera_id: string;
  rule_id: string;
  elapsed_s: number;
  required_s: number;
  state: "counting" | "passed" | "failed";
}

// Rule CI, exactly as `ci.result` is serialised: `CIResult` in src/rulecam/rule_ci/models.py
// (`model_dump(mode="json")`, so tuples are arrays and properties such as `fooled` are absent).

/** `rule_ci.scenes.SceneResult`. */
export interface SceneResult {
  name: string;
  kind: "positive" | "negative" | "edge";
  passed: boolean;
  renders: number;
  image: string;
  missing: string[];
  unexpected: string[];
}

/** `rule_ci.scenes.DetectabilityReport`. */
export interface DetectabilityReport {
  results: SceneResult[];
}

/** `rule_ci.lookalikes.FixProposal`. */
export interface FixProposal {
  concept: string;
  kind: "negative_concept" | "concept_prompt";
  text: string;
}

/** `rule_ci.lookalikes.LookalikeFinding`. */
export interface LookalikeFinding {
  concept: string;
  lookalike: string;
  image: string;
  perception_fooled: boolean;
  tier1_fooled: boolean;
  regression: boolean;
  fix: FixProposal | null;
}

/** `rule_ci.lookalikes.LookalikeReport`. */
export interface LookalikeReport {
  findings: LookalikeFinding[];
}

/** `rule_ci.injection.InjectionResult`. */
export interface InjectionResult {
  variant: string;
  text: string;
  image: string;
  alert: boolean;
  route: string;
  tamper: boolean;
}

/** `rule_ci.injection.InjectionReport`. */
export interface InjectionReport {
  results: InjectionResult[];
}

/** `rule_ci.forecast.AlertBudget`. */
export interface AlertBudget {
  alerts_per_shift: number;
  shift_hours: number;
}

/** `rule_ci.forecast.AlertForecast`. */
export interface AlertForecast {
  rule_id: string;
  cameras: string[];
  hours: number;
  candidates: number;
  backtestable: "yes" | "partial" | "no";
  per_shift: number | null;
  budget: AlertBudget;
}

/** `rule_ci.red_team.RedTeamReport` (Rule CI part 2, P2-09). */
export interface RedTeamReport {
  scenes: DetectabilityReport | null;
  lookalikes: LookalikeReport | null;
  injection: InjectionReport | null;
  forecast: AlertForecast | null;
  skipped: string[];
}

/** `rule_ci.models.RuleCIReport` (P2-08). */
export interface RuleCIReport {
  rule_id: string;
  traces_passed: number;
  traces_total: number;
  mutants_killed: number;
  mutants_total: number;
  failures: string[];
  nonvacuous: boolean;
  justification: string | null;
  red_team: RedTeamReport | null;
}

/** The `ci.result` payload. `ci_green` and `summary` are the backend's verdict: never recompute. */
export interface CIResult {
  rule_id: string;
  report: RuleCIReport;
  ci_green: boolean;
  summary: string;
}

/** A lookalike fooled the rule (`LookalikeFinding.fooled`, a property not serialised). */
export function lookalikeFooled(finding: LookalikeFinding): boolean {
  return finding.perception_fooled || finding.tier1_fooled;
}

export interface SurpriseWindow {
  camera_id: string;
  window_start: string;
  score: number;
  z: number;
}

export interface SurpriseEvent {
  event_id: string;
  camera_id: string;
  windows: SurpriseWindow[];
  heatmap_path: string;
  labels: string[];
  peak_xy: [number, number];
}

export type CountdownState = "idle" | "counting" | "passed" | "failed";

export interface Countdown {
  state: CountdownState;
  elapsed: number;
  required: number;
}

/** What the station shows: live progress when the engine streams it, else the last failure. */
export function stationCountdown(
  progress: StationProgress | undefined,
  lastFail: { value: number; required: number } | undefined,
): Countdown {
  if (progress) {
    return { state: progress.state, elapsed: progress.elapsed_s, required: progress.required_s };
  }
  if (lastFail) return { state: "failed", elapsed: lastFail.value, required: lastFail.required };
  return { state: "idle", elapsed: 0, required: 0 };
}

/** Points for the surprise curve (z over time), scaled into a width × height box. */
export function curvePoints(
  windows: SurpriseWindow[],
  width: number,
  height: number,
  zMax = 6,
): string {
  if (windows.length === 0) return "";
  const step = windows.length > 1 ? width / (windows.length - 1) : 0;
  return windows
    .map((w, i) => {
      const z = Math.max(0, Math.min(zMax, w.z));
      return `${(i * step).toFixed(1)},${(height - (z / zMax) * height).toFixed(1)}`;
    })
    .join(" ");
}
