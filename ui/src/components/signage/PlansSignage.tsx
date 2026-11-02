"use client";

import React, { useState } from "react";

export function PlansSignage() {
  const [openPlan, setOpenPlan] = useState<string>("plan-01");

  const plans = [
    {
      id: "plan-01",
      name: "Commercial Kitchen #104",
      site: "Main St Facility",
      status: "LIVE",
      rules: [
        { id: "RULE-CK-012", desc: "Handwashing 20s minimum before meat station", state: "Active" },
        { id: "RULE-CK-015", desc: "Hairnet and apron compliance in zone A", state: "Active" },
      ],
      hash: "7ad1f…e03c",
      updated: "2026-11-01 18:22 UTC",
    },
    {
      id: "plan-02",
      name: "Warehouse Dock A",
      site: "West Distribution Hub",
      status: "STAGED",
      rules: [
        { id: "RULE-WH-049", desc: "Keep people at least 2 m away from moving forklift", state: "Staged" },
        { id: "RULE-WH-018", desc: "Clear egress corridor in Bay 4", state: "Staged" },
      ],
      hash: "88bf2…11aa",
      updated: "2026-11-02 04:12 UTC",
    },
    {
      id: "plan-03",
      name: "Packaging Line North",
      site: "Plant 2",
      status: "ARCHIVED",
      rules: [
        { id: "RULE-PK-001", desc: "Emergency stop pull-cord unobstructed", state: "Archived" },
      ],
      hash: "11c99…ff40",
      updated: "2026-10-24 09:00 UTC",
    },
  ];

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s9">
            <div className="ctx">
              Site Plans &nbsp;·&nbsp; Version control &nbsp;·&nbsp; <b>Immutable AST</b>
            </div>
            <h1 className="h1">
              3 site plans on file. <span className="hi">1 is staged for release.</span>
            </h1>
          </div>
          <div className="s3 stack end">
            <a href="/planner" className="btn block" style={{ textAlign: "center" }}>
              + Create new site plan
            </a>
          </div>
        </div>
      </section>

      <div className="tape" />

      {/* Plans List */}
      <section className="wrap sec sec-last">
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s4)" }}>
          {plans.map((p) => {
            const isOpen = openPlan === p.id;
            return (
              <div
                key={p.id}
                style={{
                  borderTop: "4px solid var(--ink)",
                  paddingTop: "var(--s3)",
                  background: isOpen ? "var(--paper-2)" : "transparent",
                  padding: "var(--s3)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    cursor: "pointer",
                    flexWrap: "wrap",
                    gap: "var(--s2)",
                  }}
                  onClick={() => setOpenPlan(isOpen ? "" : p.id)}
                >
                  <div>
                    <span className="dm small">
                      {p.site} · SHA256 {p.hash}
                    </span>
                    <h2 className="h2" style={{ fontSize: "36px", marginTop: "4px" }}>
                      {p.name}
                    </h2>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
                    <span className={`state ${p.status === "LIVE" ? "ok" : ""}`}>{p.status}</span>
                    <button type="button" className="tab">
                      {isOpen ? "▲ Collapse" : "▼ Expand plan"}
                    </button>
                  </div>
                </div>

                {isOpen && (
                  <div style={{ marginTop: "var(--s4)", borderTop: "2px solid var(--ink)", paddingTop: "var(--s3)" }}>
                    <h3 className="h3" style={{ fontSize: "22px", marginBottom: "var(--s2)" }}>
                      Rules In This Plan
                    </h3>
                    <div className="scroll-x">
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Rule ID</th>
                            <th>Description</th>
                            <th>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {p.rules.map((r) => (
                            <tr key={r.id}>
                              <td><b>{r.id}</b></td>
                              <td>{r.desc}</td>
                              <td><span className="state ok">{r.state}</span></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    <div style={{ display: "flex", gap: "var(--s2)", marginTop: "var(--s4)", flexWrap: "wrap" }}>
                      <button type="button" className="btn">
                        Deploy this plan
                      </button>
                      <button type="button" className="btn o">
                        Export JSON AST
                      </button>
                      <button type="button" className="btn o">
                        View Rule CI run
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
