import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { demoPlannerSteps, demoPlanV1, demoPlanV2 } from "@/fixtures/demo-plan";
import { diffPlans } from "@/lib/plan";

import { AcceptanceTrace } from "./AcceptanceTrace";
import { AuditFindings } from "./AuditFindings";
import { CameraTiles } from "./CameraTiles";
import { FeasibilityCard } from "./FeasibilityCard";
import { PlanDiffView } from "./PlanDiffView";
import { PlanView } from "./PlanView";
import { StepProgress } from "./StepProgress";

afterEach(cleanup);

describe("feasibility cards", () => {
  it("label every verdict in words as well as colour", () => {
    for (const v of demoPlanV1.feasibility) render(<FeasibilityCard verdict={v} />);
    const cards = screen.getAllByTestId("feasibility-card");
    expect(cards.map((c) => c.dataset.verdict)).toEqual([
      "observable",
      "caveat",
      "not_observable",
      "declined",
    ]);
    expect(cards.map((c) => c.querySelector(".pill")?.textContent)).toEqual([
      "Observable",
      "Caveat",
      "Not observable",
      "Declined",
    ]);
    expect(cards[2]?.textContent).toContain("I can't see a thermometer");
    expect(cards[2]?.textContent).toContain("Instead: Point a phone at the dial");
    expect(cards[1]?.textContent).toContain("Fix: Move cam 2");
    expect(cards[0]?.textContent).toContain("lower bound 86%");
    expect(cards[3]?.textContent).toContain("Why: Clinical inference");
  });
});

describe("plan screens", () => {
  it("shows step progress with a word per status", () => {
    const steps = Object.fromEntries(
      demoPlannerSteps
        .slice(0, 7)
        .map((s) => [s.step, { status: s.status, detail: s.detail ?? "" }]),
    );
    render(
      <StepProgress
        progress={{ planId: "p", steps: { ...steps, accept: { status: "running", detail: "" } } }}
      />,
    );
    const items = screen.getByTestId("step-progress").querySelectorAll("li");
    expect(items).toHaveLength(10);
    expect([...items].map((li) => li.dataset.status)).toEqual([
      "done",
      "done",
      "done",
      "done",
      "done",
      "done",
      "done",
      "running",
      "pending",
      "pending",
    ]);
    expect(items[7]?.textContent).toContain("(running)");
  });

  it("shows the acceptance trace with failing checks", () => {
    render(<AcceptanceTrace plan={demoPlanV1} />);
    expect(screen.getByTestId("acceptance-trace").textContent).toContain("27/27 checks");
    cleanup();
    render(<AcceptanceTrace plan={demoPlanV2} />);
    const trace = screen.getByTestId("acceptance-trace");
    expect(trace.textContent).toContain("29/30 checks");
    expect(trace.textContent).toContain("2 repair rounds");
    expect(trace.querySelectorAll("tbody tr")).toHaveLength(1);
  });

  it("toggles between plain language and YAML", () => {
    render(<PlanView plan={demoPlanV1} />);
    expect(screen.getByTestId("plan-plain").textContent).toContain("Wash hands for 20 s");
    fireEvent.click(screen.getByRole("button", { name: "YAML" }));
    expect(screen.getByTestId("plan-yaml").textContent).toContain("plan_id: main-st-kitchen");
    expect(screen.queryByTestId("plan-plain")).toBeNull();
  });

  it("lists the diff and wires approve / keep", () => {
    const onApprove = vi.fn();
    const onKeep = vi.fn();
    render(
      <PlanDiffView
        diff={diffPlans(demoPlanV1, demoPlanV2)}
        fromVersion={1}
        toVersion={2}
        busy={false}
        onApprove={onApprove}
        onKeep={onKeep}
      />,
    );
    const text = screen.getByTestId("plan-diff").textContent ?? "";
    expect(text).toContain("+ Added: Spill with no wet-floor sign");
    expect(text).toContain("~ Changed gloves-rte: v1 → v2");
    expect(text).toContain("~ retention_days: 30 → 14");
    fireEvent.click(screen.getByRole("button", { name: "Approve v2" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep v1" }));
    expect(onApprove).toHaveBeenCalledOnce();
    expect(onKeep).toHaveBeenCalledOnce();
  });

  it("shows audit findings with their reasoning reference", () => {
    render(<AuditFindings findings={demoPlanV1.audit} />);
    expect(screen.getByTestId("audit-findings").textContent).toContain("back-door sink");
    expect(screen.getByTestId("audit-findings").textContent).toContain("so_3f1a9c2b7d4e6a01");
    cleanup();
    render(<AuditFindings findings={[]} />);
    expect(screen.getByText("Spark Open found no gaps.")).toBeTruthy();
  });

  it("tiles cameras with what the survey saw on each", () => {
    render(
      <CameraTiles
        cameras={[{ camera_id: "cam1", label: "sink", source: "paired_phone" }]}
        inventory={demoPlanV1.inventory}
      />,
    );
    const tiles = screen.getByTestId("camera-tiles").querySelectorAll("article");
    expect(tiles).toHaveLength(2);
    expect(tiles[0]?.textContent).toContain("hands ×2");
    expect(tiles[1]?.textContent).toContain("from the survey");
    cleanup();
    render(<CameraTiles cameras={[]} inventory={[]} />);
    expect(screen.getByText(/No cameras yet/)).toBeTruthy();
  });
});
