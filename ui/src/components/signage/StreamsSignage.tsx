"use client";

import React, { useState } from "react";

export function StreamsSignage() {
  const [filter, setFilter] = useState<"all" | "ok" | "bad">("all");

  const cameras = [
    { id: "01", name: "CAM-01 Dock A East", where: "Loading Bay 1-3", rules: "2 active", lat: "32 ms", state: "OK", blind: false },
    { id: "02", name: "CAM-02 Dock A Central", where: "Loading Bay 4", rules: "1 active", lat: "38 ms", state: "OK", blind: false },
    { id: "03", name: "CAM-03 Aisle 4 South", where: "High-rack storage", rules: "3 active", lat: "41 ms", state: "OK", blind: false },
    { id: "04", name: "CAM-04 Dock A West", where: "Active Forklift zone", rules: "1 active", lat: "38 ms", state: "OK", blind: false },
    { id: "05", name: "CAM-05 Prep Kitchen A", where: "Handwash Sink 1", rules: "2 active", lat: "29 ms", state: "OK", blind: false },
    { id: "06", name: "CAM-06 Prep Kitchen B", where: "Raw Poultry Station", rules: "1 active", lat: "31 ms", state: "OK", blind: false },
    { id: "07", name: "CAM-07 Walk-in Cooler", where: "Cold storage entrance", rules: "Unchecked", lat: "Offline", state: "BLIND", blind: true },
    { id: "08", name: "CAM-08 Packaging Line 1", where: "Conveyor infeed", rules: "2 active", lat: "35 ms", state: "OK", blind: false },
    { id: "09", name: "CAM-09 Packaging Line 2", where: "Sealing station", rules: "2 active", lat: "36 ms", state: "OK", blind: false },
    { id: "10", name: "CAM-10 Waste Area North", where: "Compactor zone", rules: "1 active", lat: "44 ms", state: "OK", blind: false },
    { id: "11", name: "CAM-11 Receiving Bay", where: "Pallet staging", rules: "2 active", lat: "33 ms", state: "OK", blind: false },
    { id: "12", name: "CAM-12 South Fire Exit", where: "Emergency corridor", rules: "1 active", lat: "30 ms", state: "OK", blind: false },
  ];

  const filtered = cameras.filter((c) => {
    if (filter === "all") return true;
    if (filter === "ok") return !c.blind;
    if (filter === "bad") return c.blind;
    return true;
  });

  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="grid">
          <div className="s9">
            <div className="ctx">
              Camera fleet &nbsp;·&nbsp; 3 sites &nbsp;·&nbsp; <b>updated 14:30:12 UTC</b>
            </div>
            <h1 className="h1">
              11 of 12 cameras are watching.{" "}
              <span className="hi">Camera 07 is blind.</span>
            </h1>
          </div>
          <div className="s3 end">
            <p className="small">
              A blind camera is never shown as compliant. Its rules are marked unchecked until someone
              clears the lens.
            </p>
          </div>
        </div>
      </section>

      <div className="tape" />

      {/* Filter and Model Telemetry Bar */}
      <section className="wrap sec">
        <div className="grid" style={{ alignItems: "end" }}>
          <div className="s6">
            <span className="small">Show</span>{" "}
            <span>
              <button
                type="button"
                className="tab"
                aria-pressed={filter === "all"}
                onClick={() => setFilter("all")}
              >
                All 12
              </button>
              <button
                type="button"
                className="tab"
                aria-pressed={filter === "ok"}
                onClick={() => setFilter("ok")}
              >
                Watching 11
              </button>
              <button
                type="button"
                className="tab"
                aria-pressed={filter === "bad"}
                onClick={() => setFilter("bad")}
              >
                Blind 1
              </button>
            </span>
          </div>

          <div
            className="s6 mono"
            style={{ display: "flex", flexWrap: "wrap", gap: "var(--s1) var(--s4)", justifyContent: "flex-end" }}
          >
            <span>SAM 3.1 · 42 ms</span>
            <span>Glimmer verifier · 110 ms</span>
            <span>Relay · 8.4 ms</span>
          </div>
        </div>

        {/* Camera Cards Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: "var(--gap)",
            marginTop: "var(--s4)",
          }}
        >
          {filtered.map((c) => (
            <article
              key={c.id}
              style={{
                borderTop: c.blind ? "4px solid var(--alert)" : "4px solid var(--ink)",
                paddingTop: "var(--s2)",
              }}
            >
              <svg
                viewBox="0 0 320 180"
                style={{ display: "block", width: "100%", height: "auto", background: "var(--cam)" }}
                role="img"
                aria-label={c.name}
              >
                <rect width="320" height="180" fill="#15181A" />
                <g fill="none" stroke="#ECEAE3" strokeOpacity=".2">
                  <rect x="16" y="14" width="288" height="26" />
                  <rect x="16" y="150" width="288" height="16" />
                </g>
                <rect
                  x="60"
                  y="60"
                  width="200"
                  height="80"
                  fill="none"
                  stroke="#F2D100"
                  strokeOpacity=".5"
                  strokeDasharray="8 5"
                />

                {!c.blind ? (
                  <g>
                    <rect x="90" y="88" width="56" height="28" fill="none" stroke="#3DB8FF" strokeWidth="1.5" />
                    <circle cx="118" cy="102" r="5" fill="#3DB8FF" />
                  </g>
                ) : (
                  <g>
                    <rect width="320" height="180" fill="#2A1510" />
                    <path
                      d="M0 40L140 0M0 100L200 0M0 160L260 0M40 180L320 20M120 180L320 80M200 180L320 140"
                      stroke="#FF5A2B"
                      strokeOpacity=".45"
                      strokeWidth="6"
                    />
                    <rect x="40" y="70" width="240" height="40" fill="#0F1112" stroke="#FF5A2B" strokeWidth="2" />
                    <text
                      x="160"
                      y="96"
                      textAnchor="middle"
                      fontFamily="var(--font-barlow-condensed, sans-serif)"
                      fontWeight="700"
                      fontSize="20"
                      fill="#FF5A2B"
                    >
                      NO IMAGE · LENS BLOCKED
                    </text>
                  </g>
                )}
              </svg>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  gap: "var(--s1)",
                  margin: "var(--s2) 0 var(--s1)",
                }}
              >
                <h3 className="h3" style={{ fontSize: "24px" }}>
                  {c.name}
                </h3>
                <span className={`state ${c.blind ? "bad" : "ok"}`}>{c.state}</span>
              </div>

              <div className="small" style={{ marginBottom: "var(--s1)" }}>
                {c.where}
              </div>

              <div className="kv" style={{ borderTop: "1px solid var(--ink)", padding: "6px 0", fontSize: "14px" }}>
                <span>Rules</span>
                <b>{c.rules}</b>
              </div>
              <div className="kv" style={{ padding: "6px 0", fontSize: "14px" }}>
                <span>Latency</span>
                <b className="dm">{c.lat}</b>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
