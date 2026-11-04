import { afterEach, describe, expect, it, vi } from "vitest";

import { SAMPLE_EXTENSION } from "@/fixtures/extension-sample";

import { setCsrfToken } from "./api";
import {
  canApprove,
  extensionRef,
  extensions,
  generatorNote,
  mutationScore,
  parseLine,
  reviewerNote,
  stepTitle,
} from "./extensions";

const record = SAMPLE_EXTENSION;

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("rule extensions", () => {
  it("reads the sample produced by the real proof", () => {
    expect(record.status).toBe("proposed");
    expect(extensionRef(record)).toBe(`ext:${record.name}@${record.sha256.slice(0, 12)}`);
    expect(canApprove(record)).toBe(true);
    expect(mutationScore(record)).toBeGreaterThanOrEqual(0.8);
  });

  it("never shows the fallback as Muse Code", () => {
    expect(generatorNote(record)).toMatch(/^Written by Muse Code/);
    expect(generatorNote({ ...record, generator: "spark-fallback" })).toMatch(
      /Muse Code CLI was not available/,
    );
    expect(reviewerNote({ ...record.review, reviewer: "spark-1.3-fallback" })).toMatch(
      /no Tier 2 model/,
    );
    expect(reviewerNote({ ...record.review, reviewer: "none" })).toBe("Not reviewed");
  });

  it("blocks approval unless proof and review both pass", () => {
    expect(canApprove({ ...record, status: "rejected" })).toBe(false);
    expect(canApprove({ ...record, review: { ...record.review, verdict: "reject" } })).toBe(false);
    expect(canApprove({ ...record, proof: { ...record.proof, passed: false } })).toBe(false);
  });

  it("names steps and parses lines", () => {
    expect(stepTitle("mutation")).toBe("Mutation testing");
    expect(stepTitle("two_versions")).toBe("two versions");
    expect(parseLine("0, 200, 640,210")).toEqual([
      [0, 200],
      [640, 210],
    ]);
    expect(parseLine("1,2,3")).toBeNull();
    expect(parseLine("a,b,c,d")).toBeNull();
  });

  it("approves the exact version with the CSRF token", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(record), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("tok");
    await extensions.approve(record);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(new RegExp(`/extensions/${record.name}/approve$`));
    expect(JSON.parse(String(init.body))).toEqual({ sha256: record.sha256 });
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("tok");
  });

  it("turns API errors into ApiError with the detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "no generator" }), { status: 503 })),
    );
    await expect(extensions.list()).rejects.toMatchObject({ status: 503, message: "no generator" });
  });
});
