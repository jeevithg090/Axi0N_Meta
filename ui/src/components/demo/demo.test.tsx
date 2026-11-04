import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { GOLDEN_RUNS } from "@/fixtures/golden";
import { CAMPAIGN, campaignTest, firstEvent, SERVERLESS_LOAD } from "@/fixtures/live-campaign";

import { headlines, Landing, MODELS, PITCH, STEPS, WORLDS } from "./Landing";
import { LiveAlerts, seconds, verdictWords } from "./LiveAlerts";

afterEach(cleanup);

describe("Landing", () => {
  it("leads with the pitch, the no-login demo and the verify page", () => {
    render(<Landing />);
    expect(screen.getByText(PITCH)).toBeTruthy();
    expect(screen.getByTestId("cta-demo").getAttribute("href")).toBe("/demo?demo=1");
    expect(screen.getByTestId("cta-verify").getAttribute("href")).toBe(GOLDEN_RUNS[0]!.verify_url);
    expect(screen.getByText(/works with\s+every GPU switched off/)).toBeTruthy();
  });

  it("shows the four steps and every Meta model with its status", () => {
    render(<Landing />);
    const steps = screen.getByRole("list", { name: "How a rule goes live" });
    for (const s of STEPS) {
      expect(within(steps).getByRole("heading", { name: s.name })).toBeTruthy();
      expect(within(steps).getByText(s.text)).toBeTruthy();
    }
    const table = screen.getByRole("table");
    for (const m of MODELS) {
      const row = within(table).getByText(m.model).closest("tr")!;
      expect(within(row).getByText(m.status)).toBeTruthy();
    }
  });

  it("links the three worlds, each with a labelled still from its own footage", () => {
    render(<Landing />);
    for (const w of WORLDS) {
      expect(screen.getByTestId(`world-${w.slug}`).getAttribute("href")).toBe(`/usecases/${w.slug}`);
      const still = screen.getByTestId(`still-${w.slug}`);
      expect(within(still).getByRole("img").getAttribute("alt")).toBe(w.still.alt);
      expect(still.textContent).toBe(w.still.label);
    }
  });

  it("quotes four real numbers, each read from a result file and linked to it", () => {
    const t02 = firstEvent(campaignTest("T02"));
    const t04 = firstEvent(campaignTest("T04"));
    expect(headlines().map((h) => h.value)).toEqual([
      `${t02.measurement!.value.toFixed(2)} m`,
      t04.verdict.score.toFixed(2),
      `${t02.event_to_decision_s.toFixed(1)} s`,
      `$${SERVERLESS_LOAD.usd_per_alert.toFixed(4)}`,
    ]);
    // The values the README results table shows for the same facts.
    expect(headlines().map((h) => h.value)).toEqual(["2.90 m", "0.05", "19.0 s", "$0.0039"]);
    render(<Landing />);
    const tiles = screen.getAllByTestId("headline");
    expect(tiles).toHaveLength(4);
    for (const [i, h] of headlines().entries()) {
      const link = within(tiles[i]!).getByRole("link", { name: h.source });
      expect(link.getAttribute("href")).toContain(`/blob/main/${h.source}`);
    }
  });
});

describe("LiveAlerts", () => {
  it("is labelled as a recorded live run, never as a replay or as live now", () => {
    render(<LiveAlerts />);
    const section = screen.getByTestId("live-alerts");
    expect(section.textContent).toContain(
      "Recorded live run, 1 Nov 2026. Not a replay, not live now.",
    );
    expect(screen.getByRole("link", { name: CAMPAIGN.source }).getAttribute("href")).toContain(
      CAMPAIGN.source,
    );
  });

  it("shows each test's request, measurement, Glimmer's verdict and reasoning, and outcome", () => {
    render(<LiveAlerts />);
    for (const test of CAMPAIGN.tests) {
      const card = screen.getByTestId(`live-${test.id}`);
      const event = firstEvent(test);
      const text = card.textContent ?? "";
      expect(text).toContain(test.request);
      expect(text).toContain(event.verdict.reason);
      expect(text).toContain(`P(violation) ${event.verdict.score.toFixed(2)}`);
      expect(text).toContain(`${event.measurement!.value.toFixed(2)} m`);
      expect(text).toContain(seconds(event.event_to_decision_s));
      for (const action of event.actions) expect(text).toContain(action);
      expect(text).toContain(event.decision.alert ? "Verified alert" : "Rejected by Glimmer");
    }
    expect(screen.getByTestId("live-T02").textContent).toContain("(the rule says under 4 m)");
    expect(screen.getByTestId("live-T03").textContent).toContain("carretilla elevadora");
    expect(screen.getByTestId("live-T04").textContent).toContain("no alert");
  });

  it("publishes no evidence images (none from this run are in the repository)", () => {
    render(<LiveAlerts />);
    expect(screen.queryAllByRole("img")).toHaveLength(0);
  });

  it("formats verdicts and durations plainly", () => {
    expect(verdictWords("no_violation")).toBe("no violation");
    expect(seconds(12891, "ms")).toBe("12.9 s");
    expect(seconds(19)).toBe("19.0 s");
  });
});
