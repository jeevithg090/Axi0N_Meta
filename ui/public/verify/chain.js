// Browser (and Node 20+) re-implementation of rulecam.evidence.chain and .merkle.
// It recomputes every hash with Web Crypto, so anyone can check a chain without trusting our
// server. It must match Python byte for byte; tests/evidence/test_verify_page.py and
// chain.test.js check that against the same fixture.

const GENESIS = "0".repeat(64);
const FIRST_SEQ = 1;
// The fields of contracts.EventRecord, sorted; Python rejects any other shape.
const RECORD_KEYS = "hash,kind,payload,prev_hash,seq,site,ts";
const EVENT_KINDS = new Set([
  "candidate", "verdict", "action", "ack", "dispute", "label", "plan", "rule_change",
  "coverage", "advice", "measurement_plan", "sealed_cause", "surprise", "tamper_attempt",
]);
// The only timestamp shape Python's utc_iso writes (rulecam.evidence.chain._TS_PATTERN).
const TS_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$/;
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

/**
 * Canonical JSON: sorted keys, no whitespace; numbers and strings as JSON.stringify writes them.
 * Throws, like Python's canonical_json, on lone surrogates (TextEncoder would silently replace
 * them). Non-finite numbers and unsafe integers need no check: they never re-serialise to the
 * text they were parsed from, so the canonical-form comparison already rejects them.
 */
export function canonical(value) {
  if (Array.isArray(value)) {
    return "[" + value.map(canonical).join(",") + "]";
  }
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return "{" + keys.map((k) => canonical(k) + ":" + canonical(value[k])).join(",") + "}";
  }
  if (typeof value === "string" && LONE_SURROGATE.test(value)) {
    throw new RangeError("lone surrogate in a chain record");
  }
  return JSON.stringify(value);
}

/** A real UTC instant in utc_iso's shape (Python's datetime parse rejects e.g. day 31 of April). */
function isTimestamp(ts) {
  if (typeof ts !== "string" || !TS_PATTERN.test(ts)) return false;
  const parsed = new Date(ts.slice(0, 19) + "Z");
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 19) === ts.slice(0, 19);
}

function isRecord(data) {
  return (
    data !== null &&
    typeof data === "object" &&
    !Array.isArray(data) &&
    Object.keys(data).sort().join(",") === RECORD_KEYS &&
    Number.isSafeInteger(data.seq) &&
    EVENT_KINDS.has(data.kind) &&
    isTimestamp(data.ts) &&
    typeof data.site === "string" &&
    data.payload !== null &&
    typeof data.payload === "object" &&
    !Array.isArray(data.payload) &&
    typeof data.prev_hash === "string" &&
    typeof data.hash === "string"
  );
}

function isCanonical(data, raw) {
  try {
    return canonical(data) === raw;
  } catch {
    return false;
  }
}

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** hash = sha256(prev_hash + canonical(record without "hash")). */
export async function recordHash(prevHash, body) {
  return sha256Hex(prevHash + canonical(body));
}

function watchedFraction(records) {
  for (let i = records.length - 1; i >= 0; i -= 1) {
    const value = records[i].payload && records[i].payload.watched_fraction;
    if (records[i].kind === "coverage" && typeof value === "number") {
      return value;
    }
  }
  return null;
}

async function checkLine(raw, expectedSeq, prevHash) {
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return { problem: "not a valid record", hash: "" };
  }
  if (!isRecord(data)) return { problem: "not a valid record", hash: "" };
  if (!isCanonical(data, raw)) return { problem: "not in canonical form", hash: data.hash };
  if (data.seq !== expectedSeq) return { problem: `seq ${data.seq} out of order`, hash: data.hash };
  if (data.prev_hash !== prevHash) {
    return { problem: "prev_hash does not link to the previous record", hash: data.hash };
  }
  const { hash, ...body } = data;
  if ((await recordHash(prevHash, body)) !== hash) {
    return { problem: "hash does not match contents", hash };
  }
  return { problem: null, hash, record: data };
}

/** Verify a chain file's text. Mirrors verify_chain() in Python. */
export async function verifyChainText(text) {
  const lines = text.split("\n");
  const endsWithNewline = lines[lines.length - 1] === "";
  if (endsWithNewline) lines.pop();
  const records = [];
  let prev = GENESIS;
  for (let i = 0; i < lines.length; i += 1) {
    const seq = FIRST_SEQ + i;
    const result = await checkLine(lines[i], seq, prev);
    let problem = result.problem;
    if (problem === null && i === lines.length - 1 && !endsWithNewline) {
      problem = "file does not end with a newline";
    }
    if (problem !== null) {
      return { ok: false, firstBadSeq: seq, n: i + 1, reason: problem, watched: null, hashes: [] };
    }
    records.push(result.record);
    prev = result.hash;
  }
  return {
    ok: true,
    firstBadSeq: null,
    n: lines.length,
    reason: "",
    watched: watchedFraction(records),
    hashes: records.map((r) => r.hash),
  };
}

/** Merkle root over hex leaves; mirrors rulecam.evidence.merkle.merkle_root. */
export async function merkleRoot(leaves) {
  let level = [...leaves];
  if (level.length === 0) return sha256Hex("");
  while (level.length > 1) {
    if (level.length % 2) level.push(level[level.length - 1]);
    const next = [];
    for (let i = 0; i < level.length; i += 2) next.push(await sha256Hex(level[i] + level[i + 1]));
    level = next;
  }
  return level[0];
}

/**
 * Compare a verified chain with the root committed to git for its day.
 *
 * The committed root covers the first `committedN` records; a chain may have grown since, but
 * those records must reproduce it exactly. Returns "match" (same records), "extended" (the
 * committed prefix matches and records were appended after it) or "mismatch" (records dropped,
 * changed or the chain rewritten).
 */
export async function compareToCommitted(hashes, committedRoot, committedN) {
  if (!Number.isSafeInteger(committedN) || committedN < 0 || committedN > hashes.length) {
    return "mismatch";
  }
  if ((await merkleRoot(hashes.slice(0, committedN))) !== committedRoot) return "mismatch";
  return committedN === hashes.length ? "match" : "extended";
}

const HEX64 = /^[0-9a-f]{64}$/;

/**
 * Validate the public `GET /evidence/{day}/hashes` body ({n, hashes}) and return the hashes.
 * The public sees record hashes only (the records are the site owner's); they are enough to
 * check that the day's log reproduces the Merkle root committed to git.
 */
export function parseHashList(body) {
  const hashes = body && Array.isArray(body.hashes) ? body.hashes : null;
  if (hashes === null || body.n !== hashes.length || !hashes.every((h) => HEX64.test(h))) {
    throw new Error("the server sent a malformed hash list");
  }
  return hashes;
}
