import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { claimParts, formatNumber, scaleX, verdictLine, type AdviceCard } from "@/lib/advice";

import { AdviceCardView } from "./AdviceCardView";

afterEach(cleanup);

// Test fixture only: the shape `GET /advice` returns (rulecam.advisor.api.AdviceCard).
const CARD: AdviceCard = {
  recommendation: {
    id: "rec-0007",
    status: "proposed",
    scope_kind: "station",
    scope_ids: ["dock_3"],
    klass: "test",
    provenance: "recorded",
    saw: [
      {
        template: "Trucks wait {m1} for a forklift at dock 3 vs {m2} turnaround at dock 1",
        slots: { m1: "mr_wait", m2: "mr_turn" },
        grade: "B",
      },
    ],
    why: [
      { id: "h1", statement: "13:00 replenishment occupies both forklifts", status: "supported" },
      { id: "h2", statement: "trucks arrive off-appointment", status: "ruled_out" },
    ],
    do: [{ op: "resequence", params: { task: "replenishment", to: "before 12:00" } }],
    who_role: "dock_lead",
    revert: "move replenishment back to 13:00",
    expect: {
      metric: "dock3_wait_for_forklift_min",
      interval: [-14, -4],
      level: 0.8,
      unit: "min/truck",
      model_rung: "erlang_c",
      assumptions: { poisson_arrivals: "pass", stable_service: "pass" },
    },
    safety: {
      rules_checked: ["ped-forklift-2m"],
      verdict: "pass",
      considered_not_recommended: [
        ["add a second forklift at the docks", "raises ped-forklift-2m exposure"],
      ],
    },
    rank_score: 0.8,
    numbers_total: 2,
    numbers_reproduced: 2,
  },
  checks: [
    {
      result_id: "mr_wait",
      metric: "dock_wait_for_forklift_min",
      catalogue_value: 14,
      unit: "min",
      recomputed: 14,
      sql: "SELECT avg(wait_s) / 60 FROM cases WHERE station = 'dock_3'",
      ok: true,
      error: "",
    },
    {
      result_id: "mr_turn",
      metric: "dock_turnaround_min",
      catalogue_value: 31,
      unit: "min",
      recomputed: 31,
      sql: "SELECT avg(turnaround_s) / 60 FROM cases WHERE station = 'dock_1'",
      ok: true,
      error: "",
    },
  ],
  hidden: [],
  flagged: false,
};

const flagged: AdviceCard = {
  ...CARD,
  recommendation: { ...CARD.recommendation, numbers_reproduced: 1 },
  checks: [
    { ...CARD.checks[0]!, recomputed: 52, ok: false, error: "value differs from the catalogue" },
    CARD.checks[1]!,
  ],
  hidden: ["mr_wait"],
  flagged: true,
};

describe("advice helpers", () => {
  it("fills slots from checks and keeps hidden numbers marked", () => {
    const parts = claimParts(CARD.recommendation.saw[0]!, flagged);
    expect(
      parts.map((p) => ("text" in p ? "t" : "check" in p ? (p.hidden ? "H" : "n") : "?")),
    ).toEqual(["t", "H", "t", "n", "t"]);
    const missing = claimParts({ template: "x {zz}", slots: {}, grade: "C" }, CARD);
    expect(missing[1]).toEqual({ missing: "zz" });
  });

  it("formats numbers and the verdict line", () => {
    expect(formatNumber(14, "min")).toBe("14 min");
    expect(formatNumber(13.94, "")).toBe("13.9");
    expect(
      verdictLine(
        { plan_id: "p", label: "worked", effect: -9, ci: [-14, -4], p_value: 0.031, pairs_done: 6 },
        "min/truck",
      ),
    ).toBe("Worked: −9 min/truck (CI −14 to −4), 6 pairs");
    expect(scaleX(0, 10, 100)).toBe(50);
    expect(scaleX(-99, 10, 100)).toBe(0);
  });
});

describe("advice card", () => {
  it("shows the six parts, the reproduced count and the safety veto list", () => {
    render(<AdviceCardView card={CARD} seals={[]} busy={false} onAccept={() => undefined} />);
    const card = screen.getByTestId("advice-card");
    for (const part of ["Saw", "Why", "Do", "Gain", "Check", "Safety"]) {
      expect(screen.getByRole("region", { name: part })).toBeTruthy();
    }
    expect(screen.getByTestId("reproduced").textContent).toBe("2/2 numbers reproduced by the Tier 1 model");
    expect(card.textContent).toContain("14 min");
    expect(card.textContent).toContain(
      "Considered and not recommended: add a second forklift at the docks",
    );
    expect(card.textContent).toContain("(ruled out)");
    expect(card.textContent).toContain("recorded");
  });

  it("opens the Tier 1 model's SQL when a number is clicked", () => {
    render(<AdviceCardView card={CARD} seals={[]} busy={false} onAccept={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "14 min" }));
    const modal = screen.getByTestId("number-modal");
    expect(modal.textContent).toContain("SELECT avg(wait_s) / 60 FROM cases");
    expect(modal.textContent).toContain("✓ reproduced");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("number-modal")).toBeNull();
  });

  it("hides a number the Tier 1 model could not reproduce and flags the card", () => {
    render(<AdviceCardView card={flagged} seals={[]} busy={false} onAccept={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toContain("could not be reproduced");
    expect(screen.queryByRole("button", { name: "14 min" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "hidden: not reproduced" }));
    const modal = screen.getByTestId("number-modal");
    expect(modal.textContent).toContain("Catalogue: hidden");
    expect(modal.textContent).toContain("✗ value differs from the catalogue");
  });

  it("accepts, then shows the pre-registration, the verdict and the sealed-cause match", () => {
    const onAccept = vi.fn();
    render(<AdviceCardView card={CARD} seals={[]} busy={false} onAccept={onAccept} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept and pre-register the check" }));
    expect(onAccept).toHaveBeenCalledOnce();
    cleanup();
    const measured: AdviceCard = {
      ...CARD,
      recommendation: { ...CARD.recommendation, status: "measuring" },
      measurement_plan: {
        id: "mp-0007",
        design: "switchback",
        block_minutes: 20,
        n_pairs: 6,
        min_detectable_effect: 4,
        smallest_possible_p: 0.03125,
        decision_rule: "worked if the CI excludes 0",
        preregistration_hash: "ab".repeat(32),
      },
      preregistered_seq: 1204,
      verdict: {
        plan_id: "mp-0007",
        label: "worked",
        effect: -9,
        ci: [-14, -4],
        p_value: 0.031,
        pairs_done: 6,
      },
    };
    const seals = [{ seal_id: "seal_1", sealed: true, revealed: true, matches: true, cause: "x" }];
    render(
      <AdviceCardView card={measured} seals={seals} busy={false} onAccept={() => undefined} />,
    );
    expect(screen.getByTestId("preregistered").textContent).toContain(
      "Pre-registered at event 1204",
    );
    expect(screen.getByTestId("verdict-plot").textContent).toContain(
      "Worked: −9 min/truck (CI −14 to −4), 6 pairs",
    );
    expect(screen.getByTestId("seal-badge").textContent).toBe("✓ Sealed cause revealed: match");
    expect(screen.queryByRole("button", { name: /Accept/ })).toBeNull();
  });

  it("says when a sealed cause does not match", () => {
    const seals = [{ seal_id: "s", sealed: true, revealed: true, matches: false, cause: "x" }];
    render(<AdviceCardView card={CARD} seals={seals} busy={false} onAccept={() => undefined} />);
    expect(screen.getByTestId("seal-badge").textContent).toBe("✗ Sealed cause does not match");
  });
});
