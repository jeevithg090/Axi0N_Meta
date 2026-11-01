import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StripFrames, stripUrls } from "@/components/VerifierPanel";
import {
  DEFAULT_GOLDEN,
  GOLDEN_RUNS,
  goldenRun,
  PENDING_RUNS,
  type GoldenVerification,
} from "@/fixtures/golden";
import { decisionText, goldenActions, goldenCandidates, plural } from "@/lib/golden";

import DemoPage from "./page";

let golden: (typeof GOLDEN_RUNS)[number] | null = null;
let demo: boolean | null = true;
vi.mock("@/lib/stream", () => ({ useDemo: () => demo, useGolden: () => golden }));

afterEach(() => {
  cleanup();
  golden = null;
  demo = true;
});

const run = GOLDEN_RUNS[0]!;

describe("golden runs", () => {
  it("selects runs from the demo parameter", () => {
    expect(goldenRun("1")?.name).toBe(DEFAULT_GOLDEN);
    expect(goldenRun(DEFAULT_GOLDEN)).toBe(run);
    expect(goldenRun(null)).toBeNull();
    expect(goldenRun("nope")).toBeNull();
  });

  it("is generated from a verified recording, not written by hand", () => {
    const { evidence, verify, plan } = run.steps;
    expect(run.frames[0]).toEqual({
      topic: "mode.changed",
      payload: { mode: "replay", badge: "REPLAY" },
    });
    expect(evidence.ok).toBe(true);
    expect(plan.rule_hashes_match_run).toBe(true);
    expect(verify.length).toBe(goldenCandidates(run).length);
    for (const v of verify) {
      expect(v.tier1_matches_chain).toBe(true);
      expect(v.decision_matches_chain).toBe(true);
    }
    expect(run.verify_url).toContain(`root=${evidence.root}`);
    expect(run.verify_url).toContain(`n=${evidence.n}`);
  });

  it("keeps each candidate's actions in chain order", () => {
    const [candidate] = goldenCandidates(run);
    expect(candidate).toBeDefined();
    const actions = goldenActions(run, candidate!.event_id);
    expect(actions.length).toBeGreaterThan(0);
    expect(goldenActions(run, "no-such-event")).toEqual([]);
  });

  it("describes the policy's decision from the recorded fields", () => {
    const v: GoldenVerification = {
      event_id: "e",
      rule_id: "r",
      severity: "high",
      measurement: null,
      strip_frames: 6,
      act_first: true,
      tier1: {
        tier: "glimmer",
        verdict: "no_violation",
        score: 0.05,
        reason: "operator",
        scene_text: "",
        tamper: false,
        model_version: "m",
      },
      tier1_matches_chain: true,
      decision: { alert: true, route: "human_queue", act_first: true, reason: "a person reviews" },
      decision_matches_chain: true,
    };
    expect(decisionText(v)).toBe(
      "Severity HIGH acts before verification; Glimmer then said no violation, so a person " +
        "reviews it (human queue): “a person reviews”.",
    );
    const auto = { ...v, decision: { ...v.decision, act_first: false, route: "auto" as const } };
    expect(decisionText(auto)).toContain("Glimmer said no violation before anything ran");
    expect(decisionText(auto)).toContain("handled automatically");
    expect(plural(1, "alert")).toBe("1 alert");
    expect(plural(0, "alert")).toBe("0 alerts");
  });
});

describe("strips", () => {
  it("shows only the golden runs' public strip copies", () => {
    expect(
      stripUrls([
        "/golden/run/strips/e/1.jpg",
        "/workspace/data/strips/cam/e/1.jpg",
        "/golden/../secret.jpg",
      ]),
    ).toEqual(["/golden/run/strips/e/1.jpg"]);
    const { container } = render(<StripFrames paths={["/workspace/data/strips/x/1.jpg"]} />);
    expect(container.innerHTML).toBe("");
    render(<StripFrames paths={["/golden/a/strips/e/1.jpg", "/golden/a/strips/e/2.jpg"]} />);
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });
});

describe("DemoPage", () => {
  it("lists the golden runs when the page is not replaying one", () => {
    golden = null;
    demo = false;
    render(<DemoPage />);
    expect(screen.getByRole("link", { name: DEFAULT_GOLDEN }).getAttribute("href")).toBe(
      `/demo?demo=${DEFAULT_GOLDEN}`,
    );
  });

  it("waits for the URL before choosing", () => {
    demo = null;
    render(<DemoPage />);
    expect(screen.getByText("Loading…")).toBeTruthy();
  });

  it("tells the recorded run: planner, rule, Glimmer's reasoning, decision and evidence", () => {
    golden = run;
    render(<DemoPage />);
    expect(screen.getByTestId("golden-run")).toBeTruthy();
    for (const step of run.steps.plan.planner_steps) {
      expect(screen.getByText(step.step)).toBeTruthy();
    }
    const [v] = run.steps.verify;
    expect(screen.getByTestId("golden-decision").textContent).toBe(decisionText(v!));
    expect(screen.getByText(new RegExp(v!.tier1.reason.slice(0, 30)))).toBeTruthy();
    expect(screen.getAllByRole("img")).toHaveLength(v!.strip_frames);
    expect(screen.getByTestId("verify-link").getAttribute("href")).toBe(run.verify_url);
    for (const pending of PENDING_RUNS) {
      expect(screen.getByLabelText(`Pending: ${pending.name}`)).toBeTruthy();
    }
  });
});
