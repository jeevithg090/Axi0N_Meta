// The browser's checks against packs the Python builder wrote (tests/compliance/test_pack.py
// keeps fixture.json current): a recorded golden chain and a simulated one.

import { webcrypto } from "node:crypto";
import { beforeAll, describe, expect, it } from "vitest";

import { canonical, formatValue, merkleRoot, micros, renderTemplate } from "./engine";
import fixtureJson from "./fixture.json";
import type { SharedPack } from "./types";
import { anchorDay, tokenHash, verifyPack } from "./verify";

const fixture = fixtureJson as unknown as { golden: SharedPack; synthetic: SharedPack };

function copy(pack: SharedPack): SharedPack {
  return JSON.parse(JSON.stringify(pack)) as SharedPack;
}

beforeAll(() => {
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  }
});

describe("proof pack verification in the browser", () => {
  it.each(["golden", "synthetic"] as const)("passes every check on the %s pack", async (name) => {
    const report = await verifyPack(fixture[name], null);
    expect(report.numbers.problems).toEqual([]);
    expect(report.records.problems).toEqual([]);
    expect(report.windows.problems).toEqual([]);
    expect(report.summaries.problems).toEqual([]);
    expect(report.rules.problems).toEqual([]);
    expect(report.ok).toBe(true);
  });

  it("catches an edited record", async () => {
    const pack = copy(fixture.golden);
    const record = pack.pack.records.find((r) => r.record.kind === "verdict");
    (record!.record.payload as Record<string, unknown>).verdict = "violation";
    const report = await verifyPack(pack, null);
    expect(report.records.ok).toBe(false);
    expect(report.packHash.ok).toBe(false);
  });

  it("catches a changed number even when the pack hash is recomputed", async () => {
    const pack = copy(fixture.synthetic);
    const metric = pack.pack.metrics.find((m) => m.id === "glimmer_verified")!;
    metric.value = 7;
    pack.pack_hash = "0".repeat(64);
    const report = await verifyPack(pack, null);
    expect(report.numbers.ok).toBe(false);
    expect(report.numbers.problems[0]).toContain("recomputed 1, pack says 7");
  });

  it("catches a withheld record inside the window", async () => {
    const pack = copy(fixture.golden);
    const day = pack.pack.days[0]!;
    pack.pack.records = pack.pack.records.filter(
      (r) => r.key !== `${day.day}#${(day.window_first ?? 1) + 1}`,
    );
    expect((await verifyPack(pack, null)).windows.ok).toBe(false);
  });

  it("catches a summary sentence that does not follow from the numbers", async () => {
    const pack = copy(fixture.synthetic);
    pack.pack.summaries[0]!.text = ["Rules raised 0 alerts in this period."];
    expect((await verifyPack(pack, null)).summaries.ok).toBe(false);
  });

  it("flags a number counted per person or per track", async () => {
    const pack = copy(fixture.synthetic);
    pack.pack.metrics[0]!.spec.where["payload.candidate.predicate_trace.subject"] = "x";
    const report = await verifyPack(pack, null);
    expect(report.perPerson.ok).toBe(false);
    expect(report.perPerson.problems[0]).toContain("subject");
  });

  it("checks the link's stated hash", async () => {
    const claims = btoa(JSON.stringify({ p: "pp-x", h: "a".repeat(64), x: 1 }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(tokenHash(`${claims}.sig`)).toBe("a".repeat(64));
    expect((await verifyPack(fixture.golden, `${claims}.sig`)).packHash.ok).toBe(false);
  });

  it("anchors a day against the site's live hash list", async () => {
    const pack = fixture.golden.pack;
    const day = pack.days[0]!;
    const hashes = pack.records
      .filter((r) => r.day === day.day)
      .sort((a, b) => Number(a.record.seq) - Number(b.record.seq))
      .map((r) => String(r.record.hash));
    // The golden window discloses only part of the day, so build the anchor from a day it fully covers.
    const full = { ...day, n: hashes.length, root: await merkleRoot(hashes) };
    expect((await anchorDay(full, hashes)).live).toBe("match");
    expect((await anchorDay(full, [...hashes, "f".repeat(64)])).live).toBe("extended");
    expect((await anchorDay(full, hashes.slice(1))).live).toBe("mismatch");
    expect((await anchorDay(day, null)).live).toBe("unavailable");
  });
});

describe("engine helpers mirror Python", () => {
  it("formats values like narrate.format_value", () => {
    expect(formatValue(null, "count")).toBe("n/a");
    expect(formatValue(20.5, "seconds")).toBe("21 s");
    expect(formatValue(150, "seconds")).toBe("3 min");
    expect(formatValue(4, "count")).toBe("4");
  });

  it("parses microsecond timestamps exactly", () => {
    expect(micros("2026-11-01T00:00:11.949308Z") - micros("2026-11-01T00:00:00Z")).toBe(11_949_308);
    expect(micros("2026-11-01T01:00:00+01:00")).toBe(micros("2026-11-01T00:00:00Z"));
  });

  it("refuses templates with typed digits", () => {
    const metrics = new Map([["a", { value: 3, previous: 1, unit: "count" }]]);
    expect(renderTemplate("{a} now, {a.previous} before", metrics)).toBe("3 now, 1 before");
    expect(renderTemplate("3 alerts", metrics)).toBeNull();
    expect(canonical({ b: 1, a: [true, null] })).toBe('{"a":[true,null],"b":1}');
  });
});
