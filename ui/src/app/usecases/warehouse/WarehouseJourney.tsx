"use client";

// The forklift story as the same seven steps the other use cases tell, read from the 2 Nov golden
// run (worker_passes_4m). Shown when the live story cannot be (a visitor, or an API without the
// warehouse routes); labelled REPLAY.

import type { FlowStep } from "@/components/kit";
import { UseCaseJourney } from "@/components/usecases/UseCaseJourney";
import { goldenRun, type GoldenRun } from "@/fixtures/golden";
import { goldenActions } from "@/lib/golden";

/** Describe → Evidence for one golden run, each step said with the run's own values. */
export function warehouseJourney(run: GoldenRun): FlowStep[] {
  const { plan, rule_ci, verify, evidence } = run.steps;
  const survey = plan.planner_steps.find((s) => s.step === "survey");
  const doneSteps = plan.planner_steps.filter((s) => s.status === "done").length;
  const ci = rule_ci[0];
  const confirmed = verify.filter((v) => v.tier1.verdict === "violation");
  const actions = verify.flatMap((v) => goldenActions(run, v.event_id));
  return [
    { name: "Describe", text: `“${plan.request ?? "the request"}”`, models: ["Muse Spark 1.3"] },
    {
      name: "Plan",
      text: `${doneSteps} of ${plan.planner_steps.length} planner steps done${survey?.detail ? `; ${survey.detail}` : ""}.`,
      models: ["Muse Spark 1.3", "SAM 3.1"],
    },
    {
      name: "Prove",
      text: ci ? `Rule CI ${ci.ci_green ? "green" : "red"}: ${ci.summary}.` : "No Rule CI result recorded.",
      models: [{ model: "Rule CI", meta: false }],
    },
    {
      name: "Watch",
      text: `${verify.length} candidate${verify.length === 1 ? "" : "s"} on ${run.camera_id}, closest ${Math.min(
        ...verify.map((v) => v.measurement?.value ?? Number.POSITIVE_INFINITY),
      ).toFixed(2)} m.`,
      models: [{ model: "SAM 3.1", job: "tracked and measured" }],
    },
    {
      name: "Verify",
      text: `Glimmer confirmed ${confirmed.length} of ${verify.length} on the strips.`,
      models: ["Muse Glimmer 30B"],
    },
    {
      name: "Act",
      text: actions.length ? `${actions[0]!.detail} (${actions.length} action${actions.length === 1 ? "" : "s"}).` : "No action recorded.",
    },
    {
      name: "Evidence",
      text: `${evidence.ok ? "Chain intact" : "Chain broken"}: ${evidence.n} records, daily root ${evidence.root.slice(0, 10)}….`,
    },
  ];
}

export function WarehouseJourney() {
  const run = goldenRun("worker_passes_4m");
  if (!run) return null;
  return (
    <UseCaseJourney
      id="wh-journey"
      title="One forklift rule, step by step"
      badge={{ mode: "replay", detail: `recorded ${run.recorded_at.slice(0, 10)}` }}
      steps={warehouseJourney(run)}
      label="The forklift rule, step by step"
      columns={4}
      current={7}
    />
  );
}
