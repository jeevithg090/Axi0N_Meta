"use client";

import React, { useState } from "react";

export function AuditSignage() {
  const [filter, setFilter] = useState<"all" | "rule" | "trip" | "disp">("all");
  const [verifying, setVerifying] = useState<boolean>(false);
  const [progress, setProgress] = useState<number>(100);

  const rows = [
    { n: "35", t: "14:28:49", what: "Event 8912-A interlock trip: 1.4 m < 2.0 m", who: "Hardware relay 01", hash: "4a9f…71b2", kind: "trip" },
    { n: "34", t: "14:20:00", what: "Operator seated warm-up: fork 09 linked", who: "ByteTrack runtime", hash: "8d1c…660a", kind: "rule" },
    { n: "33", t: "12:00:15", what: "Worker dispute filed: episode CK-012", who: "Anon #882 (voice note)", hash: "3e5b…90da", kind: "disp" },
    { n: "32", t: "10:15:30", what: "Plan approved: RULE-CK-012 staged", who: "Site Owner (key #01)", hash: "2f10…a48c", kind: "rule" },
    { n: "31", t: "09:44:12", what: "Rule CI verification green: 142/142 killed", who: "Automated test harness", hash: "99aa…4401", kind: "rule" },
    { n: "30", t: "08:30:00", what: "Daily genesis block: 2026-11-02", who: "System authority", hash: "0000…e481", kind: "rule" },
  ];

  const filtered = rows.filter((r) => {
    if (filter === "all") return true;
    if (filter === "rule") return r.kind === "rule";
    if (filter === "trip") return r.kind === "trip";
    if (filter === "disp") return r.kind === "disp";
    return true;
  });

  const runVerify = () => {
    setVerifying(true);
    setProgress(0);
    let p = 0;
    const interval = setInterval(() => {
      p += 20;
      if (p >= 100) {
        clearInterval(interval);
        setProgress(100);
        setVerifying(false);
      } else {
        setProgress(p);
      }
    }, 100);
  };

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s9">
            <div className="ctx">
              Audit log &nbsp;·&nbsp; every rule change, trip and dispute &nbsp;·&nbsp; <b>append only</b>
            </div>
            <h1 className="h1">
              35 blocks sealed. <span className="hi">Chain intact.</span>
            </h1>
          </div>
          <div className="s3 stack end">
            <button
              type="button"
              className="btn block"
              onClick={runVerify}
              disabled={verifying}
            >
              {verifying ? `Verifying… ${progress}%` : "Verify chain in browser"}
            </button>
            <div className="sweep" aria-hidden="true">
              <i style={{ width: `${progress}%`, transition: "width 0.1s ease" }} />
            </div>
            <p className="mono">Client WebAssembly SHA-256</p>
          </div>
        </div>
      </section>

      <div className="tape" />

      {/* Ledger Table */}
      <section className="wrap sec sec-last">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            flexWrap: "wrap",
            gap: "var(--s2)",
          }}
        >
          <h2 className="h3">Latest sealed records</h2>
          <div>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              All
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "rule"}
              onClick={() => setFilter("rule")}
            >
              Rule changes
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "trip"}
              onClick={() => setFilter("trip")}
            >
              Interlocks
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "disp"}
              onClick={() => setFilter("disp")}
            >
              Disputes
            </button>
          </div>
        </div>

        <div className="scroll-x">
          <div className="blk hd" style={{ minWidth: "760px" }}>
            <span>Block</span>
            <span>Time (UTC)</span>
            <span>Event</span>
            <span>Signed by</span>
            <span>Hash</span>
          </div>

          {filtered.map((r) => (
            <div key={r.n} className="blk">
              <span className="no">
                <i />
                {r.n}
              </span>
              <span className="dm">{r.t}</span>
              <span>{r.what}</span>
              <span className="small">{r.who}</span>
              <span className="dm small">{r.hash}</span>
            </div>
          ))}
        </div>

        <div className="grid" style={{ marginTop: "var(--s5)" }}>
          <p className="lead s6 rule">
            Each block holds the cryptographic hash of the block before it. If anyone alters a past
            entry, every subsequent hash stops matching, making any tampering immediately visible.
          </p>
          <p className="lead s6 rule">
            Disputes are permanently anchored exactly as the worker filed them, in their native
            language, with zero facial or personal identification attached.
          </p>
        </div>
      </section>
    </div>
  );
}
