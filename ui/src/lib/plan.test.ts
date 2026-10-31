import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { demoPlannerSteps, demoPlanV1, demoPlanV2 } from "@/fixtures/demo-plan";

import { acceptanceSummary, diffPlans, isEmptyDiff, planYaml, ruleSentence } from "./plan";
import { initialState, reducer, type UiState } from "./store";

describe("plan helpers", () => {
  it("summarises the acceptance trace", () => {
    expect(acceptanceSummary(demoPlanV1)).toMatchObject({
      total: 27,
      passed: 27,
      rounds: 1,
      allPassed: true,
    });
    const v2 = acceptanceSummary(demoPlanV2);
    expect(v2).toMatchObject({ total: 30, passed: 29, allPassed: false });
    expect(v2.failed.map((c) => c.check)).toEqual(["ci_green"]);
  });

  it("diffs rules by id and the settings that matter", () => {
    const diff = diffPlans(demoPlanV1, demoPlanV2);
    expect(diff.added.map((r) => r.id)).toEqual(["spill-no-sign"]);
    expect(diff.removed).toEqual([]);
    expect(diff.changed.map((c) => c.after.id)).toEqual(["gloves-rte"]);
    expect(diff.settings).toEqual([{ field: "retention_days", before: 30, after: 14 }]);
    expect(isEmptyDiff(diffPlans(demoPlanV1, demoPlanV1))).toBe(true);
    const removed = diffPlans(demoPlanV2, demoPlanV1);
    expect(removed.removed.map((r) => r.id)).toEqual(["spill-no-sign"]);
  });

  it("renders YAML that round-trips to the same plan", () => {
    expect(parse(planYaml(demoPlanV1))).toEqual(demoPlanV1);
    expect(planYaml(demoPlanV1)).toContain("plan_id: main-st-kitchen");
  });

  it("says each rule in plain words with its citation and actions", () => {
    expect(ruleSentence(demoPlanV1.rules[0]!)).toBe(
      "Wash hands for 20 s before entering the line (Food Code 2026 §2-301.12; §2-103.11(G)): " +
        "medium severity, then coach_station, notify shift_lead.",
    );
  });
});

const run = (actions: Parameters<typeof reducer>[1][], start: UiState = initialState): UiState =>
  actions.reduce(reducer, start);

describe("plan and planner state", () => {
  it("tracks planner steps per plan and restarts for a new plan", () => {
    let state = run(
      demoPlannerSteps
        .slice(0, 3)
        .map((payload) => ({ type: "frame", frame: { topic: "planner.step", payload } })),
    );
    expect(state.planner.planId).toBe("main-st-kitchen");
    expect(Object.keys(state.planner.steps)).toEqual(["understand", "survey", "probe"]);
    state = reducer(state, {
      type: "frame",
      frame: {
        topic: "planner.step",
        payload: { plan_id: "yard", step: "understand", status: "running" },
      },
    });
    expect(state.planner).toEqual({
      planId: "yard",
      steps: { understand: { status: "running", detail: "" } },
    });
  });

  it("keeps the approved version while a new proposal arrives, until it is approved", () => {
    let state = run([{ type: "plan-loaded", plan: demoPlanV1, approved: true }]);
    state = reducer(state, {
      type: "frame",
      frame: { topic: "plan.updated", payload: demoPlanV2 },
    });
    expect(state.plans["main-st-kitchen"]?.latest.version).toBe(2);
    expect(state.plans["main-st-kitchen"]?.approved?.version).toBe(1);
    state = reducer(state, { type: "plan-approved", planId: "main-st-kitchen" });
    expect(state.plans["main-st-kitchen"]?.approved?.version).toBe(2);
    expect(reducer(state, { type: "plan-approved", planId: "nope" })).toBe(state);
  });

  it("never lets a reload of an older version undo a newer plan.updated", () => {
    // In the demo, v2 arrives on the stream; opening another screen reloads the recorded v1.
    let state = reducer(initialState, {
      type: "frame",
      frame: { topic: "plan.updated", payload: demoPlanV2 },
    });
    state = reducer(state, { type: "plan-loaded", plan: demoPlanV1, approved: true });
    expect(state.plans["main-st-kitchen"]?.latest.version).toBe(2);
    expect(state.plans["main-st-kitchen"]?.approved?.version).toBe(1);
    state = reducer(state, { type: "plan-approved", planId: "main-st-kitchen" });
    state = reducer(state, { type: "plan-loaded", plan: demoPlanV1, approved: true });
    expect(state.plans["main-st-kitchen"]?.approved?.version).toBe(2);
  });

  it("loads an unapproved plan with no approved version", () => {
    const state = run([{ type: "plan-loaded", plan: demoPlanV2, approved: false }]);
    expect(state.plans["main-st-kitchen"]?.approved).toBeNull();
  });
});
