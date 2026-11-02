"use client";

import React, { useState } from "react";

export function IncidentsSignage() {
  const [selectedId, setSelectedId] = useState<string>("8912");

  const incidents = [
    {
      id: "8912",
      time: "14:28:49",
      where: "Dock A, Bay 4 · Cam 04",
      title: "Worker within 1.4 m of moving forklift",
      headline: "Worker #204 entered the exclusion zone. Truck stopped in 8 ms.",
      status: "NEEDS SIGNATURE",
      stCls: "bad",
      rule: "RULE-WH-049",
      tier1: "Glimmer confirmed: pedestrian inside 2.0 m polygon while vehicle in gear.",
    },
    {
      id: "8911",
      time: "11:04:12",
      where: "Kitchen Prep · Cam 01",
      title: "Handwash duration 14.2 s (20.0 s req)",
      headline: "Handwash deficit before raw meat handling. Slicer interlock activated.",
      status: "NEEDS SIGNATURE",
      stCls: "bad",
      rule: "RULE-CK-012",
      tier1: "Glimmer confirmed: sink activity ceased after 14.2s without glove donning.",
    },
    {
      id: "8910",
      time: "08:15:30",
      where: "Receiving Bay · Cam 11",
      title: "Pallet left in emergency egress corridor",
      headline: "Corridor blocked > 60 seconds. Warning broadcast to speaker 02.",
      status: "RESOLVED",
      stCls: "ok",
      rule: "RULE-WH-018",
      tier1: "Corridor cleared by crew within 38 seconds.",
    },
  ];

  const current = incidents.find((i) => i.id === selectedId) ?? incidents[0]!;

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s9">
            <div className="ctx">
              Incident queue &nbsp;·&nbsp; today &nbsp;·&nbsp; <b>2 need a person</b>
            </div>
            <h1 className="h1">
              2 incidents are waiting. <span className="hi">Nothing is punished</span> until a person
              signs.
            </h1>
          </div>
        </div>
      </section>

      <div className="tape" />

      {/* Main Split Queue & Review View */}
      <section className="wrap sec sec-last">
        <div className="grid">
          {/* Queue List (4 cols) */}
          <div className="s4" style={{ padding: 0 }}>
            <div style={{ borderTop: "2px solid var(--ink)" }}>
              {incidents.map((it) => {
                const isSel = it.id === selectedId;
                return (
                  <button
                    key={it.id}
                    type="button"
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      background: isSel ? "var(--paper-2)" : "transparent",
                      color: "var(--ink)",
                      border: 0,
                      borderBottom: "1px solid var(--ink)",
                      borderLeft: isSel ? "6px solid var(--ink)" : "6px solid transparent",
                      padding: "var(--s2)",
                      cursor: "pointer",
                    }}
                    onClick={() => setSelectedId(it.id)}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--s1)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", color: "var(--ink-2)" }}>
                      <span>{it.time} · #{it.id}</span>
                      <span className={`state ${it.stCls}`}>{it.status}</span>
                    </div>
                    <div style={{ fontSize: "17px", fontWeight: 700, margin: "6px 0", lineHeight: 1.25 }}>
                      {it.title}
                    </div>
                    <div className="small">{it.where}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Selected Incident Detail (8 cols) */}
          <div className="s8" style={{ minWidth: 0 }}>
            <div className="ctx">
              #{current.id} &nbsp;·&nbsp; {current.where} &nbsp;·&nbsp; <b>{current.time} UTC</b>
            </div>
            <h2 className="h2" style={{ marginBottom: "var(--s4)" }}>
              {current.headline}
            </h2>

            {/* 3 Evidence Perception Frames */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                gap: "var(--s3)",
                marginBottom: "var(--s4)",
              }}
            >
              {[1, 2, 3].map((f) => (
                <figure
                  key={f}
                  style={{
                    margin: 0,
                    background: "var(--cam)",
                    border: "1px solid var(--rule-2)",
                  }}
                >
                  <svg viewBox="0 0 320 180" style={{ display: "block", width: "100%" }}>
                    <rect width="320" height="180" fill="#15181A" />
                    <rect x="30" y="30" width="260" height="120" fill="none" stroke="#F2D100" strokeDasharray="4 4" />
                    <text x="40" y="60" fill="#F2D100" fontFamily="var(--font-dm-mono, monospace)" fontSize="11">
                      FRAME #{f} · T -{0.4 * (4 - f)}s
                    </text>
                    <circle cx={80 + f * 50} cy={100} r="18" fill="none" stroke="#FF5A2B" strokeWidth="2" />
                  </svg>
                  <p style={{ margin: 0, padding: "8px 12px", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "12px", color: "var(--on-dark-2)", borderTop: "1px solid #2B3033" }}>
                    SAM track #{f * 7} · conf 0.96
                  </p>
                </figure>
              ))}
            </div>

            <div className="rule4" style={{ marginBottom: "var(--s4)" }}>
              <div className="small">Glimmer 2.0 Verifier Reasoning</div>
              <p className="lead" style={{ marginTop: "var(--s1)" }}>
                {current.tier1}
              </p>
              <div className="kv" style={{ marginTop: "var(--s2)" }}>
                <span>Rule Enforced</span>
                <b>{current.rule}</b>
              </div>
            </div>

            {/* Action Bar */}
            <div style={{ display: "flex", gap: "var(--s2)", flexWrap: "wrap" }}>
              <button type="button" className="btn alert">
                Uphold incident & log
              </button>
              <button type="button" className="btn o">
                Dismiss as false alarm
              </button>
              <button type="button" className="btn o">
                Forward to site owner
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
