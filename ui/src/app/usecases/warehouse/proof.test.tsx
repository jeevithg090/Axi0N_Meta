import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { goldenRun } from "@/fixtures/golden";
import { campaignTest, firstEvent } from "@/fixtures/live-campaign";

import { WarehouseProof } from "./WarehouseProof";

afterEach(cleanup);

describe("WarehouseProof", () => {
  it("leads with the recorded measurement and Glimmer's verdict, labelled as recorded", () => {
    render(<WarehouseProof />);
    const proof = screen.getByTestId("warehouse-proof");
    const t02 = firstEvent(campaignTest("T02"));
    expect(proof.textContent).toContain(t02.measurement!.value.toFixed(2));
    expect(proof.textContent).toContain(t02.verdict.reason);
    expect(within(proof).getAllByTestId("live-badge")[0]!.textContent).toContain("REPLAY");
  });

  it("shows the verified alert and the operator exclusion with their strips and verify links", () => {
    render(<WarehouseProof />);
    const cards = screen.getAllByTestId("evidence-card");
    expect(cards).toHaveLength(2);
    const passes = goldenRun("worker_passes_4m")!;
    const driver = goldenRun("driver_only_4m")!;
    const [alert, excluded] = cards as [HTMLElement, HTMLElement];
    expect(within(alert).getAllByRole("img").length).toBeGreaterThan(0);
    expect(within(alert).getByTestId("verdict-pill").getAttribute("data-verdict")).toBe("yes");
    expect(within(alert).getByRole("link", { name: "Verify" }).getAttribute("href")).toBe(passes.verify_url);
    expect(within(excluded).getByTestId("verdict-pill").getAttribute("data-verdict")).toBe("no");
    expect(within(excluded).getByRole("link", { name: "Verify" }).getAttribute("href")).toBe(driver.verify_url);
    expect(excluded.textContent).toContain("operator");
  });
});
