import { describe, expect, it } from "vitest";

import { hostLabel, modelLabel, modelLine, modelRows, tierRole, verifierLabel } from "./models";

const META_API = {
  tier1: "muse-spark-1.3",
  tier1_host: "meta-api",
  tier2: "muse-wonder-1.0",
  tier2_host: "meta-api",
  sam: "sam-3.1",
  sam_host: "meta-api",
} as const;

describe("modelLabel", () => {
  it("says a model id the way people say it", () => {
    expect(modelLabel("muse-spark-1.3")).toBe("Muse Spark 1.3");
    expect(modelLabel("muse-wonder-1.0")).toBe("Muse Wonder 1.0");
    expect(modelLabel("muse-glimmer-30b")).toBe("Muse Glimmer 30B");
    expect(modelLabel("muse-glimmer-30b:free")).toBe("Muse Glimmer 30B");
    expect(modelLabel("meta-models/muse-spark-open-1026")).toBe("Muse Spark Open 1026");
    expect(modelLabel("sam-3.1")).toBe("SAM 3.1");
    expect(modelLabel(null)).toBe("");
  });

  it("adds where it runs, or falls back to the role", () => {
    expect(modelLine("muse-spark-1.3", "meta-api", "Tier 1 check")).toBe("Muse Spark 1.3 · Meta API");
    expect(modelLine("muse-glimmer-30b", "self-hosted", "Tier 1 check")).toBe("Muse Glimmer 30B · our GPU");
    expect(modelLine(undefined, "meta-api", "Tier 1 check")).toBe("Tier 1 check");
    expect(hostLabel(null)).toBe("");
  });

  it("names a verdict by its tier's role and the model that ran it", () => {
    expect(tierRole("glimmer")).toBe("Tier 1");
    expect(tierRole("spark_open")).toBe("Tier 2");
    expect(tierRole("muse-glimmer-30b")).toBe("Muse Glimmer 30B"); // a chain record's tier
    expect(verifierLabel("glimmer", "muse-spark-1.3")).toBe("Tier 1 · Muse Spark 1.3");
    expect(verifierLabel("spark_open", "")).toBe("Tier 2");
  });
});

describe("modelRows", () => {
  it("names each job from /health and says the Meta API, not a GPU state", () => {
    const rows = modelRows({ ok: true, models: META_API, gpus: { glimmer: { state: "warm", up: true } } });
    expect(rows.map((r) => [r.role, r.model, r.status])).toEqual([
      ["Tier 1 check", "Muse Spark 1.3", "Meta API"],
      ["Tier 2 review", "Muse Wonder 1.0", "Meta API"],
      ["SAM 3.1", "SAM 3.1", "Meta API"],
    ]);
  });

  it("shows a failing Meta API probe and a missing Tier 2", () => {
    const rows = modelRows({
      models: { ...META_API, tier2: null, tier2_host: null },
      gpus: { glimmer: { state: "unhealthy", up: false } },
    });
    expect(rows[0]?.status).toBe("Meta API · unhealthy");
    expect(rows[1]).toMatchObject({ model: null, status: "not configured" });
  });

  it("keeps our own GPUs' states and workers when the models run there", () => {
    const rows = modelRows({
      models: { ...META_API, tier1: "muse-glimmer-30b", tier1_host: "self-hosted", sam_host: "self-hosted" },
      gpus: {
        glimmer: { state: "cold-ready", up: true, workers: { running: 0, idle: 1 } },
        sam: { state: "warm", up: true },
      },
    });
    expect(rows[0]).toMatchObject({ model: "Muse Glimmer 30B", status: "cold-ready", workers: "0 running, 1 idle" });
    expect(rows[2]?.status).toBe("warm");
  });

  it("says only roles and probe states when the API sends no models", () => {
    const rows = modelRows({ gpus: { glimmer: "cold-ready" } });
    expect(rows.map((r) => [r.model, r.status])).toEqual([
      [null, "cold-ready"],
      [null, "unknown"],
      [null, "unknown"],
    ]);
    expect(modelRows(null).every((r) => r.status === "unknown")).toBe(true);
  });
});
