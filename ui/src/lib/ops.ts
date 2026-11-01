// Shapes for the operations screens: alerts (P3-06), the station display, Rule CI (P2-08/09)
// and Surprise Lens (P4-03). Only the fields the UI reads are typed.

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

export interface LookalikeResult {
  name: string;
  fooled: boolean;
  fix?: string | null;
}

/** `ci.result` report: P2-08's boundary/mutation numbers plus P2-09's sections when present. */
export interface RuleCIReport {
  rule_id: string;
  traces_passed: number;
  traces_total: number;
  mutants_killed: number;
  mutants_total: number;
  failures: string[];
  scenes?: { passed: number; total: number } | null;
  lookalikes?: LookalikeResult[] | null;
  injection?: { variants: number; still_alerted: number; tamper_flagged: number } | null;
  forecast?: { alerts_per_shift: number; budget_per_shift: number } | null;
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

export const CI_MUTANT_BAR = 0.9;

export interface CiVerdict {
  green: boolean;
  boundary: string;
  mutations: string;
  lookalikes: string | null;
  fooled: LookalikeResult[];
}

/** Summarise a report the way the rule card shows it ("boundary 3/3, mutations 5/5"). */
export function ciVerdict(report: RuleCIReport): CiVerdict {
  const tracesOk = report.traces_passed === report.traces_total;
  const mutantShare = report.mutants_total ? report.mutants_killed / report.mutants_total : 1;
  const fooled = (report.lookalikes ?? []).filter((l) => l.fooled);
  const lookalikes = report.lookalikes
    ? `${report.lookalikes.length} lookalikes, fooled ${fooled.length}`
    : null;
  return {
    green: tracesOk && mutantShare >= CI_MUTANT_BAR,
    boundary: `boundary ${report.traces_passed}/${report.traces_total}`,
    mutations: `mutations ${report.mutants_killed}/${report.mutants_total}`,
    lookalikes,
    fooled,
  };
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
