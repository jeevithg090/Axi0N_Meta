import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import {
  CAMPAIGN,
  CAMPAIGN_REPORT_MD,
  campaignTest,
  distanceLimit,
  firstEvent,
  SERVERLESS_LOAD,
} from "./index";

// The repository root, four levels up from ui/src/fixtures/live-campaign/.
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const repoPath = (path: string) => resolve(ROOT, path);
const readJson = (path: string): unknown => JSON.parse(readFileSync(repoPath(path), "utf8"));
const pick = (record: Record<string, unknown>, keys: string[]) =>
  Object.fromEntries(keys.map((k) => [k, record[k]]));

const RECORD_KEYS = [
  "id",
  "clip",
  "request",
  "camera_id",
  "plan_id",
  "plan_s",
  "started_at",
  "finished_at",
  "back_translation",
  "metrics",
];
const RULE_KEYS = ["id", "severity", "predicate"];
const EVENT_KEYS = [
  "event_id",
  "rule_id",
  "ts_start",
  "ts_end",
  "measurement",
  "calibration",
  "cal_err",
  "verdict",
  "decision",
  "event_to_candidate_s",
  "event_to_decision_s",
  "actions",
];

type Row = Record<string, unknown> & {
  id: string;
  rules: Record<string, unknown>[];
  events: Record<string, unknown>[];
};

describe("live campaign fixture", () => {
  it("is a verbatim copy of T02, T03 and T04 from the campaign result file", () => {
    expect(CAMPAIGN.source).toBe("reports/eval/2026-11-01-live-test-campaign.json");
    const report = readJson(CAMPAIGN.source) as Row[];
    const byId = new Map(report.map((r) => [r.id, r]));
    expect(CAMPAIGN.tests.map((t) => t.id)).toEqual(["T02", "T03", "T04"]);
    for (const test of CAMPAIGN.tests) {
      const row = byId.get(test.id)!;
      expect(test).toEqual({
        ...pick(row, RECORD_KEYS),
        rules: row.rules.map((r) => pick(r, RULE_KEYS)),
        events: row.events.map((e) => pick(e, EVENT_KEYS)),
      });
    }
  });

  it("names each clip as the report does and copies its manifest", () => {
    const md = readFileSync(repoPath(CAMPAIGN_REPORT_MD), "utf8");
    for (const [name, clip] of Object.entries(CAMPAIGN.clips)) {
      const id = clip.folder.replace("pexels_", "");
      expect(md).toContain(`| ${name} | Pexels ${id}`);
      const manifest = parse(
        readFileSync(repoPath(`eval/clips/${clip.folder}/clip.yaml`), "utf8"),
      ) as Record<string, unknown> & { source: { url: string } };
      expect(clip).toEqual({
        folder: clip.folder,
        description: manifest.description,
        license: manifest.license,
        duration_s: manifest.duration_s,
        source_url: manifest.source.url,
      });
    }
    for (const test of CAMPAIGN.tests) expect(CAMPAIGN.clips[test.clip]).toBeTruthy();
  });

  it("copies the load test's cost per alert check (20 at once, warm)", () => {
    const load = readJson(SERVERLESS_LOAD.source) as {
      scenarios: { burst: number; state: string; usd_per_alert: number }[];
    };
    const scenario = load.scenarios[SERVERLESS_LOAD.scenario]!;
    expect(SERVERLESS_LOAD).toMatchObject({
      burst: scenario.burst,
      state: scenario.state,
      usd_per_alert: scenario.usd_per_alert,
    });
    expect(SERVERLESS_LOAD.burst).toBe(20);
    expect(SERVERLESS_LOAD.state).toBe("warm");
  });

  it("holds the outcomes the README quotes: T02 and T03 alert, T04 is rejected", () => {
    const [t02, t03, t04] = ["T02", "T03", "T04"].map((id) => firstEvent(campaignTest(id)));
    expect(t02!.decision.alert).toBe(true);
    expect(t02!.verdict.verdict).toBe("violation");
    expect(t03!.decision.alert).toBe(true);
    expect(t04!.decision.alert).toBe(false);
    expect(t04!.verdict.verdict).toBe("no_violation");
    expect(distanceLimit(campaignTest("T02").rules[0]!.predicate)).toBe(4);
    expect(() => campaignTest("T99")).toThrow();
  });
});
