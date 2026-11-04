import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CROSS_COUNT, HAZARD, REPORT } from "./proofData";
import { SupermarketProof } from "./SupermarketProof";

afterEach(cleanup);

// The repository root, five levels up from ui/src/app/usecases/supermarket/.
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
const report = readFileSync(resolve(ROOT, REPORT), "utf8");

describe("supermarket proof data", () => {
  it("is copied from the live report, value by value", () => {
    for (const f of CROSS_COUNT.frames) {
      const row = report.split("\n").find((l) => l.includes(`checkout queue, ${f.at}`));
      expect(row, f.at).toBeTruthy();
      for (const v of [f.byHand, `${f.tracker} \`customer\``, f.sam, f.spark, f.glimmer, `**${f.askNow}**`]) {
        expect(row, `${f.at}: ${v}`).toContain(v);
      }
    }
    expect(report).toContain(CROSS_COUNT.reconciled);
    expect(report).toContain(CROSS_COUNT.error);
    expect(report).toContain(`Seen ${HAZARD.seen}`);
    expect(report).toContain(`cleared ${HAZARD.cleared} (${HAZARD.minutes})`);
    expect(report).toContain(`Spark (${HAZARD.sparkProof}) and Glimmer (${HAZARD.glimmerProof})`);
    expect(report).toContain(`chain records ${HAZARD.detectRecord} (detection) and ${HAZARD.clearedRecord}`);
    expect(report).toContain(`chain intact (${HAZARD.chainRecords}`);
  });
});

describe("SupermarketProof", () => {
  it("leads with the count it fixed and labels the generated spill photo as SAMPLE", () => {
    render(<SupermarketProof />);
    const proof = screen.getByTestId("supermarket-proof");
    expect(proof.querySelector("s")?.textContent).toBe(CROSS_COUNT.frames[0].tracker);
    expect(proof.textContent).toContain(`${CROSS_COUNT.frames[0].askNow}people`);
    const badges = within(proof).getAllByTestId("live-badge").map((b) => b.textContent ?? "");
    expect(badges[0]).toContain("REPLAY");
    expect(badges.some((b) => b.includes("SAMPLE") && b.includes("Muse Image"))).toBe(true);
    expect(screen.getByRole("list", { name: "The spill task" }).textContent).toContain(HAZARD.minutes);
  });
});
