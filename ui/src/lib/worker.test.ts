import { describe, expect, it } from "vitest";

import { workerPage } from "@/fixtures/worker-page";

import { bucketLabel, disputeError, STRINGS, uiLang, workerView } from "./worker";

describe("workerView", () => {
  it("joins rules with their counts, defaulting to zero", () => {
    const { rules } = workerView(workerPage());
    expect(rules).toEqual([
      { ruleId: "strap", text: "Abroche el arnés.", counts: { alerts: 3, disputes: 1, upheld: 1 } },
      { ruleId: "sink", text: null, counts: { alerts: 0, disputes: 0, upheld: 0 } },
    ]);
  });

  it("gives events their rule's text and bucket time, keeping the dispute outcome", () => {
    const { events } = workerView(workerPage());
    expect(events[0]).toEqual({
      code: "1A2B3C4D",
      ruleId: "strap",
      ruleText: "Abroche el arnés.",
      bucketStart: new Date("2026-11-02T10:15:00Z"),
      dispute: null,
    });
    expect(events[1]?.ruleText).toBeNull();
    expect(events[1]?.dispute).toBe("upheld");
  });
});

describe("bucketLabel", () => {
  it("shows the 15-minute window", () => {
    const start = new Date("2026-11-02T10:15:00Z");
    expect(bucketLabel(start, "en", "UTC")).toBe("Mon 10:15–10:30");
    expect(bucketLabel(start, "es", "UTC")).toBe("lun 10:15–10:30");
  });
});

describe("strings", () => {
  it("picks Spanish for any es locale and English otherwise", () => {
    expect(uiLang("es")).toBe("es");
    expect(uiLang("es-MX")).toBe("es");
    expect(uiLang("vi")).toBe("en");
  });

  it("has the same keys in every language", () => {
    expect(Object.keys(STRINGS.es).sort()).toEqual(Object.keys(STRINGS.en).sort());
  });

  it("maps dispute failures to messages", () => {
    expect(disputeError(429, STRINGS.en)).toBe(STRINGS.en.errors.rateLimited);
    expect(disputeError(404, STRINGS.es)).toBe(STRINGS.es.errors.notFound);
    expect(disputeError(0, STRINGS.en)).toBe(STRINGS.en.errors.failed);
  });
});
