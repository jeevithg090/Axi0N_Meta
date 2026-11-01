import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RuntimeMetrics, VerifierHealth } from "@/lib/api";
import type { EventView } from "@/lib/store";

import { VerifierBadge } from "./VerifierBadge";
import { VerifierPanel, verifierError } from "./VerifierPanel";

afterEach(cleanup);

const health = (over: Partial<VerifierHealth> = {}): VerifierHealth => ({
  state: "open",
  down: true,
  consecutive_failures: 3,
  failures: 3,
  fast_failures: 1,
  opens: 1,
  last_error: "ModelTransportError: POST /chat/completions failed: ConnectError",
  retry_in_s: 12,
  ...over,
});

const view = (reason: string, verdict: "unsure" | "violation" = "unsure"): EventView => ({
  candidate: {
    event_id: "ev-1",
    rule_id: "forklift-proximity",
    camera_id: "cam1",
    ts_start: "2026-11-01T12:00:00Z",
    ts_end: "2026-11-01T12:00:03Z",
    severity: "high",
    predicate_trace: { distance_m: 1.2 },
    strip_paths: [],
  },
  verdicts: [
    {
      tier: "glimmer",
      verdict,
      score: 0.5,
      reason,
      scene_text: "",
      tamper: false,
      latency_ms: 0,
      model_version: "muse-glimmer-30b",
    },
  ],
  actions: [
    {
      kind: "notify",
      role: "site_lead",
      gate: "human",
      ts: "2026-11-01T12:00:04Z",
      ok: true,
      detail: "",
    },
  ],
});

describe("verifier down", () => {
  it("shows a red header badge while the Tier 1 circuit is not closed", () => {
    const metrics: RuntimeMetrics = {
      verifier_down: true,
      verifier: health(),
      verifications_failed: 4,
    };
    render(<VerifierBadge metrics={metrics} />);
    const badge = screen.getByTestId("verifier-badge");
    expect(badge.textContent).toBe("VERIFIER DOWN");
    expect(badge.className).toContain("badge-alarm");
    expect(badge.getAttribute("role")).toBe("alert");
    expect(badge.getAttribute("title")).toContain("human queue");
    expect(badge.getAttribute("title")).toContain("ConnectError");
    expect(badge.getAttribute("title")).toContain("4 candidates decided without a verdict");
  });

  it("says when a trial is under way, and hides when the verifier is up", () => {
    render(
      <VerifierBadge
        metrics={{ verifier_down: true, verifier: health({ state: "half_open", last_error: "" }) }}
      />,
    );
    expect(screen.getByTestId("verifier-badge").textContent).toBe("VERIFIER DOWN · retrying");
    cleanup();
    const { container } = render(<VerifierBadge metrics={{ verifier_down: false }} />);
    expect(container.textContent).toBe("");
    render(<VerifierBadge metrics={null} />);
    expect(screen.queryByTestId("verifier-badge")).toBeNull();
  });

  it("marks an event Tier 1 could not verify as needing review", () => {
    const failed = view("verifier_error: circuit open, failing fast");
    expect(verifierError(failed)).toBe("circuit open, failing fast");
    render(<VerifierPanel view={failed} />);
    expect(screen.getByTestId("needs-review").textContent).toBe(
      "Needs review: verifier unavailable — circuit open, failing fast",
    );
  });

  it("does not mark a real verdict", () => {
    expect(verifierError(view("Glimmer is unsure about the distance."))).toBeNull();
    expect(verifierError(view("verifier_error: x", "violation"))).toBeNull();
    render(<VerifierPanel view={view("A forklift passes close to a person.", "violation")} />);
    expect(screen.queryByTestId("needs-review")).toBeNull();
  });
});
