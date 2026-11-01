import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { demoAlerts, demoCiResults, demoSurprise } from "@/fixtures/demo-ops";
import { demoPlanV1 } from "@/fixtures/demo-plan";

import { AlertCard } from "./AlertCard";
import { CiReportCard } from "./CiReportCard";
import { Countdown } from "./Countdown";
import { DeclinedCardTile, RuleCardTile } from "./GalleryCards";
import { SurpriseView } from "./SurpriseView";

afterEach(cleanup);

describe("alerts PWA", () => {
  it("shows the three buttons and the escalation so far", () => {
    const onPress = vi.fn();
    render(
      <AlertCard
        alert={demoAlerts[0]!}
        busy={false}
        onPress={onPress}
        timeline={[
          {
            kind: "notify",
            role: "aide",
            gate: "auto",
            ts: "2026-11-01T22:00:12Z",
            ok: true,
            detail: "step 1/3",
          },
          {
            kind: "escalate",
            role: "charge_nurse",
            gate: "auto",
            ts: "2026-11-01T22:01:12Z",
            ok: true,
            detail: "",
          },
        ]}
      />,
    );
    const card = screen.getByTestId("alert-card");
    expect(card.textContent).toContain("22:00:12 notified aide");
    expect(card.textContent).toContain("22:01:12 escalated to charge_nurse");
    fireEvent.click(screen.getByRole("button", { name: "False alarm" }));
    expect(onPress).toHaveBeenCalledWith("False alarm");
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("hides the buttons once the alert is handled", () => {
    render(
      <AlertCard
        alert={{ ...demoAlerts[0]!, status: "acknowledged" }}
        timeline={[]}
        busy={false}
        onPress={() => undefined}
      />,
    );
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByTestId("alert-card").textContent).toContain("Acknowledged");
  });
});

describe("station display", () => {
  it("freezes red at 8 / 20 s on a fail, with a text label", () => {
    render(<Countdown value={{ state: "failed", elapsed: 8, required: 20 }} />);
    const el = screen.getByTestId("countdown");
    expect(el.dataset.state).toBe("failed");
    expect(el.className).toContain("countdown-failed");
    expect(el.textContent).toContain("8 / 20 s");
    expect(el.textContent).toContain("Too short");
  });

  it("shows progress and a pass in words too", () => {
    render(<Countdown value={{ state: "counting", elapsed: 12.7, required: 20 }} />);
    expect(screen.getByTestId("countdown").textContent).toBe("12 / 20 sKeep washing");
    cleanup();
    render(<Countdown value={{ state: "idle", elapsed: 0, required: 0 }} />);
    expect(screen.getByTestId("countdown").textContent).toContain("Ready");
  });
});

describe("Rule CI panel and card gallery", () => {
  it("shows the CI numbers, the fooled lookalike and its fix", () => {
    render(<CiReportCard result={demoCiResults[1]!} />);
    const card = screen.getByTestId("ci-card");
    expect(card.dataset.green).toBe("false");
    expect(card.textContent).toContain("CI red");
    expect(card.textContent).toContain(demoCiResults[1]!.summary);
    expect(card.textContent).toContain("18 lookalikes, fooled 1");
    expect(card.textContent).toContain(
      'Fooled by backpack strap → negative concept "backpack strap" on harness',
    );
    expect(card.textContent).toContain("alert still raised 6/6, tamper flagged 6");
    expect(card.textContent).toContain("not run: scenes");
    expect(card.querySelector(".over")?.textContent).toContain("7.1 alerts/shift (budget 5)");
  });

  it("renders the scene, injection and forecast lines of a green report", () => {
    render(<CiReportCard result={demoCiResults[0]!} />);
    const card = screen.getByTestId("ci-card");
    expect(card.dataset.green).toBe("true");
    expect(card.textContent).toContain("detectability scenes 11/12");
    expect(card.textContent).toContain("6 lookalikes, fooled 0");
    expect(card.textContent).toContain("alert still raised 6/6, tamper flagged 5");
    expect(card.textContent).toContain("forecast 2.4 alerts/shift (budget 5)");
    expect(card.querySelector(".over")).toBeNull();
  });

  it("shows red when the backend says so, even with every trace and mutant passing", () => {
    const wash = demoCiResults[0]!;
    const redTeam = wash.report.red_team!;
    const strap = { ...redTeam.lookalikes!.findings[0]!, perception_fooled: true };
    const fooled = {
      ...wash,
      ci_green: false,
      report: { ...wash.report, red_team: { ...redTeam, lookalikes: { findings: [strap] } } },
    };
    render(<CiReportCard result={fooled} />);
    const card = screen.getByTestId("ci-card");
    expect(card.dataset.green).toBe("false");
    expect(card.textContent).toContain("CI red");
    expect(card.textContent).toContain("boundary 3/3");
    expect(card.textContent).toContain("mutations 5/5");
    expect(card.textContent).toContain(`Fooled by ${strap.lookalike}`);
  });

  it("follows ci_green for zero traces and zero mutants instead of guessing", () => {
    const wash = demoCiResults[0]!;
    const empty = {
      ...wash,
      report: {
        ...wash.report,
        traces_passed: 0,
        traces_total: 0,
        mutants_killed: 0,
        mutants_total: 0,
      },
    };
    render(
      <CiReportCard
        result={{ ...empty, ci_green: false, summary: "boundary 0/0, mutations 0/0" }}
      />,
    );
    expect(screen.getByTestId("ci-card").dataset.green).toBe("false");
    expect(screen.getByTestId("ci-card").textContent).toContain("boundary 0/0, mutations 0/0");
    cleanup();
    const justified = {
      ...empty,
      report: { ...empty.report, justification: "operator-only rule" },
    };
    render(
      <CiReportCard
        result={{ ...justified, ci_green: true, summary: "boundary 0/0, mutations 0/0" }}
      />,
    );
    expect(screen.getByTestId("ci-card").dataset.green).toBe("true");
    expect(screen.getByTestId("ci-card").textContent).toContain(
      "Justification: operator-only rule",
    );
  });

  it("renders a rule card with the backend's CI verdict", () => {
    render(<RuleCardTile rule={demoPlanV1.rules[0]!} ci={demoCiResults[0]} />);
    const card = screen.getByTestId("gallery-rule");
    expect(card.textContent).toContain("CI green · boundary 3/3, mutations 5/5");
    expect(card.textContent).toContain("Checked by Glimmer + Spark Open");
    cleanup();
    render(
      <RuleCardTile rule={demoPlanV1.rules[0]!} ci={{ ...demoCiResults[0]!, ci_green: false }} />,
    );
    expect(screen.getByTestId("gallery-rule").textContent).toContain("CI red");
    cleanup();
    render(<RuleCardTile rule={demoPlanV1.rules[1]!} />);
    expect(screen.getByTestId("gallery-rule").textContent).toContain("Rule CI pending");
  });

  it("never puts the raw request text on a declined card", () => {
    const declined = {
      ...demoPlanV1.feasibility[3]!,
      concern: "Tell me if Maria the cook is sick",
    };
    render(<DeclinedCardTile verdict={declined} />);
    const card = screen.getByTestId("gallery-declined");
    expect(card.textContent).not.toContain("Maria");
    expect(card.textContent).not.toContain("Tell me if");
    expect(card.textContent).toContain("I won't judge anyone's health");
    expect(card.textContent).toContain("Instead:");
  });
});

describe("surprise view", () => {
  it("plots the curve, names what SAM sees and drafts a rule", () => {
    const onSuggest = vi.fn();
    render(<SurpriseView event={demoSurprise} heatmapUrl={null} onSuggest={onSuggest} />);
    const view = screen.getByTestId("surprise-view");
    expect(view.textContent).toContain("peak z 5.6");
    expect(view.textContent).toContain("cardboard box");
    expect(view.textContent).toContain("never cuts power");
    expect(view.querySelector("polyline")?.getAttribute("points")?.split(" ")).toHaveLength(12);
    fireEvent.click(screen.getByRole("button", { name: "Draft a rule from this" }));
    expect(onSuggest).toHaveBeenCalledOnce();
  });

  it("disables drafting, with a reason, when no backend route exists", () => {
    render(<SurpriseView event={demoSurprise} heatmapUrl={null} onSuggest={null} />);
    const button = screen.getByRole("button", { name: "Draft a rule from this" });
    expect(button).toHaveProperty("disabled", true);
    expect(screen.getByTestId("surprise-view").textContent).toContain(
      "not available on this server yet",
    );
  });

  it("shows the heatmap only from a URL", () => {
    render(
      <SurpriseView
        event={demoSurprise}
        heatmapUrl="https://x.test/h.png"
        onSuggest={() => undefined}
      />,
    );
    expect(screen.getByRole("img", { name: /Heatmap/ }).getAttribute("src")).toBe(
      "https://x.test/h.png",
    );
  });
});
