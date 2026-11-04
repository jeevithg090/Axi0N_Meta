import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { goldenRun } from "@/fixtures/golden";

import { WarehouseJourney, warehouseJourney } from "./WarehouseJourney";

afterEach(cleanup);

describe("WarehouseJourney", () => {
  it("tells the golden run in the same seven steps as the other worlds", () => {
    const run = goldenRun("worker_passes_4m")!;
    const steps = warehouseJourney(run);
    expect(steps.map((s) => s.name)).toEqual(["Describe", "Plan", "Prove", "Watch", "Verify", "Act", "Evidence"]);
    const text = steps.map((s) => String(s.text)).join(" ");
    expect(text).toContain(run.steps.plan.request!);
    expect(text).toContain(run.steps.rule_ci[0]!.summary);
    expect(text).toContain(`${run.steps.evidence.n} records`);
  });

  it("is labelled REPLAY", () => {
    render(<WarehouseJourney />);
    const list = screen.getByRole("list", { name: "The forklift rule, step by step" });
    expect(within(list).getAllByRole("listitem").filter((li) => li.parentElement === list)).toHaveLength(7);
    expect(screen.getByTestId("live-badge").textContent).toContain("REPLAY");
  });
});
