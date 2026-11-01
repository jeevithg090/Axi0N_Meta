import { describe, expect, it } from "vitest";

import { demoAlerts, demoCiResults, demoOpsFrames, demoSurprise } from "@/fixtures/demo-ops";

import { curvePoints, lookalikeFooled, stationCountdown } from "./ops";
import { initialState, reducer, type UiState } from "./store";

const fold = (frames: typeof demoOpsFrames, start: UiState = initialState): UiState =>
  frames.reduce((s, frame) => reducer(s, { type: "frame", frame }), start);

describe("ops helpers", () => {
  it("reads lookalike findings in the serialised Rule CI shape", () => {
    const findings = demoCiResults[1]!.report.red_team!.lookalikes!.findings;
    expect(findings).toHaveLength(18);
    expect(findings.filter(lookalikeFooled).map((f) => f.lookalike)).toEqual(["backpack strap"]);
    expect(lookalikeFooled({ ...findings[0]!, tier1_fooled: true })).toBe(true);
  });

  it("drives the station countdown from live progress, else the last failure", () => {
    expect(stationCountdown(undefined, undefined)).toEqual({
      state: "idle",
      elapsed: 0,
      required: 0,
    });
    expect(stationCountdown(undefined, { value: 8, required: 20 })).toEqual({
      state: "failed",
      elapsed: 8,
      required: 20,
    });
    const live = {
      camera_id: "cam1",
      rule_id: "w",
      elapsed_s: 12.4,
      required_s: 20,
      state: "counting" as const,
    };
    expect(stationCountdown(live, { value: 8, required: 20 })).toEqual({
      state: "counting",
      elapsed: 12.4,
      required: 20,
    });
  });

  it("scales the surprise curve into the box and clamps z", () => {
    expect(curvePoints([], 100, 50)).toBe("");
    const points = curvePoints(demoSurprise.windows, 110, 60).split(" ");
    expect(points).toHaveLength(12);
    expect(points[0]).toBe("0.0,57.0");
    expect(points[11]?.startsWith("110.0,")).toBe(true);
    const clamped = curvePoints([{ ...demoSurprise.windows[0]!, z: 99 }], 10, 60);
    expect(clamped).toBe("0.0,0.0");
  });
});

describe("ops state", () => {
  it("folds alerts, station coaching, CI results and surprises", () => {
    const state = fold(demoOpsFrames);
    expect(state.alerts.map((a) => a.alert_id)).toEqual(["al-1"]);
    expect(state.station.cam1?.coach?.text).toContain("Lávate");
    expect(Object.keys(state.ci)).toEqual(["wash-before-line", "harness-at-height"]);
    expect(state.ci["harness-at-height"]?.ci_green).toBe(false);
    expect(state.surprises[0]?.labels).toEqual(["cardboard box", "shelf"]);
  });

  it("updates alert status by event and de-duplicates by alert id", () => {
    let state = reducer(initialState, { type: "alerts-loaded", alerts: demoAlerts });
    state = fold([{ topic: "alert.new", payload: demoAlerts[0]! }], state);
    expect(state.alerts).toHaveLength(1);
    state = reducer(state, {
      type: "alert-status",
      eventId: "ev-uc3-0007",
      status: "acknowledged",
    });
    expect(state.alerts[0]?.status).toBe("acknowledged");
  });

  it("keeps live station progress next to the coaching line", () => {
    const state = fold([
      ...demoOpsFrames,
      {
        topic: "station.progress",
        payload: {
          camera_id: "cam1",
          rule_id: "w",
          elapsed_s: 22,
          required_s: 20,
          state: "passed",
        },
      },
    ]);
    expect(state.station.cam1?.progress?.state).toBe("passed");
    expect(state.station.cam1?.coach).toBeDefined();
  });
});
