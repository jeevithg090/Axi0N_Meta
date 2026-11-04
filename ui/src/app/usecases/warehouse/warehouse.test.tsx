import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HeatmapView, NearMissPanel } from "./NearMissPanel";
import { StoryPanel } from "./StoryPanel";
import {
  heatFill,
  metres,
  progress,
  reportUrl,
  verifyUrl,
  type Heatmap,
  type NearMiss,
  type WarehouseStory,
} from "./warehouse";

// Shapes as the API returns them for the recorded T02 run (2 Nov 2026), trimmed.
const NEAR: NearMiss = {
  event_id: "ev-49940b44a5ce03bffe26313f",
  ts: "2026-11-02T23:43:47.167176Z",
  camera_id: "phone-97",
  rule_id: "keep-people-4m-from-moving-forklift",
  rule_text: "Keep people at least 4 metres away from a moving forklift.",
  lang: "en",
  plan_id: "plan-10517db0b9f6",
  severity: "high",
  limit_m: 4,
  separation_m: 3.991023,
  closest_m: 3.814843,
  ttc_s: null,
  vehicle_speed_mps: 0.328294,
  calibration: "person_height_1.7m",
  calibration_err_m: 0.5,
  verdict: "violation",
  verdict_score: 0.99,
  verdict_reason: "A person is within about 4 m of a moving forklift.",
  verifier: "muse-glimmer-30b",
  alert: true,
  route: "auto",
  actions: ["dry-run: would notify supervisor"],
  acked_by: [],
  disputes: [],
  regression: false,
  training_done: false,
  foot_px: [640, 600],
};

const STORY: WarehouseStory = {
  plan_id: "plan-10517db0b9f6",
  approved: false,
  request: NEAR.rule_text,
  request_lang: "en",
  rules: [{ id: NEAR.rule_id, severity: "high" }],
  cant_check: [
    { concern: NEAR.rule_text, status: "caveat", explanation: "calibration error 0.26 m" },
  ],
  event_id: NEAR.event_id,
  steps: [
    { key: "request", title: "Say or type the rule", status: "done", detail: "…", refs: {} },
    { key: "rule_ci", title: "Rule CI", status: "done", detail: "boundary 16/16", refs: {} },
    { key: "approve", title: "A person approves", status: "waiting", detail: "…", refs: {} },
  ],
  near_misses: [NEAR],
};

describe("warehouse helpers", () => {
  it("formats metres and heat", () => {
    expect(metres(3.991023)).toBe("3.99 m");
    expect(metres(null)).toBe("–");
    expect(heatFill(0, 3)).toBe("rgba(0,0,0,0)");
    expect(heatFill(3, 3)).toBe("rgba(220, 38, 38, 0.80)");
  });

  it("counts done steps and builds evidence links", () => {
    expect(progress(STORY.steps)).toEqual({ done: 2, total: 3 });
    expect(verifyUrl("2026-11-02")).toBe("/rulecam-api/verify/?day=2026-11-02");
    expect(reportUrl("ev-1")).toBe("/rulecam-api/incidents/ev-1/report?format=html");
  });
});

describe("warehouse panels", () => {
  it("shows the story with the planner's caveats and the approve button", () => {
    render(<StoryPanel story={STORY} onApproved={() => undefined} />);
    expect(screen.getByText("2/3 steps")).toBeTruthy();
    expect(screen.getByText("calibration error 0.26 m")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve this plan" })).toBeTruthy();
  });

  it("lists near misses and draws the heatmap", () => {
    const heat: Heatmap = {
      camera_id: "phone-97",
      day: "2026-11-02",
      width: 1280,
      height: 720,
      cols: 4,
      rows: 3,
      cells: [
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 1, 0],
      ],
      points: [{ event_id: NEAR.event_id, x: 640, y: 600, alert: true }],
      unplaced: 0,
      background: null,
    };
    render(
      <NearMissPanel
        rows={[NEAR]}
        heat={heat}
        frameUrl={null}
        selected={NEAR.event_id}
        onSelect={() => undefined}
      />,
    );
    expect(screen.getByText("3.99 m")).toBeTruthy();
    expect(screen.getByText("violation 0.99")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Near-miss heatmap" })).toBeTruthy();
    const { container } = render(<HeatmapView heat={heat} frameUrl="/frame.jpg" />);
    expect(container.querySelectorAll("rect")).toHaveLength(12);
  });
});
