// The UI's single state, folded from WebSocket frames (event → state). Pure, so it is unit-tested.

import type {
  ActionRecord,
  Badge,
  CandidateEvent,
  CoverageChanged,
  Frame,
  ModeChanged,
  Mode,
  VerdictNew,
  VerifierVerdict,
} from "./events";
import type { Alert, RuleCIReport, StationCoach, StationProgress, SurpriseEvent } from "./ops";
import type { PlannerStepFrame, SitePlan, StepStatus } from "./plan";
import { emptyRelay, reduceRelay, type RelayState } from "./relay";

export const MAX_EVENTS = 50;

export type Connection = "connecting" | "open" | "closed";

export interface EventView {
  candidate: CandidateEvent;
  verdicts: VerifierVerdict[];
  actions: ActionRecord[];
}

/** A plan as the UI knows it: the newest version, and the version the owner last approved. */
export interface PlanEntry {
  latest: SitePlan;
  approved: SitePlan | null;
}

export interface PlannerProgress {
  planId: string | null;
  steps: Record<string, { status: StepStatus; detail: string }>;
}

export interface UiState {
  connection: Connection;
  mode: Mode | null;
  badge: Badge | null;
  relay: RelayState;
  events: EventView[]; // newest first
  coverage: Record<string, string>; // camera_id → state
  plans: Record<string, PlanEntry>;
  planner: PlannerProgress;
  alerts: Alert[]; // newest first
  ci: Record<string, RuleCIReport>; // rule_id → latest report
  surprises: SurpriseEvent[]; // newest first
  station: Record<string, { coach?: StationCoach; progress?: StationProgress }>; // camera_id →
}

export const initialState: UiState = {
  connection: "connecting",
  mode: null,
  badge: null,
  relay: emptyRelay,
  events: [],
  coverage: {},
  plans: {},
  planner: { planId: null, steps: {} },
  alerts: [],
  ci: {},
  surprises: [],
  station: {},
};

export type Action =
  | { type: "frame"; frame: Frame }
  | { type: "connection"; connection: Connection }
  | { type: "mode"; mode: ModeChanged }
  | { type: "plan-loaded"; plan: SitePlan; approved: boolean }
  | { type: "plan-approved"; planId: string }
  | { type: "reset" }
  | { type: "alerts-loaded"; alerts: Alert[] }
  | { type: "alert-status"; eventId: string; status: Alert["status"] };

function upsertAlert(alerts: Alert[], alert: Alert): Alert[] {
  const rest = alerts.filter((a) => a.alert_id !== alert.alert_id);
  return [alert, ...rest].slice(0, MAX_EVENTS);
}

function withStation(
  state: UiState,
  cameraId: string,
  patch: { coach?: StationCoach; progress?: StationProgress },
): UiState {
  return {
    ...state,
    station: { ...state.station, [cameraId]: { ...state.station[cameraId], ...patch } },
  };
}

/** The newer of two versions of one plan (a reload must not undo a `plan.updated`). */
function newer(a: SitePlan | null | undefined, b: SitePlan): SitePlan {
  return a && a.version > b.version ? a : b;
}

function withPlan(state: UiState, plan: SitePlan, approved: boolean): UiState {
  const previous = state.plans[plan.plan_id];
  const entry: PlanEntry = {
    latest: newer(previous?.latest, plan),
    approved: approved ? newer(previous?.approved, plan) : (previous?.approved ?? null),
  };
  return { ...state, plans: { ...state.plans, [plan.plan_id]: entry } };
}

function reducePlannerStep(state: UiState, frame: PlannerStepFrame): UiState {
  const fresh = state.planner.planId !== frame.plan_id;
  const steps = fresh ? {} : state.planner.steps;
  return {
    ...state,
    planner: {
      planId: frame.plan_id,
      steps: { ...steps, [frame.step]: { status: frame.status, detail: frame.detail ?? "" } },
    },
  };
}

function updateEvent(
  events: EventView[],
  eventId: string,
  update: (view: EventView) => EventView,
): EventView[] {
  return events.map((view) => (view.candidate.event_id === eventId ? update(view) : view));
}

function reduceFrame(state: UiState, frame: Frame): UiState {
  switch (frame.topic) {
    case "mode.changed": {
      const payload = frame.payload as ModeChanged;
      return { ...state, mode: payload.mode, badge: payload.badge };
    }
    case "relay.model_used":
      return { ...state, relay: reduceRelay(state.relay, frame.payload as RelayPayload) };
    case "candidate.new": {
      const candidate = frame.payload as CandidateEvent;
      if (state.events.some((v) => v.candidate.event_id === candidate.event_id)) return state;
      const view: EventView = { candidate, verdicts: [], actions: [] };
      return { ...state, events: [view, ...state.events].slice(0, MAX_EVENTS) };
    }
    case "verdict.new": {
      const { event_id, verdict } = frame.payload as VerdictNew;
      return {
        ...state,
        events: updateEvent(state.events, event_id, (v) => ({
          ...v,
          verdicts: [...v.verdicts, verdict],
        })),
      };
    }
    case "action.done": {
      const { event_id, action } = frame.payload as { event_id: string; action: ActionRecord };
      return {
        ...state,
        events: updateEvent(state.events, event_id, (v) => ({
          ...v,
          actions: [...v.actions, action],
        })),
      };
    }
    case "alert.new":
      return { ...state, alerts: upsertAlert(state.alerts, frame.payload as Alert) };
    case "station.coach": {
      const coach = frame.payload as StationCoach;
      return withStation(state, coach.camera_id, { coach });
    }
    case "station.progress": {
      const progress = frame.payload as StationProgress;
      return withStation(state, progress.camera_id, { progress });
    }
    case "ci.result": {
      const { rule_id, report } = frame.payload as { rule_id: string; report: RuleCIReport };
      return { ...state, ci: { ...state.ci, [rule_id]: report } };
    }
    case "surprise.new": {
      const event = frame.payload as SurpriseEvent;
      const rest = state.surprises.filter((s) => s.event_id !== event.event_id);
      return { ...state, surprises: [event, ...rest].slice(0, MAX_EVENTS) };
    }
    case "plan.updated":
      return withPlan(state, frame.payload as SitePlan, false);
    case "planner.step":
      return reducePlannerStep(state, frame.payload as PlannerStepFrame);
    case "coverage.changed": {
      const { camera_id, state: coverage } = frame.payload as CoverageChanged;
      return { ...state, coverage: { ...state.coverage, [camera_id]: coverage } };
    }
    default:
      return state;
  }
}

type RelayPayload = Parameters<typeof reduceRelay>[1];

export function reducer(state: UiState, action: Action): UiState {
  switch (action.type) {
    case "frame":
      return reduceFrame(state, action.frame);
    case "connection":
      return reduceConnection(state, action.connection);
    case "mode":
      return { ...state, mode: action.mode.mode, badge: action.mode.badge };
    case "plan-loaded":
      return withPlan(state, action.plan, action.approved);
    case "alerts-loaded":
      return { ...state, alerts: action.alerts.reduceRight(upsertAlert, state.alerts) };
    case "alert-status":
      return {
        ...state,
        alerts: state.alerts.map((a) =>
          a.event_id === action.eventId ? { ...a, status: action.status } : a,
        ),
      };
    case "plan-approved": {
      const entry = state.plans[action.planId];
      return entry ? withPlan(state, entry.latest, true) : state;
    }
    case "reset":
      return initialState;
  }
}

/**
 * Track the socket. Once it drops, the last mode is forgotten (it may change while we cannot
 * hear `mode.changed`) and the badge stays OFFLINE through reconnect attempts until a socket is
 * open again and `GET /mode` has answered.
 */
function reduceConnection(state: UiState, connection: Connection): UiState {
  if (connection === "closed") return { ...state, connection, mode: null, badge: null };
  if (connection === "connecting" && state.connection === "closed") return state;
  return { ...state, connection };
}

/** True when the camera's rules must show BLIND (never "compliant"). */
export function isBlind(state: UiState, cameraId: string): boolean {
  return (state.coverage[cameraId] ?? "").toUpperCase() === "BLIND";
}

/** One card per (rule, camera) seen, newest event first. */
export interface RuleCardView {
  ruleId: string;
  cameraId: string;
  severity: CandidateEvent["severity"];
  latest: EventView;
  count: number;
  blind: boolean;
}

export function ruleCards(state: UiState): RuleCardView[] {
  const cards = new Map<string, RuleCardView>();
  for (const view of state.events) {
    const { rule_id, camera_id, severity } = view.candidate;
    const key = `${rule_id}@${camera_id}`;
    const card = cards.get(key);
    if (card) {
      card.count += 1;
    } else {
      cards.set(key, {
        ruleId: rule_id,
        cameraId: camera_id,
        severity,
        latest: view,
        count: 1,
        blind: isBlind(state, camera_id),
      });
    }
  }
  return [...cards.values()];
}

/** Cameras whose coverage is BLIND, sorted: none of their rules is being checked. */
export function blindCameras(state: UiState): string[] {
  return Object.keys(state.coverage)
    .filter((cameraId) => isBlind(state, cameraId))
    .sort();
}

/** What the badge shows: LIVE/REPLAY from the server, OFFLINE when the socket is down. */
export function badgeLabel(state: UiState): "LIVE" | "REPLAY" | "OFFLINE" | "…" {
  if (state.connection === "closed") return "OFFLINE";
  return state.badge ?? "…";
}

/** The monitor's empty-list line: never claims rules are running when the stream is down. */
export function noAlertsText(state: UiState): string {
  if (state.connection !== "open") return "Not connected to the Rulecam server: no live data.";
  if (blindCameras(state).length > 0) return "No alerts from the cameras that can see.";
  return "No alerts yet.";
}
