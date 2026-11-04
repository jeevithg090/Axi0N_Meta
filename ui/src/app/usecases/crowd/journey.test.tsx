import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CrowdJourney, crowdJourney } from "./CrowdJourney";
import recorded from "./recorded-run.json";

afterEach(cleanup);

describe("CrowdJourney", () => {
  it("tells the recorded run in seven steps, each with the run's own values", () => {
    const steps = crowdJourney();
    expect(steps.map((s) => s.name)).toEqual(["Describe", "Plan", "Prove", "Watch", "Verify", "Act", "Evidence"]);
    const text = steps.map((s) => String(s.text)).join(" ");
    const gate = recorded.report.zones.find((z) => z.zone.startsWith("gate"))!;
    expect(text).toContain(`Peak ${gate.peak_density_ppm2.toFixed(1)} people/m²`);
    expect(text).toContain(`${gate.peak_people} people`);
    expect(text).toContain(`ratio ${recorded.report.truth[0]!.count_ratio.toFixed(3)}`);
    expect(text).toContain(`${recorded.report.evidence.records.length} records`);
    expect(text).toContain(recorded.alert.chain_hash.slice(0, 10));
  });

  it("is labelled as a recorded run, never as live", () => {
    render(<CrowdJourney />);
    const list = screen.getByRole("list", { name: "The crowd warning, step by step" });
    expect(within(list).getAllByRole("listitem").filter((li) => li.parentElement === list)).toHaveLength(7);
    expect(screen.getByTestId("live-badge").textContent).toContain("REPLAY");
  });
});
