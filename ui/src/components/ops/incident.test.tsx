import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { IncidentReport } from "@/lib/incidents";
import { incidents } from "@/lib/incidents";

import { IncidentView } from "./IncidentView";

afterEach(cleanup);

const report: IncidentReport = {
  event_id: "ev-1",
  site: "demo",
  day: "2026-11-02",
  rule_id: "keep-2m",
  rule_text: "Keep people 2 m from a moving forklift.",
  clauses: ["Site rule"],
  severity: "high",
  camera_id: "phone-1",
  ts_start: "2026-11-02T10:00:00Z",
  ts_end: "2026-11-02T10:00:03Z",
  measurement: "1.20 m",
  privacy_mode: "strict",
  timeline: [
    {
      seq: 4,
      ts: "2026-11-02T10:00:03Z",
      kind: "candidate",
      summary: "Rule fired",
      hash: "a".repeat(64),
    },
    {
      seq: 5,
      ts: "2026-11-02T10:00:05Z",
      kind: "ack",
      summary: "Acknowledged by aide",
      hash: "b".repeat(64),
    },
  ],
  stills: 1,
  verify_url: "https://ui/verify/?day=2026-11-02",
  chain_records: 12,
  chain_ok: true,
};

describe("incident report", () => {
  it("shows the chained records and a one-click download", () => {
    render(<IncidentView report={report} />);
    expect(screen.getByText("Acknowledged by aide")).toBeTruthy();
    const link = screen.getByTestId("incident-download");
    expect(link.getAttribute("href")).toBe("/rulecam-api/incidents/ev-1/report");
    expect(screen.getByTestId("incident-chain").textContent).toContain("intact: 12 records");
    expect(screen.getByTestId("incident-chain").textContent).toContain("1 anonymised still ");
  });

  it("builds escaped report URLs", () => {
    expect(incidents.reportUrl("ev/1", "html")).toBe(
      "/rulecam-api/incidents/ev%2F1/report?format=html",
    );
  });

  it("says when the chain failed", () => {
    render(<IncidentView report={{ ...report, chain_ok: false, stills: 2, measurement: null }} />);
    expect(screen.getByTestId("incident-chain").textContent).toContain("failed verification");
  });
});
