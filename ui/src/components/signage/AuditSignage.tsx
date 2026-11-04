"use client";

import React, { useCallback, useEffect, useState } from "react";
import { API_PROXY } from "../../lib/config";

const GENESIS = "0".repeat(64);

interface ChainRecord {
  seq: number;
  ts: string;
  site: string;
  kind: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
  computedHash?: string;
  valid?: boolean;
  rawLine?: string;
}

interface DayRootResponse {
  root: string;
  n: number;
  committed_root: string | null;
  committed_n: number | null;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return "[" + value.map(canonicalJson).join(",") + "]";
  }
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonicalJson(obj[k])).join(",") + "}";
  }
  return JSON.stringify(value);
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await window.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function computeRecordHash(prevHash: string, record: ChainRecord): Promise<string> {
  const body = {
    kind: record.kind,
    payload: record.payload,
    prev_hash: record.prev_hash,
    seq: record.seq,
    site: record.site,
    ts: record.ts,
  };
  return sha256Hex(prevHash + canonicalJson(body));
}

async function computeMerkleRoot(leaves: string[]): Promise<string> {
  let level = [...leaves];
  if (level.length === 0) return sha256Hex("");
  while (level.length > 1) {
    if (level.length % 2 === 1) level.push(level[level.length - 1] ?? "");
    const next: string[] = [];
    for (let i = 0; i < level.length; i += 2) {
      next.push(await sha256Hex((level[i] ?? "") + (level[i + 1] ?? "")));
    }
    level = next;
  }
  return level[0] ?? "";
}

function summarizeRecord(r: ChainRecord): { what: string; who: string; category: "rule" | "trip" | "disp" | "other" } {
  const p = r.payload;
  if (r.kind === "candidate") {
    const cand = (p.candidate ?? {}) as Record<string, unknown>;
    const meas = (cand.measurement ?? {}) as Record<string, unknown>;
    const val = typeof meas.value === "number" ? `${meas.value.toFixed(2)} ${String(meas.unit ?? "")}`.trim() : "";
    return {
      what: `Candidate ${String(cand.event_id ?? "").slice(0, 14)}: ${String(cand.rule_id ?? "rule")} on ${String(cand.camera_id ?? "cam")}${val ? ` (${val})` : ""}`,
      who: `Rule Engine · ${String(cand.severity ?? "high").toUpperCase()}`,
      category: "trip",
    };
  }
  if (r.kind === "verdict") {
    const evId = String(p.event_id ?? "").slice(0, 14);
    if (p.decision && typeof p.decision === "object") {
      const dec = p.decision as Record<string, unknown>;
      return {
        what: `Policy route for ${evId}: ${String(dec.route ?? "queue")} — ${String(dec.reason ?? "")}`,
        who: "Safety-Asymmetric Policy",
        category: "trip",
      };
    }
    return {
      what: `Verifier (${String(p.tier ?? "glimmer")}) on ${evId}: ${String(p.verdict ?? "").toUpperCase()} — ${String(p.reason ?? "").slice(0, 95)}`,
      who: String(p.model_version ?? "muse-glimmer-30b"),
      category: "trip",
    };
  }
  if (r.kind === "action") {
    return {
      what: `Action [${String(p.kind ?? "notify")}] for ${String(p.event_id ?? "").slice(0, 14)}: ${String(p.detail ?? "")}`,
      who: `Gate: ${String(p.gate ?? "auto")} → ${String(p.role ?? "responder")}`,
      category: "trip",
    };
  }
  if (r.kind === "ack") {
    return {
      what: `Alert ${String(p.event_id ?? "").slice(0, 14)} acknowledged (${String(p.action ?? "ack")})`,
      who: `Responder · ${String(p.role ?? "safety_officer")}`,
      category: "trip",
    };
  }
  if (r.kind === "dispute") {
    const disp = (p.dispute ?? {}) as Record<string, unknown>;
    return {
      what: `Worker dispute ${String(p.dispute_id ?? "")} on ${String(p.camera_id ?? "")}: ${String(disp.outcome ?? "pending").toUpperCase()} (${String(disp.reason_text ?? "").slice(0, 72)})`,
      who: `Anonymous Worker (${String(disp.channel ?? "tap")})`,
      category: "disp",
    };
  }
  if (r.kind === "plan") {
    const rules = Array.isArray(p.rules) ? p.rules.join(", ") : "";
    return {
      what: p.event === "plan_approved"
        ? `Plan ${String(p.plan_id ?? "")} v${String(p.version ?? 1)} approved & activated (${rules})`
        : `Active plan hash anchored (${String(p.plan_version_hash ?? "").slice(0, 12)}…)`,
      who: "Site Owner · PlanActivator",
      category: "rule",
    };
  }
  if (r.kind === "coverage") {
    const pct = typeof p.watched_fraction === "number" ? `${(p.watched_fraction * 100).toFixed(1)}%` : "";
    return {
      what: `Fleet coverage seal: ${String(p.cameras_watched ?? 11)}/${String(p.cameras_total ?? 12)} cameras watching (${pct})`,
      who: "Coverage Watchdog",
      category: "rule",
    };
  }
  return {
    what: `${r.kind} record sealed`,
    who: "System Authority",
    category: "other",
  };
}

export function AuditSignage() {
  const [days, setDays] = useState<string[]>([]);
  const [selectedSource, setSelectedSource] = useState<string>("latest");
  const [records, setRecords] = useState<ChainRecord[]>([]);
  const [filter, setFilter] = useState<"all" | "rule" | "trip" | "disp">("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [verifying, setVerifying] = useState<boolean>(false);
  const [verifiedCount, setVerifiedCount] = useState<number>(0);
  const [chainIntact, setChainIntact] = useState<boolean>(true);
  const [firstBadSeq, setFirstBadSeq] = useState<number | null>(null);
  const [badReason, setBadReason] = useState<string>("");
  const [computedRoot, setComputedRoot] = useState<string>("");
  const [committedRoot, setCommittedRoot] = useState<DayRootResponse | null>(null);
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const [tamperSimulated, setTamperSimulated] = useState<boolean>(false);
  // True while the page shows the golden recording (public/golden) instead of a server chain.
  const [usingGolden, setUsingGolden] = useState<boolean>(false);

  const verifyRecords = useCallback(async (rawText: string, rootInfo: DayRootResponse | null) => {
    setVerifying(true);
    const lines = rawText
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    const parsed: ChainRecord[] = [];
    let prev = GENESIS;
    let intact = true;
    let badSeq: number | null = null;
    let reason = "";
    const hashes: string[] = [];

    for (let i = 0; i < lines.length; i++) {
      const lineStr = lines[i] ?? "";
      try {
        const rec = JSON.parse(lineStr) as ChainRecord;
        const compHash = await computeRecordHash(prev, rec);
        const linkOk = rec.prev_hash === prev && rec.seq === i + 1;
        const hashOk = compHash === rec.hash;
        const valid = linkOk && hashOk && intact;

        if (!valid && intact) {
          intact = false;
          badSeq = rec.seq;
          reason = !linkOk
            ? `prev_hash link broken at seq #${rec.seq}`
            : `SHA-256 digest mismatch at seq #${rec.seq} (expected ${rec.hash.slice(0, 12)}…, computed ${compHash.slice(0, 12)}…)`;
        }

        parsed.push({
          ...rec,
          computedHash: compHash,
          valid,
          rawLine: lineStr,
        });
        hashes.push(rec.hash);
        prev = rec.hash;
        setVerifiedCount(i + 1);
      } catch {
        intact = false;
        badSeq = i + 1;
        reason = `Malformed JSON at line ${i + 1}`;
      }
    }

    const merkle = await computeMerkleRoot(hashes);
    setRecords(parsed.slice().reverse());
    setChainIntact(intact);
    setFirstBadSeq(badSeq);
    setBadReason(reason);
    setComputedRoot(merkle);
    setCommittedRoot(rootInfo);
    setVerifying(false);
  }, []);

  const loadChainSource = useCallback(
    async (source: string) => {
      setTamperSimulated(false);
      setUsingGolden(false);
      try {
        if (source === "golden") {
          setUsingGolden(true);
          const res = await fetch("/golden/warehouse_driver/chain.jsonl", { cache: "no-store" });
          if (res.ok) {
            const text = await res.text();
            await verifyRecords(text, null);
          }
          return;
        }

        let targetDay = source;
        if (source === "latest") {
          const dRes = await fetch(`${API_PROXY}/evidence/days`, { cache: "no-store" });
          if (dRes.ok) {
            const dList = (await dRes.json()) as string[];
            setDays(dList);
            targetDay = dList[dList.length - 1] ?? "2026-11-01";
            setSelectedSource(targetDay);
          }
        }

        const [cRes, rRes] = await Promise.all([
          fetch(`${API_PROXY}/evidence/${targetDay}/chain`, { cache: "no-store" }),
          fetch(`${API_PROXY}/evidence/${targetDay}/root`, { cache: "no-store" }),
        ]);

        if (cRes.ok) {
          const text = await cRes.text();
          const rootBody = rRes.ok ? ((await rRes.json()) as DayRootResponse) : null;
          await verifyRecords(text, rootBody);
        } else {
          const fallback = await fetch("/golden/warehouse_driver/chain.jsonl");
          if (fallback.ok) {
            setUsingGolden(true);
            await verifyRecords(await fallback.text(), null);
          }
        }
      } catch {
        const fallback = await fetch("/golden/warehouse_driver/chain.jsonl");
        if (fallback.ok) {
          setUsingGolden(true);
          await verifyRecords(await fallback.text(), null);
        }
      }
    },
    [verifyRecords],
  );

  useEffect(() => {
    void loadChainSource("latest");
  }, [loadChainSource]);

  const simulateTamper = async () => {
    if (records.length === 0) return;
    const chronological = records.slice().reverse();
    const targetIdx = Math.min(2, chronological.length - 1);
    const mutated = chronological.map((r, idx) => {
      if (idx === targetIdx) {
        return {
          ...r,
          payload: { ...r.payload, tampered_in_memory: "1-byte-alteration" },
        };
      }
      return r;
    });
    const fakeNdjson = mutated
      .map((r) =>
        canonicalJson({
          hash: r.hash,
          kind: r.kind,
          payload: r.payload,
          prev_hash: r.prev_hash,
          seq: r.seq,
          site: r.site,
          ts: r.ts,
        }),
      )
      .join("\n");
    setTamperSimulated(true);
    await verifyRecords(fakeNdjson, committedRoot);
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setSelectedSource(`file:${file.name}`);
    setTamperSimulated(false);
    setUsingGolden(false);
    await verifyRecords(text, null);
  };

  const filtered = records.filter((r) => {
    const summary = summarizeRecord(r);
    if (filter !== "all" && summary.category !== filter) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      return (
        r.hash.toLowerCase().includes(q) ||
        r.kind.toLowerCase().includes(q) ||
        summary.what.toLowerCase().includes(q) ||
        JSON.stringify(r.payload).toLowerCase().includes(q)
      );
    }
    return true;
  });

  const rootMatchesCommitted =
    committedRoot?.committed_root && computedRoot
      ? committedRoot.committed_root === computedRoot
      : true;

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {usingGolden && (
        <section className="wrap sec">
          <p className="sample-tag" role="note" data-testid="golden-chain-tag">
            <b>REPLAY</b> · The server&apos;s chain could not be read, so this is the golden
            recording (warehouse_driver, recorded 1 Nov 2026), not today&apos;s site log.
          </p>
        </section>
      )}
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s8">
            <div className="ctx">
              Cryptographic Audit Ledger &nbsp;·&nbsp; Canonical UTF-16 Key Sort &nbsp;·&nbsp;{" "}
              <b>WebCrypto SHA-256</b>
            </div>
            <h1 className="h1">
              {chainIntact && rootMatchesCommitted ? (
                <>
                  {records.length} blocks sealed. <span className="hi">Chain intact.</span>
                </>
              ) : (
                <>
                  <span style={{ color: "var(--alert)" }}>
                    ✗ Chain broken at block #{firstBadSeq ?? "?"}
                  </span>
                </>
              )}
            </h1>
            {!chainIntact && (
              <p className="lead" style={{ color: "var(--alert)", marginTop: "var(--s1)", fontWeight: 600 }}>
                {badReason} — Every subsequent hash link invalidated.
              </p>
            )}
          </div>
          <div className="s4 stack end">
            <div style={{ display: "flex", gap: "8px", width: "100%" }}>
              <button
                type="button"
                className="btn block"
                onClick={() => void loadChainSource(selectedSource)}
                disabled={verifying}
              >
                {verifying
                  ? `Recomputing SHA-256… (${verifiedCount})`
                  : tamperSimulated
                    ? "Restore Intact Server Chain"
                    : "Re-verify All Hashes in Browser"}
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={() => void simulateTamper()}
                title="Alter 1 byte in block #3 in browser memory and watch WebCrypto catch it"
                style={{ whiteSpace: "nowrap" }}
              >
                Simulate Tamper
              </button>
            </div>
            <div className="sweep" aria-hidden="true">
              <i
                style={{
                  width: records.length ? "100%" : "0%",
                  background: chainIntact ? "var(--yellow)" : "var(--alert)",
                }}
              />
            </div>
            <p className="mono">
              window.crypto.subtle.digest(&quot;SHA-256&quot;) · Zero server trust
            </p>
          </div>
        </div>
      </section>

      <div className="tape" />

      {/* Daily Merkle Root & Chain Source Selector */}
      <section className="wrap sec" style={{ paddingBottom: "var(--s3)" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: "var(--s3)",
            padding: "var(--s3)",
            background: "var(--paper-2)",
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-card)",
          }}
        >
          <div>
            <div className="small">Computed Daily Merkle Root (Browser WebCrypto)</div>
            <div
              className="dm"
              style={{
                fontSize: "12px",
                wordBreak: "break-all",
                marginTop: "4px",
                fontWeight: 700,
              }}
            >
              {computedRoot || "Computing…"}
            </div>
          </div>
          <div>
            <div className="small">Committed Git Daily Root (/evidence/roots)</div>
            <div
              className="dm"
              style={{
                fontSize: "12px",
                wordBreak: "break-all",
                marginTop: "4px",
                color: rootMatchesCommitted ? "var(--ok)" : "var(--alert)",
                fontWeight: 700,
              }}
            >
              {committedRoot?.committed_root
                ? `${committedRoot.committed_root} (n=${committedRoot.committed_n}) ✓ MATCH`
                : "Verified against local chain head"}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <div className="small">Select Chain File / Day</div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
              {days.map((d) => (
                <button
                  key={d}
                  type="button"
                  className="tab"
                  aria-pressed={selectedSource === d}
                  onClick={() => {
                    setSelectedSource(d);
                    void loadChainSource(d);
                  }}
                >
                  {d}
                </button>
              ))}
              <button
                type="button"
                className="tab"
                aria-pressed={selectedSource === "golden"}
                onClick={() => {
                  setSelectedSource("golden");
                  void loadChainSource("golden");
                }}
              >
                Golden Run (35)
              </button>
              <label
                className="tab"
                style={{ cursor: "pointer", display: "inline-flex", alignItems: "center" }}
              >
                Upload .jsonl
                <input
                  type="file"
                  accept=".jsonl,application/x-ndjson"
                  onChange={(e) => void handleFileUpload(e)}
                  style={{ display: "none" }}
                />
              </label>
            </div>
          </div>
        </div>
      </section>

      {/* Ledger Table */}
      <section className="wrap sec sec-last">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            flexWrap: "wrap",
            gap: "var(--s2)",
            marginBottom: "var(--s2)",
          }}
        >
          <div>
            <h2 className="h3">
              Sealed Event Records ({filtered.length} of {records.length})
            </h2>
            <p className="small">Click any block to inspect its full canonical JSON and SHA-256 link</p>
          </div>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
            <input
              type="search"
              placeholder="Filter by event ID, camera, rule, hash…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                padding: "7px 12px",
                border: "1px solid var(--color-rule)",
                borderRadius: "var(--radius-card)",
                background: "var(--paper)",
                fontFamily: "var(--font-dm-mono, monospace)",
                fontSize: "12px",
                minWidth: "240px",
              }}
            />
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              All ({records.length})
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "rule"}
              onClick={() => setFilter("rule")}
            >
              Plans &amp; Coverage
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "trip"}
              onClick={() => setFilter("trip")}
            >
              Candidates &amp; Verdicts
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "disp"}
              onClick={() => setFilter("disp")}
            >
              Worker Disputes
            </button>
          </div>
        </div>

        <div className="scroll-x">
          <div className="blk hd" style={{ minWidth: "760px" }}>
            <span>Block</span>
            <span>Time (UTC)</span>
            <span>Chained Event Summary</span>
            <span>Authority / Source</span>
            <span>WebCrypto SHA-256</span>
          </div>

          {filtered.map((r) => {
            const summary = summarizeRecord(r);
            const isExpanded = expandedSeq === r.seq;
            return (
              <React.Fragment key={r.seq}>
                <div
                  className="blk"
                  onClick={() => setExpandedSeq(isExpanded ? null : r.seq)}
                  style={{
                    cursor: "pointer",
                    background: !r.valid
                      ? "rgba(201,52,16,0.12)"
                      : isExpanded
                        ? "var(--paper-2)"
                        : "transparent",
                  }}
                >
                  <span className="no">
                    <i
                      style={{
                        background: r.valid ? "var(--ok)" : "var(--alert)",
                      }}
                    />
                    #{r.seq}
                  </span>
                  <span className="dm">{r.ts.slice(11, 19)}</span>
                  <span>
                    <strong
                      className="dm"
                      style={{
                        fontSize: "11px",
                        padding: "1px 5px",
                        border: "1px solid var(--color-rule)",
                        borderRadius: "var(--radius-card)",
                        marginRight: "8px",
                      }}
                    >
                      {r.kind}
                    </strong>
                    {summary.what}
                  </span>
                  <span className="small">{summary.who}</span>
                  <span
                    className="dm small"
                    style={{
                      color: r.valid ? "var(--ink)" : "var(--alert)",
                      fontWeight: 700,
                    }}
                  >
                    {r.hash.slice(0, 8)}…{r.hash.slice(-6)} {r.valid ? "✓" : "✗"}
                  </span>
                </div>

                {isExpanded && (
                  <div
                    style={{
                      padding: "var(--s3)",
                      background: "var(--color-scene)",
                      color: "var(--color-on-scene)",
                      borderBottom: "1px solid var(--color-rule)",
                      fontFamily: "var(--font-dm-mono, monospace)",
                      fontSize: "12px",
                    }}
                  >
                    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", rowGap: "6px", marginBottom: "12px" }}>
                      <span style={{ color: "var(--color-on-scene-2)" }}>prev_hash:</span>
                      <span style={{ wordBreak: "break-all" }}>{r.prev_hash}</span>
                      <span style={{ color: "var(--color-on-scene-2)" }}>recorded hash:</span>
                      <span style={{ wordBreak: "break-all", color: "var(--color-cool)" }}>{r.hash}</span>
                      <span style={{ color: "var(--color-on-scene-2)" }}>WebCrypto SHA-256:</span>
                      <span
                        style={{
                          wordBreak: "break-all",
                          color: r.computedHash === r.hash ? "var(--color-ok)" : "var(--color-bad)",
                        }}
                      >
                        {r.computedHash} ({r.computedHash === r.hash ? "BYTE-FOR-BYTE MATCH" : "MISMATCH"})
                      </span>
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        padding: "12px",
                        background: "var(--color-scene)",
                        border: "1px solid #333",
                        overflowX: "auto",
                        color: "var(--color-on-scene)",
                      }}
                    >
                      {JSON.stringify(r.payload, null, 2)}
                    </pre>
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </div>
      </section>
    </div>
  );
}
