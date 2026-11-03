"use client";

import React, { useState } from "react";
import Link from "next/link";

export function MonitorSignage() {
  const [t, setT] = useState<number>(-0.64);
  const [acked, setAcked] = useState<boolean>(false);
  const [activeCam, setActiveCam] = useState<string>("cam-04");

  // Dynamic values driven by timeline scrubber (-1.25s to +0.25s)
  const norm = (t - (-1.25)) / (0.25 - (-1.25)); // 0 to 1
  const px = 60 + norm * 800; // 60 to 860

  const dist = t < 0 ? Math.max(1.1, 1.1 + Math.pow(t / 0.7, 2) * 1.5) : 1.1 + t * 0.4;
  const dNum = dist.toFixed(1);
  const dText = `${dNum} m`;
  const isBreach = dist <= 2.0;

  const wx = 520 + dist * 130;
  const mid = (520 + wx) / 2;
  const wc = isBreach ? "var(--alert, #C93410)" : "var(--machine, #0B62A8)";
  const py = 262 - (dist / 4.0) * 180;

  const ttc = dist <= 2.0 ? `${((dist - 0.5) / 2.4).toFixed(2)} s` : "Clear";
  const ilText = dist <= 2.0 ? "TRIPPED · 8 ms" : "Arm / Live";
  const ilCol = dist <= 2.0 ? "var(--alert)" : "var(--machine)";

  return (
    <div style={{ padding: "var(--s3) var(--gut) var(--s6)" }}>
      {/* Dashboard Cockpit Control Strip */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "16px",
          padding: "14px 18px",
          background: "var(--card)",
          border: "2px solid var(--ink)",
          marginBottom: "var(--s3)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
          <span className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
            Camera Stream:
          </span>
          <div style={{ display: "flex", gap: "4px" }}>
            <button
              type="button"
              className="tab"
              aria-pressed={activeCam === "cam-04"}
              onClick={() => setActiveCam("cam-04")}
              style={{ minHeight: "36px", padding: "0 10px" }}
            >
              CAM-04 · Dock A West
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={activeCam === "cam-01"}
              onClick={() => setActiveCam("cam-01")}
              style={{ minHeight: "36px", padding: "0 10px" }}
            >
              CAM-01 · Kitchen Prep
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={activeCam === "cam-07"}
              onClick={() => setActiveCam("cam-07")}
              style={{ minHeight: "36px", padding: "0 10px", color: "var(--alert)" }}
            >
              CAM-07 · Blind
            </button>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <span className="state bad">EVENT #8912-A</span>
          <span className="dm small">14:28:49 UTC</span>
          <button
            type="button"
            className={`btn ${acked ? "o" : ""}`}
            style={{ minHeight: "40px", padding: "8px 18px", fontSize: "14px" }}
            onClick={() => setAcked(!acked)}
          >
            {acked ? "✓ Acknowledged" : "Acknowledge"}
          </button>
          <Link
            href="/audit"
            className="btn o"
            style={{ minHeight: "40px", padding: "8px 18px", fontSize: "14px" }}
          >
            Evidence Pack
          </Link>
        </div>
      </div>

      {/* Main 2-Column Dashboard Grid */}
      <div className="grid">
        {/* Left Column: 8 Cols (Hero Visual Monitor + Scrubber) */}
        <div className="s8" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          {/* Panel: Camera Overhead Visualization */}
          <div style={{ border: "2px solid var(--ink)", background: "var(--cam)" }}>
            <div className="camhud">
              <span>CAM-04 Dock A west · 3840×2160 · 60 fps · 38 ms</span>
              <span style={{ color: wc }}>
                {isBreach ? "BREACH ACTIVE · EXCLUSION 2.0 m" : "CLEAR"} · T{" "}
                {t > 0 ? `+${t.toFixed(2)}` : t.toFixed(2)} s
              </span>
            </div>

            <svg
              viewBox="0 0 1440 520"
              style={{ display: "block", width: "100%", height: "auto" }}
              role="img"
              aria-label="Overhead plan of the dock"
            >
              <defs>
                <pattern id="hy" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
                  <rect width="6" height="12" fill="#F2D100" fillOpacity=".22" />
                </pattern>
                <pattern id="hb" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="4" height="10" fill="#3DB8FF" fillOpacity=".3" />
                </pattern>
                <pattern id="gr" width="100" height="100" patternUnits="userSpaceOnUse">
                  <path d="M100 0H0V100" fill="none" stroke="#fff" strokeOpacity=".05" />
                </pattern>
              </defs>

              <rect width="1440" height="520" fill="#15181A" />
              <rect width="1440" height="520" fill="url(#gr)" />

              <g fill="none" stroke="#fff" strokeOpacity=".16">
                <rect x="48" y="24" width="1344" height="60" />
                <path d="M384 24V84M720 24V84M1056 24V84" />
                <rect x="48" y="456" width="1344" height="40" />
              </g>

              <rect
                x="160"
                y="150"
                width="1130"
                height="290"
                fill="none"
                stroke="#F2D100"
                strokeOpacity=".45"
                strokeWidth="3"
                strokeDasharray="22 12"
              />

              {/* Exclusion zone */}
              <polygon
                points="520,262 780,180 780,420 520,338"
                fill="url(#hy)"
                stroke="#F2D100"
                strokeWidth="2"
                strokeDasharray="8 6"
              />
              <text
                x="520"
                y="445"
                fill="#F2D100"
                fontFamily="var(--font-barlow-condensed, sans-serif)"
                fontWeight="600"
                fontSize="20"
                letterSpacing="1"
              >
                EXCLUSION ZONE 2.0 m
              </text>

              {/* Forklift chassis */}
              <rect x="300" y="250" width="200" height="100" fill="url(#hb)" stroke="#3DB8FF" strokeWidth="2" />
              <rect x="300" y="250" width="66" height="100" fill="#15181A" stroke="#3DB8FF" strokeWidth="2" />
              <path d="M500 262H520M500 338H520" stroke="#3DB8FF" strokeWidth="5" />
              <path d="M360 250V214" stroke="#ECEAE3" />
              <text
                x="360"
                y="204"
                textAnchor="middle"
                fill="#ECEAE3"
                fontFamily="var(--font-barlow, sans-serif)"
                fontWeight="600"
                fontSize="16"
              >
                Forklift #09 · 2.4 m/s
              </text>

              {/* Pedestrian */}
              <g transform={`translate(${wx}, 300)`}>
                <ellipse rx="10" ry="22" fill={wc} fillOpacity=".3" stroke={wc} strokeWidth="2" />
                <circle r="9" fill={wc} />
                <circle r="44" fill="none" stroke={wc} strokeWidth="2" strokeDasharray="3 6" />
                <path d="M0 44V70" stroke="#ECEAE3" />
                <text
                  x="0"
                  y="90"
                  textAnchor="middle"
                  fill="#ECEAE3"
                  fontFamily="var(--font-barlow, sans-serif)"
                  fontWeight="600"
                  fontSize="16"
                >
                  Pedestrian #204
                </text>
              </g>

              {/* Measure callout */}
              <g stroke={wc} strokeWidth="2" fill="none">
                <path d={`M520 168V262M${wx} 168V254M520 168H${wx}M520 168l9 -5M520 168l9 5M${wx} 168l-9 -5M${wx} 168l-9 5`} />
              </g>
              <g transform={`translate(${mid}, 130)`}>
                <rect x="-56" y="-26" width="112" height="46" fill="#0F1112" stroke={wc} strokeWidth="2" />
                <text
                  x="0"
                  y="10"
                  textAnchor="middle"
                  fontFamily="var(--font-barlow-condensed, sans-serif)"
                  fontWeight="700"
                  fontSize="34"
                  fill={wc}
                >
                  {dText}
                </text>
              </g>
            </svg>
          </div>

          {/* Panel: Timeline & Curve Scrubber */}
          <div
            style={{
              border: "2px solid var(--ink)",
              background: "var(--card)",
              padding: "20px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                flexWrap: "wrap",
                gap: "8px",
                marginBottom: "12px",
              }}
            >
              <h2 className="h3" style={{ fontSize: "22px" }}>
                The 1.5 Seconds Measured · Distance Curve
              </h2>
              <span className="small">Drag slider to scrub timeline frame by frame</span>
            </div>

            <div className="plot">
              <svg viewBox="0 0 880 260" style={{ display: "block", width: "100%", height: "auto" }}>
                <g stroke="var(--ink)" strokeOpacity=".18">
                  <path d="M60 170H860M60 120H860M60 70H860M60 25H860" />
                </g>
                <g fontFamily="var(--font-dm-mono, monospace)" fontSize="11" fill="var(--ink-2)">
                  <text x="50" y="224" textAnchor="end">0</text>
                  <text x="50" y="174" textAnchor="end">1</text>
                  <text x="50" y="74" textAnchor="end">3</text>
                  <text x="50" y="29" textAnchor="end">4 m</text>
                </g>

                {/* 2.0 m threshold */}
                <path d="M60 120H860" stroke="var(--alert)" strokeWidth="2" strokeDasharray="6 4" />
                <text
                  x="858"
                  y="112"
                  textAnchor="end"
                  fontFamily="var(--font-barlow, sans-serif)"
                  fontWeight="700"
                  fontSize="13"
                  fill="var(--alert)"
                >
                  2.0 m limit
                </text>

                {/* Shaded breach */}
                <polygon
                  points="385,120 385,185 550,192 720,185 720,120"
                  fill="var(--alert)"
                  fillOpacity=".2"
                />

                {/* Distance curve */}
                <path
                  d="M60 50 C 200 50, 320 85, 420 140 S 600 195, 720 185 S 820 165, 860 155"
                  fill="none"
                  stroke="var(--ink)"
                  strokeWidth="3.5"
                  strokeLinejoin="round"
                />

                {/* Markers */}
                <g stroke="var(--ink)" strokeWidth="1" strokeDasharray="3 4">
                  <path d="M385 25V225M546 25V225M720 25V225" />
                </g>
                <g fontFamily="var(--font-dm-mono, monospace)" fontSize="11" fontWeight="500">
                  <rect x="374" y="6" width="22" height="18" fill="var(--ink)" />
                  <text x="385" y="19" textAnchor="middle" fill="var(--paper)">1</text>
                  <rect x="535" y="6" width="22" height="18" fill="var(--ink)" />
                  <text x="546" y="19" textAnchor="middle" fill="var(--paper)">2</text>
                  <rect x="709" y="6" width="22" height="18" fill="var(--ink)" />
                  <text x="720" y="19" textAnchor="middle" fill="var(--paper)">3</text>
                </g>

                {/* Axis */}
                <path d="M60 225H860" stroke="var(--ink)" strokeWidth="2" />
                <g fontFamily="var(--font-dm-mono, monospace)" fontSize="11" fill="var(--ink-2)" textAnchor="middle">
                  <text x="70" y="244">−1.25 s</text>
                  <text x="327" y="244">−0.75</text>
                  <text x="593" y="244">−0.25</text>
                  <text x="727" y="244">0.00</text>
                  <text x="850" y="244">+0.25</text>
                </g>

                <line x1={px} y1="25" x2={px} y2="225" stroke={wc} strokeWidth="3" />
                <circle cx={px} cy={py} r="8" fill={wc} stroke="#ECEAE3" strokeWidth="2" />
              </svg>

              <input
                type="range"
                min="-1.25"
                max="0.25"
                step="0.01"
                value={t}
                onChange={(e) => setT(parseFloat(e.target.value))}
                aria-label="Scrub timeline"
              />
            </div>

            <div style={{ marginTop: "14px", borderTop: "1px solid var(--ink)", paddingTop: "8px" }}>
              <div className="ev">
                <span className="tag">1</span>
                <span className="dm">−0.64 s</span>
                <span>Pedestrian crossed 2.0 m perimeter. Rule WH-049 fired candidate alert.</span>
              </div>
              <div className="ev">
                <span className="tag">2</span>
                <span className="dm">−0.38 s</span>
                <span>Glimmer confirmed non-operator in path. Interlock tripped in 8 ms.</span>
              </div>
              <div className="ev">
                <span className="tag">3</span>
                <span className="dm">0.00 s</span>
                <span>Forklift halted at 1.1 m. Hash-chain record sealed in Merkle DAG.</span>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: 4 Cols (High-Density Dashboard Operational Panels) */}
        <div className="s4" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          {/* Panel 1: Critical Telemetry Gauge */}
          <div
            style={{
              border: "2px solid var(--ink)",
              background: "var(--card)",
              padding: "18px",
            }}
          >
            <div className="small" style={{ textTransform: "uppercase", fontWeight: 700 }}>
              Live Separation Distance
            </div>
            <div
              className="num-xl"
              style={{
                fontSize: "76px",
                lineHeight: 0.9,
                margin: "8px 0",
                color: isBreach ? "var(--alert)" : "var(--ink)",
              }}
            >
              {dNum}
              <span style={{ fontSize: "32px", fontWeight: 600 }}> m</span>
            </div>

            <div className="tape" style={{ height: "8px", margin: "12px 0" }} />

            <div className="kvs" style={{ borderTop: "1px solid var(--ink)" }}>
              <div className="kv">
                <span>Rule WH-049 Threshold</span>
                <b>2.0 m</b>
              </div>
              <div className="kv">
                <span>Time to Contact (2.4 m/s)</span>
                <b>{ttc}</b>
              </div>
              <div className="kv">
                <span>Hardware Interlock</span>
                <b style={{ color: ilCol }}>{ilText}</b>
              </div>
              <div className="kv">
                <span>Camera Stream Latency</span>
                <b className="dm">38 ms (60 FPS)</b>
              </div>
            </div>
          </div>

          {/* Panel 2: Multimodal Perception (Glimmer 2.0) */}
          <div
            style={{
              border: "2px solid var(--ink)",
              background: "var(--card)",
              padding: "18px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: "8px",
              }}
            >
              <span className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                AI Model Reasoning
              </span>
              <span className="state ok">GLIMMER 2.0</span>
            </div>

            <p className="lead" style={{ fontSize: "16px", lineHeight: 1.4 }}>
              Pedestrian #204 identified walking across bay axis. Operator exclusion verified on
              forklift driver (P1-07 warm-up matched). Candidate confirmed as non-operator.
            </p>

            <div className="kvs" style={{ marginTop: "12px", borderTop: "1px solid var(--ink)" }}>
              <div className="kv">
                <span>Decisive Confidence</span>
                <b>99.4%</b>
              </div>
              <div className="kv">
                <span>ByteTrack Agreement</span>
                <b>16/16 frames</b>
              </div>
              <div className="kv">
                <span>Privacy Mode</span>
                <b className="state ok">ANONYMOUS</b>
              </div>
            </div>
          </div>

          {/* Panel 3: Cryptographic Audit Sealed Record */}
          <div
            style={{
              border: "2px solid var(--ink)",
              background: "var(--card)",
              padding: "18px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: "8px",
              }}
            >
              <span className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                Evidence Chain Block
              </span>
              <span className="state ok">INTACT</span>
            </div>

            <div className="mono" style={{ background: "var(--paper-2)", padding: "10px", fontSize: "12px" }}>
              <div><b>BLOCK:</b> #8912-A</div>
              <div><b>HASH:</b> 4a9fe310…71b2e</div>
              <div><b>MERKLE ROOT:</b> c70198aa…9901</div>
              <div><b>SIGNER:</b> hardware_relay_01</div>
            </div>

            <div style={{ marginTop: "12px", display: "flex", gap: "8px" }}>
              <Link href="/audit" className="btn o block" style={{ fontSize: "13px", padding: "8px" }}>
                Verify in Browser Wasm
              </Link>
            </div>
          </div>

          {/* Panel 4: Operator Actions */}
          <div
            style={{
              border: "2px solid var(--ink)",
              background: "var(--paper-2)",
              padding: "16px",
              display: "flex",
              flexDirection: "column",
              gap: "8px",
            }}
          >
            <div className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
              Operator Intervention
            </div>
            <button type="button" className="btn alert block" style={{ fontSize: "14px" }}>
              Manual Emergency Power Cut
            </button>
            <Link href="/worker" className="btn o block" style={{ fontSize: "14px", textAlign: "center" }}>
              Open Worker Dispute Handset
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
