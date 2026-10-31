// Cross-language check: the browser code must produce exactly the Python hashes.
// Run with `npx vitest run` in this folder (fixture written by tests/evidence/test_verify_page.py).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { merkleRoot, verifyChainText } from "./chain.js";

const fixtures = fileURLToPath(new URL("../../../../tests/fixtures/evidence/", import.meta.url));
const chain = readFileSync(fixtures + "chain.jsonl", "utf8");
const expected = JSON.parse(readFileSync(fixtures + "expected.json", "utf8"));

describe("verify page chain code", () => {
  it("recomputes the Python chain and root", async () => {
    const report = await verifyChainText(chain);
    expect(report.ok).toBe(true);
    expect(report.n).toBe(expected.n);
    expect(report.hashes).toEqual(expected.hashes);
    expect(await merkleRoot(report.hashes)).toBe(expected.root);
    expect(report.watched).toBe(expected.watched);
  });

  it("finds the first broken event", async () => {
    const lines = chain.split("\n");
    lines[2] = lines[2].replace('"i":2', '"i":7');
    const report = await verifyChainText(lines.join("\n"));
    expect(report.ok).toBe(false);
    expect(report.firstBadSeq).toBe(3);
  });
});
