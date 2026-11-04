// The recipient's browser recomputes a proof pack here. It mirrors, rule for rule:
// - canonical JSON and record hashes: src/rulecam/evidence/verify_page/chain.js (the verify page);
// - Merkle inclusion proofs: src/rulecam/compliance/proofs.py;
// - every number's recipe: src/rulecam/compliance/engine.py (see its docstring for the semantics);
// - summary templates: src/rulecam/compliance/narrate.py (`format_value`, `render`).
// engine.test.ts checks this file against a pack the Python builder wrote from a recorded chain.

import type { MetricSpec, Period, Scalar } from "./types";

export type JsonRecord = Record<string, unknown>;

export interface Located {
  key: string;
  day: string;
  record: JsonRecord;
}

const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

/** Canonical JSON (sorted keys, no whitespace, JSON.stringify numbers): the verify page's. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    return "{" + keys.map((k) => canonical(k) + ":" + canonical(obj[k])).join(",") + "}";
  }
  if (typeof value === "string" && LONE_SURROGATE.test(value)) {
    throw new RangeError("lone surrogate in a chain record");
  }
  return JSON.stringify(value);
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** hash = sha256(prev_hash + canonical(record without "hash")). */
export async function recordHash(record: JsonRecord): Promise<string> {
  const { hash: _ignored, ...body } = record;
  void _ignored;
  return sha256Hex(String(record.prev_hash) + canonical(body));
}

/** Fold a Merkle inclusion proof from a leaf up to the root ([sibling, "L" | "R"] steps). */
export async function fold(leaf: string, proof: ReadonlyArray<readonly [string, string]>) {
  let node = leaf;
  for (const [sibling, side] of proof) {
    node = side === "L" ? await sha256Hex(sibling + node) : await sha256Hex(node + sibling);
  }
  return node;
}

/** Merkle root over hex leaves; mirrors rulecam.evidence.merkle.merkle_root. */
export async function merkleRoot(leaves: readonly string[]): Promise<string> {
  let level = [...leaves];
  if (level.length === 0) return sha256Hex("");
  while (level.length > 1) {
    if (level.length % 2) level.push(level[level.length - 1] as string);
    const next: string[] = [];
    for (let i = 0; i < level.length; i += 2) {
      next.push(await sha256Hex((level[i] as string) + (level[i + 1] as string)));
    }
    level = next;
  }
  return level[0] as string;
}

// --- the recipes ---------------------------------------------------------------------------

/** Integer microseconds since the epoch of `YYYY-MM-DDTHH:MM:SS[.ffffff](Z|+00:00)`. */
export function micros(ts: string): number {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(ts);
  if (!match) throw new RangeError(`not a timestamp: ${ts}`);
  const [, y, mo, d, h, mi, s, frac = "", zone = "Z"] = match;
  let ms = Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s));
  if (zone !== "Z") {
    const sign = zone.startsWith("-") ? -1 : 1;
    const [zh, zm] = zone.slice(1).split(":").map(Number);
    ms -= sign * ((zh ?? 0) * 60 + (zm ?? 0)) * 60_000;
  }
  return ms * 1000 + Number(frac.padEnd(6, "0"));
}

export function getPath(record: unknown, path: string): unknown {
  let node: unknown = record;
  for (const part of path.split(".")) {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return null;
    const obj = node as Record<string, unknown>;
    if (!(part in obj)) return null;
    node = obj[part];
  }
  return node === undefined ? null : node;
}

export function eventOf(record: JsonRecord): string | null {
  let found = getPath(record, "payload.candidate.event_id");
  if (found === null) found = getPath(record, "payload.event_id");
  return typeof found === "string" ? found : null;
}

export function matches(
  record: JsonRecord,
  where: Record<string, Scalar>,
  prefix: Record<string, string> = {},
): boolean {
  for (const [path, wanted] of Object.entries(where)) {
    if (getPath(record, path) !== wanted) return false;
  }
  for (const [path, start] of Object.entries(prefix)) {
    const value = getPath(record, path);
    if (typeof value !== "string" || !value.startsWith(start)) return false;
  }
  return true;
}

export function shiftOf(ts: string, period: Period): string | null {
  if (period.shifts.length === 0) return null;
  const date = new Date(Math.floor(micros(ts) / 1000));
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: period.timezone,
      hour: "2-digit",
      hourCycle: "h23",
    }).format(date),
  );
  const ordered = [...period.shifts].sort((a, b) => a.start_hour - b.start_hour);
  let chosen = ordered[ordered.length - 1];
  for (const shift of ordered) if (shift.start_hour <= hour) chosen = shift;
  return chosen ? chosen.name : null;
}

class Index {
  readonly candidates = new Map<string, Located>();
  constructor(readonly records: readonly Located[]) {
    for (const item of records) {
      if (item.record.kind !== "candidate") continue;
      const event = eventOf(item.record);
      if (event !== null && !this.candidates.has(event)) this.candidates.set(event, item);
    }
  }
  candidate(item: Located): Located | null {
    const event = eventOf(item.record);
    return event === null ? null : (this.candidates.get(event) ?? null);
  }
  anchor(item: Located): string {
    return String((this.candidate(item) ?? item).record.ts ?? "");
  }
}

export type WindowName = "current" | "previous";

function bounds(period: Period, window: WindowName): [number, number] {
  return window === "current"
    ? [micros(period.start), micros(period.end)]
    : [micros(period.previous_start), micros(period.previous_end)];
}

function scoped(
  item: Located,
  spec: MetricSpec,
  index: Index,
  period: Period,
  b: [number, number],
) {
  const anchor = index.anchor(item);
  if (!anchor) return false;
  const at = micros(anchor);
  if (!(b[0] <= at && at < b[1])) return false;
  const source = index.candidate(item)?.record ?? null;
  if (spec.rule !== null && getPath(source ?? {}, "payload.candidate.rule_id") !== spec.rule) {
    return false;
  }
  if (spec.camera !== null) {
    let camera = getPath(source ?? item.record, "payload.candidate.camera_id");
    if (camera === null) camera = getPath(item.record, "payload.camera_id");
    if (camera !== spec.camera) return false;
  }
  return spec.shift === null || shiftOf(anchor, period) === spec.shift;
}

export interface Result {
  value: number | null;
  refs: string[];
}

function count(spec: MetricSpec, index: Index, period: Period, window: WindowName): Result {
  const b = bounds(period, window);
  const hits = index.records
    .filter((item) => matches(item.record, spec.where, spec.prefix))
    .filter((item) => scoped(item, spec, index, period, b))
    .map((item) => item.key);
  return { value: hits.length, refs: hits.sort() };
}

function summarise(op: string, values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  let chosen: number;
  if (op === "median_s") chosen = ordered[Math.floor((ordered.length - 1) / 2)] as number;
  else if (op === "p90_s")
    chosen = ordered[Math.max(Math.ceil(0.9 * ordered.length) - 1, 0)] as number;
  else chosen = ordered[ordered.length - 1] as number;
  return chosen / 1_000_000;
}

function durations(spec: MetricSpec, index: Index, period: Period, window: WindowName): Result {
  const b = bounds(period, window);
  const where = Object.keys(spec.where).length ? spec.where : { kind: "candidate" };
  const values: number[] = [];
  const refs = new Set<string>();
  index.records.forEach((item, position) => {
    if (!matches(item.record, where, spec.prefix)) return;
    if (!scoped(item, spec, index, period, b)) return;
    const event = eventOf(item.record);
    if (event === null) return;
    const end = index.records
      .slice(position + 1)
      .find(
        (later) =>
          eventOf(later.record) === event &&
          spec.to.some((option) => matches(later.record, option)),
      );
    if (!end) return;
    values.push(micros(String(end.record.ts)) - micros(String(item.record.ts)));
    refs.add(item.key);
    refs.add(end.key);
  });
  return { value: summarise(spec.op, values), refs: [...refs].sort() };
}

function stateSeconds(spec: MetricSpec, index: Index, period: Period, window: WindowName): Result {
  const [start, end] = bounds(period, window);
  const changes = index.records
    .filter(
      (item) =>
        item.record.kind === "coverage" &&
        getPath(item.record, "payload.camera_id") === spec.camera,
    )
    .map((item) => {
      const ts = getPath(item.record, "payload.ts") ?? item.record.ts;
      return [micros(String(ts)), item] as const;
    })
    .sort((a, b) => a[0] - b[0]);
  let total = 0;
  const refs: string[] = [];
  changes.forEach(([at, item], position) => {
    const next = changes[position + 1];
    const until = next ? next[0] : end;
    const lo = Math.max(at, start);
    const hi = Math.min(until, end);
    if (hi <= lo) return;
    refs.push(item.key);
    if (getPath(item.record, "payload.state") === spec.state) total += hi - lo;
  });
  return { value: total / 1_000_000, refs: refs.sort() };
}

function twoKey(index: Index, period: Period, window: WindowName): Result {
  const b = bounds(period, window);
  const approvals = new Map<string, Set<string>>();
  const bad: string[] = [];
  for (const item of index.records) {
    const record = item.record;
    if (record.kind !== "rule_change") continue;
    const proposal = getPath(record, "payload.proposal_id");
    const action = getPath(record, "payload.action");
    if (typeof proposal !== "string") continue;
    if (action === "setting_approval") {
      const role = getPath(record, "payload.role");
      if (typeof role === "string") {
        const held = approvals.get(proposal) ?? new Set<string>();
        held.add(role);
        approvals.set(proposal, held);
      }
    } else if (action === "setting_applied") {
      const at = micros(String(record.ts));
      if (b[0] <= at && at < b[1]) {
        const held = approvals.get(proposal) ?? new Set<string>();
        if (!(held.has("owner") && held.has("crew_representative"))) bad.push(item.key);
      }
    }
  }
  return { value: bad.length, refs: bad.sort() };
}

/** Compute one number from records in chain order (mirrors engine.evaluate). */
export function evaluate(
  spec: MetricSpec,
  records: readonly Located[],
  period: Period,
  window: WindowName = "current",
): Result {
  const index = new Index(records);
  if (spec.op === "count") return count(spec, index, period, window);
  if (spec.op === "median_s" || spec.op === "p90_s" || spec.op === "max_s") {
    return durations(spec, index, period, window);
  }
  if (spec.op === "state_s") return stateSeconds(spec, index, period, window);
  return twoKey(index, period, window);
}

// --- summaries --------------------------------------------------------------------------------

export function formatValue(value: number | null, unit: string): string {
  if (value === null) return "n/a";
  if (unit === "seconds") {
    if (value < 120) return `${Math.floor(value + 0.5)} s`;
    return `${Math.floor(value / 60 + 0.5)} min`;
  }
  return String(Math.trunc(value));
}

const PLACEHOLDER = /\{([A-Za-z0-9_:.-]+?)(\.previous)?\}/g;

/** Fill a summary template from metric values (null when it names an unknown metric). */
export function renderTemplate(
  template: string,
  metrics: ReadonlyMap<string, { value: number | null; previous: number | null; unit: string }>,
): string | null {
  let unknown = false;
  const text = template.replace(PLACEHOLDER, (_all, id: string, previous?: string) => {
    const metric = metrics.get(id);
    if (!metric) {
      unknown = true;
      return "";
    }
    return formatValue(previous ? metric.previous : metric.value, metric.unit);
  });
  if (/\d/.test(template.replace(PLACEHOLDER, ""))) return null;
  return unknown ? null : text;
}
