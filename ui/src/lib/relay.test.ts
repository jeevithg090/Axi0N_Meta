import { describe, expect, it } from "vitest";

import type { ModelUsed } from "./events";
import { emptyRelay, isMetaModel, metaCount, reduceRelay, type RelayState } from "./relay";

const used = (model: string, segment: string, latency_ms = 100): ModelUsed => ({
  model,
  segment,
  latency_ms,
});

const fold = (events: ModelUsed[]): RelayState => events.reduce(reduceRelay, emptyRelay);

describe("relay HUD", () => {
  it("shows only models that actually ran", () => {
    expect(emptyRelay.models).toEqual([]);
    const state = fold([used("sam-3.1", "perception"), used("sam-3.1", "perception", 300)]);
    expect(state.models.map((m) => m.model)).toEqual(["sam-3.1"]);
    expect(state.models[0]).toMatchObject({ calls: 2, lastLatencyMs: 300, totalLatencyMs: 400 });
  });

  it("greys non-Meta parts", () => {
    expect(isMetaModel("sam-3.1")).toBe(true);
    expect(isMetaModel("muse-glimmer-30b")).toBe(true);
    expect(isMetaModel("meta-models/muse-spark-open-1026")).toBe(true);
    expect(isMetaModel("mms-tts-spa")).toBe(true);
    expect(isMetaModel("rtmpose-m")).toBe(false);
    expect(isMetaModel("espeak-ng")).toBe(false);
    const state = fold([used("sam-3.1", "verify"), used("espeak-ng", "verify")]);
    expect(state.models.map((m) => m.meta)).toEqual([true, false]);
    expect(metaCount(state)).toBe(1);
  });

  it("resets the list and counter when the segment changes", () => {
    const state = fold([
      used("sam-3.1", "perception"),
      used("muse-glimmer-30b", "verify"),
      used("meta-models/muse-spark-open-1026", "verify"),
    ]);
    expect(state.segment).toBe("verify");
    expect(state.models.map((m) => m.model)).toEqual([
      "muse-glimmer-30b",
      "meta-models/muse-spark-open-1026",
    ]);
    expect(metaCount(state)).toBe(2);
  });
});
