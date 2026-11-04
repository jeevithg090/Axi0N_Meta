// Everything the recipient's browser checks in a shared proof pack, without trusting the site.

import {
  canonical,
  evaluate,
  fold,
  getPath,
  merkleRoot,
  recordHash,
  renderTemplate,
  sha256Hex,
  type Located,
} from "./engine";
import type { SharedPack } from "./types";

export interface Check {
  ok: boolean;
  passed: number;
  total: number;
  problems: string[];
}

export interface PackReport {
  packHash: Check;
  records: Check;
  windows: Check;
  numbers: Check;
  summaries: Check;
  rules: Check;
  perPerson: Check;
  ok: boolean;
}

export type LiveStatus = "match" | "extended" | "mismatch" | "unavailable";

export interface DayAnchor {
  day: string;
  live: LiveStatus;
  committed: LiveStatus | "none";
}

const MAX_PROBLEMS = 20;

function check(problems: string[], total: number): Check {
  return {
    ok: problems.length === 0,
    passed: total - problems.length,
    total,
    problems: problems.slice(0, MAX_PROBLEMS),
  };
}

function sameRefs(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, i) => value === b[i]);
}

/** The pack's hash as the share token states it (the token's payload is readable by anyone). */
export function tokenHash(token: string): string | null {
  try {
    const payload = token.split(".")[0] ?? "";
    const text = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(text) as { h?: unknown };
    return typeof claims.h === "string" ? claims.h : null;
  } catch {
    return null;
  }
}

function ordered(shared: SharedPack): Located[] {
  return shared.pack.records
    .map((r) => ({ key: r.key, day: r.day, record: r.record }))
    .sort((a, b) =>
      a.day === b.day ? Number(a.record.seq) - Number(b.record.seq) : a.day < b.day ? -1 : 1,
    );
}

async function checkRecords(shared: SharedPack): Promise<Check> {
  const roots = new Map(shared.pack.days.map((d) => [d.day, d.root]));
  const problems: string[] = [];
  for (const item of shared.pack.records) {
    const hash = await recordHash(item.record);
    if (hash !== item.record.hash) {
      problems.push(`${item.key}: contents do not hash to the recorded hash`);
      continue;
    }
    if (`${item.day}#${String(item.record.seq)}` !== item.key) {
      problems.push(`${item.key}: the key does not match the record's day and seq`);
      continue;
    }
    if ((await fold(hash, item.proof)) !== roots.get(item.day)) {
      problems.push(`${item.key}: the Merkle proof does not reach the day's root`);
    }
  }
  return check(problems, shared.pack.records.length);
}

/** No record inside a window was left out: the window's seqs are all disclosed, with both edges. */
function checkWindows(shared: SharedPack): Check {
  const keys = new Set(shared.pack.records.map((r) => r.key));
  const problems: string[] = [];
  for (const day of shared.pack.days) {
    if (day.window_first === null || day.window_last === null) continue;
    for (let seq = day.window_first; seq <= day.window_last; seq += 1) {
      if (!keys.has(`${day.day}#${seq}`)) problems.push(`${day.day}: record ${seq} is withheld`);
    }
    if (day.window_first > 1 && !keys.has(`${day.day}#${day.window_first - 1}`)) {
      problems.push(`${day.day}: the record before the window is not shown`);
    }
    if (day.window_last < day.n && !keys.has(`${day.day}#${day.window_last + 1}`)) {
      problems.push(`${day.day}: the record after the window is not shown`);
    }
  }
  return check(problems, shared.pack.days.length);
}

function checkNumbers(shared: SharedPack, records: Located[]): Check {
  const problems: string[] = [];
  for (const metric of shared.pack.metrics) {
    const now = evaluate(metric.spec, records, shared.pack.period, "current");
    const before = evaluate(metric.spec, records, shared.pack.period, "previous");
    if (now.value !== metric.value || !sameRefs(now.refs, metric.refs)) {
      problems.push(
        `${metric.label}: recomputed ${String(now.value)}, pack says ${String(metric.value)}`,
      );
    } else if (before.value !== metric.previous || !sameRefs(before.refs, metric.previous_refs)) {
      problems.push(
        `${metric.label} (previous): recomputed ${String(before.value)}, pack says ${String(metric.previous)}`,
      );
    }
  }
  return check(problems, shared.pack.metrics.length);
}

function checkSummaries(shared: SharedPack, records: Located[]): Check {
  const values = new Map(
    shared.pack.metrics.map((m) => [
      m.id,
      {
        value: evaluate(m.spec, records, shared.pack.period, "current").value,
        previous: evaluate(m.spec, records, shared.pack.period, "previous").value,
        unit: m.unit,
      },
    ]),
  );
  const problems: string[] = [];
  let total = 0;
  for (const summary of shared.pack.summaries) {
    summary.template.forEach((template, i) => {
      total += 1;
      if (renderTemplate(template, values) !== summary.text[i]) {
        problems.push(`${summary.audience}: a sentence does not follow from the numbers`);
      }
    });
  }
  return check(problems, total);
}

/** Each rule's approvals are disclosed and its gate is what the latest one chained. */
function checkRules(shared: SharedPack): Check {
  const byKey = new Map(shared.pack.records.map((r) => [r.key, r.record]));
  const problems: string[] = [];
  for (const rule of shared.pack.rules) {
    const last = rule.approvals[rule.approvals.length - 1];
    const record = last ? byKey.get(last) : undefined;
    if (!record) {
      problems.push(`${rule.rule_id}: its approval record is not disclosed`);
      continue;
    }
    const gates = getPath(record, "payload.gates");
    const gate = Array.isArray(gates)
      ? (gates as Record<string, unknown>[]).find((g) => g.rule_id === rule.rule_id)
      : undefined;
    if ((gate ? String(gate.status) : "unknown") !== rule.gate) {
      problems.push(`${rule.rule_id}: the gate differs from the chained approval`);
    }
  }
  return check(problems, shared.pack.rules.length);
}

// Paths that would point a number at a person or a tracked individual (guardrail R13).
const PERSONAL = /subject|track|person|worker|approver|proposed_by|rejecter|identity|face/i;

/** No number is filtered or grouped by a person or a track: recipes use rule, camera, shift. */
export function checkNoPerPerson(shared: SharedPack): Check {
  const problems: string[] = [];
  for (const metric of shared.pack.metrics) {
    const paths = [
      ...Object.keys(metric.spec.where),
      ...Object.keys(metric.spec.prefix),
      ...metric.spec.to.flatMap((option) => Object.keys(option)),
    ];
    const personal = paths.filter((path) => PERSONAL.test(path));
    if (personal.length > 0) problems.push(`${metric.label}: filters on ${personal.join(", ")}`);
  }
  return check(problems, shared.pack.metrics.length);
}

/** Run every check that needs nothing but the pack itself. */
export async function verifyPack(shared: SharedPack, token: string | null): Promise<PackReport> {
  const recomputed = await sha256Hex(canonical(shared.pack));
  const hashProblems: string[] = [];
  if (recomputed !== shared.pack_hash)
    hashProblems.push("the pack's contents do not match its hash");
  const stated = token ? tokenHash(token) : null;
  if (token && stated !== shared.pack_hash) hashProblems.push("the link was made for another pack");
  const records = ordered(shared);
  const report = {
    packHash: check(hashProblems, 1),
    records: await checkRecords(shared),
    windows: checkWindows(shared),
    numbers: checkNumbers(shared, records),
    summaries: checkSummaries(shared, records),
    rules: checkRules(shared),
    perPerson: checkNoPerPerson(shared),
  };
  return { ...report, ok: Object.values(report).every((c) => c.ok) };
}

/** Compare a day's root with the site's live log (`GET /evidence/{day}/hashes`, public). */
export async function anchorDay(
  day: {
    day: string;
    n: number;
    root: string;
    committed_root: string | null;
    committed_n: number | null;
  },
  hashes: readonly string[] | null,
): Promise<DayAnchor> {
  if (hashes === null) {
    return {
      day: day.day,
      live: "unavailable",
      committed: day.committed_root ? "unavailable" : "none",
    };
  }
  const prefix = async (n: number, root: string): Promise<LiveStatus> => {
    if (n > hashes.length) return "mismatch";
    if ((await merkleRoot(hashes.slice(0, n))) !== root) return "mismatch";
    return n === hashes.length ? "match" : "extended";
  };
  return {
    day: day.day,
    live: await prefix(day.n, day.root),
    committed:
      day.committed_root && day.committed_n !== null
        ? await prefix(day.committed_n, day.committed_root)
        : "none",
  };
}
