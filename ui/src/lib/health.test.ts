import { describe, expect, it } from "vitest";

import { gpuLine, healthBadge } from "./health";

describe("health", () => {
  it("maps /health onto the badge the way the API's badge_for does", () => {
    const at = new Date();
    expect(healthBadge({ phase: "loading" })).toBe("…");
    expect(healthBadge({ phase: "error", at })).toBe("OFFLINE");
    expect(healthBadge({ phase: "ok", at, health: { ok: true, mode: "live" } })).toBe("LIVE");
    expect(healthBadge({ phase: "ok", at, health: { ok: true, mode: "record" } })).toBe("LIVE");
    expect(healthBadge({ phase: "ok", at, health: { ok: true, mode: "replay" } })).toBe("REPLAY");
    expect(healthBadge({ phase: "ok", at, health: { ok: false, mode: "live" } })).toBe("OFFLINE");
  });

  it("says a GPU's state and workers as the API reported them, never summed", () => {
    expect(
      gpuLine({ state: "warm", workers_max: 3, workers: { running: 2, idle: 3, initializing: 0 } }),
    ).toEqual({ state: "warm", workers: "2 running, 3 idle" });
    expect(gpuLine({ state: "cold-ready", workers: { running: 0, idle: 0, initializing: 1 } })).toEqual({
      state: "cold-ready",
      workers: "0 running, 0 idle, 1 starting",
    });
    expect(gpuLine("cold-ready")).toEqual({ state: "cold-ready", workers: null });
    expect(gpuLine(undefined)).toEqual({ state: "unknown", workers: null });
  });
});
