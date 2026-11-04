import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { densityColour, fileUrl, seconds } from "./crowdApi";
import { VenueMap } from "./VenueMap";
import type { Assessment, Place } from "./crowdApi";

afterEach(cleanup);

const places: Place[] = [
  { name: "Outer queue barrier", kind: "holding point", leads_to: ["Main gate"] },
  {
    name: "Main gate",
    kind: "gate",
    leads_to: ["Hall"],
    camera_id: "phone-2",
    zone: "gate",
    now: { at: "2026-11-04T00:45:56Z", people: 33, density_ppm2: 8.2 },
  },
  { name: "Hall", kind: "hall" },
];

const assessment: Assessment = {
  level: "critical",
  zones: ["gate"],
  why: "8.2 per m² at the gate.",
  forecast: "Stays dangerous while inflow continues.",
  minutes_to_danger: null,
  alert: true,
  staff_actions: [],
  actions: [{ do: "hold", target: "Outer queue barrier", role: "stewards", why: "inflow" }],
  announce: [],
  check: "",
};

describe("crowd page helpers", () => {
  it("formats durations and colours densities from blue to red", () => {
    expect(seconds(42)).toBe("42 s");
    expect(seconds(150)).toBe("2.5 min");
    expect(seconds(null)).toBe("–");
    expect(densityColour(0)).toContain("hsla(220");
    expect(densityColour(9)).toContain("hsla(0");
    expect(fileUrl("a b.jpg")).toBe("/rulecam-api/crowd/files/a%20b.jpg");
  });

  it("draws the venue with measured places and Spark's targets", () => {
    render(<VenueMap places={places} sample assessment={assessment} routes={[]} />);
    expect(screen.getByText("SAMPLE layout")).toBeTruthy();
    expect(screen.getByText("8.2/m² · 33")).toBeTruthy();
    expect(screen.getByText("HOLD")).toBeTruthy();
    expect(screen.getByText("Hall")).toBeTruthy();
  });

  it("says when there is no venue yet", () => {
    render(<VenueMap places={[]} sample={false} assessment={null} routes={[]} />);
    expect(screen.getByText(/No venue layout yet/)).toBeTruthy();
  });
});
