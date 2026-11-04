import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { demoFrames } from "@/fixtures/demo-frames";
import { initialState, reducer, ruleCards, type UiState } from "@/lib/store";

import { ModeBadge } from "./ModeBadge";
import { RelayHud } from "./RelayHud";
import { RuleCard } from "./RuleCard";
import { VerifierPanel } from "./VerifierPanel";

afterEach(cleanup);

const demoState = (): UiState =>
  demoFrames.reduce((s, frame) => reducer(s, { type: "frame", frame }), initialState);

describe("screens", () => {
  it("always renders the badge, whatever the state", () => {
    for (const state of [
      initialState,
      demoState(),
      reducer(initialState, { type: "connection", connection: "closed" }),
    ]) {
      render(<ModeBadge state={state} />);
      expect(screen.getByTestId("mode-badge").textContent).toMatch(/LIVE|REPLAY|OFFLINE|…/);
      cleanup();
    }
    render(<ModeBadge state={demoState()} />);
    expect(screen.getByTestId("mode-badge").textContent).toBe("REPLAY");
  });

  it("greys out rule cards on a BLIND camera, with a text label as well as colour", () => {
    const [card] = ruleCards(demoState());
    expect(card).toBeDefined();
    render(<RuleCard card={card!} />);
    const element = screen.getByTestId("rule-card");
    expect(element.dataset.blind).toBe("true");
    expect(element.className).toContain("rule-card-blind");
    expect(element.textContent).toContain("BLIND");
    expect(element.textContent).toContain("not being checked");
  });

  it("shows a normal card with its severity when the camera is fine", () => {
    const state = reducer(demoState(), {
      type: "frame",
      frame: { topic: "coverage.changed", payload: { camera_id: "cam1", state: "OK" } },
    });
    render(<RuleCard card={ruleCards(state)[0]!} />);
    const element = screen.getByTestId("rule-card");
    expect(element.dataset.blind).toBe("false");
    expect(element.textContent).toContain("MEDIUM");
    expect(element.textContent).toContain("8 (7.6–8.4) s");
  });

  it("renders the relay HUD with only the models that ran", () => {
    render(<RelayHud relay={demoState().relay} />);
    expect(screen.getByTestId("relay-hud").textContent).toContain("muse-glimmer-30b");
    expect(screen.getByTestId("relay-hud").textContent).not.toContain("sam-3.1");
    expect(screen.getByTestId("hud-count").textContent).toBe("2 Meta models");
  });

  it("streams verifier reasoning and scene text into the panel", () => {
    const [view] = demoState().events;
    const withSpark = {
      ...view!,
      verdicts: [
        ...view!.verdicts,
        {
          tier: "spark_open" as const,
          verdict: "violation" as const,
          score: 0.8,
          reason: "Confirmed.",
          reasoning: "The trace says 8 of 20 seconds.",
          scene_text: "",
          tamper: true,
          latency_ms: 2200,
          model_version: "muse-wonder-1.0",
        },
      ],
    };
    render(<VerifierPanel view={withSpark} />);
    const panel = screen.getByTestId("verifier-panel");
    // Each tier is named by the model its verdict says ran (`model_version`).
    expect(panel.textContent).toContain("Tier 1 · Muse Glimmer 30B");
    expect(panel.textContent).toContain("Tier 2 · Muse Wonder 1.0");
    expect(panel.textContent).toContain("The trace says 8 of 20 seconds.");
    expect(panel.textContent).toContain("tamper attempt");
    expect(panel.textContent).toContain("WASH HANDS");
  });
});
