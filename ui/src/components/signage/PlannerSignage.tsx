"use client";

import React, { useState } from "react";
import Link from "next/link";

export function PlannerSignage() {
  const [lang, setLang] = useState<"en" | "es">("en");
  const [playing, setPlaying] = useState<boolean>(false);
  const [accepted, setAccepted] = useState<boolean>(false);

  const steps = [
    { n: "01", name: "Intake", ms: "42 ms", status: "ok" },
    { n: "02", name: "Survey", ms: "110 ms", status: "ok" },
    { n: "03", name: "Calibrate", ms: "88 ms", status: "ok" },
    { n: "04", name: "24 Probes", ms: "340 ms", status: "ok" },
    { n: "05", name: "Compile", ms: "64 ms", status: "ok" },
    { n: "06", name: "Z3 Lint", ms: "18 ms", status: "ok" },
    { n: "07", name: "Rule CI", ms: "1.2 s", status: "ok" },
    { n: "08", name: "Accept", ms: "waiting", status: "active" },
    { n: "09", name: "Deploy", ms: "—", status: "pending" },
    { n: "10", name: "Live", ms: "—", status: "pending" },
  ];

  const bars = Array.from({ length: 44 }, (_, i) => ({
    x: i * 5.2,
    y: 26 - Math.sin(i * 0.4) * 16 - (i % 3) * 4,
    h: Math.abs(Math.sin(i * 0.4) * 32) + 6,
    c: playing ? (i < 20 ? "#F2D100" : "var(--ink)") : "var(--ink-2)",
  }));

  return (
    <div style={{ padding: "var(--s3) var(--gut) var(--s6)" }}>
      {/* Dashboard Top Control Strip */}
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
        <div>
          <div className="ctx" style={{ marginBottom: "2px" }}>
            Site Planner Studio &nbsp;·&nbsp; Step 8 of 10 &nbsp;·&nbsp; <b>Acceptance Gate</b>
          </div>
          <h1 className="h2" style={{ fontSize: "28px", margin: 0 }}>
            RULE-CK-012 passed all 6 verification checks.
          </h1>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <button
            type="button"
            className={`btn ${accepted ? "o" : ""}`}
            style={{ minHeight: "40px", padding: "8px 18px", fontSize: "14px" }}
            onClick={() => setAccepted(!accepted)}
          >
            {accepted ? "✓ Plan Accepted · Staged" : "Accept Rule & Stage"}
          </button>
          <Link
            href="/ci"
            className="btn o"
            style={{ minHeight: "40px", padding: "8px 18px", fontSize: "14px" }}
          >
            Run Rule CI
          </Link>
          <button
            type="button"
            className="btn o"
            style={{ minHeight: "40px", padding: "8px 18px", fontSize: "14px" }}
          >
            Send Back with Note
          </button>
        </div>
      </div>

      {/* 10-Step Pipeline Strip */}
      <div
        style={{
          border: "2px solid var(--ink)",
          background: "var(--card)",
          padding: "12px 16px",
          marginBottom: "var(--s3)",
        }}
      >
        <div className="pipeline">
          {steps.map((s) => {
            const isActive = s.status === "active";
            const isOk = s.status === "ok";
            const barColor = isActive
              ? "#F2D100"
              : isOk
              ? "var(--ink)"
              : "var(--rule-2)";
            const bg = isActive ? "var(--paper-2)" : "transparent";
            return (
              <div
                key={s.n}
                className="step"
                style={{ borderTopColor: barColor, background: bg }}
              >
                <span className="dm">{s.n}</span>
                <b>{s.name}</b>
                <span className="dm">{s.ms}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Main 2-Column Dashboard Workspace */}
      <div className="grid">
        {/* Left Column (6 cols): Natural Language & Formal Logic */}
        <div className="s6" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          {/* Natural Language Prompt & Audio Card */}
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
                alignItems: "center",
                marginBottom: "12px",
              }}
            >
              <span className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                1 · Natural Language Intake (06:12 UTC)
              </span>
              <div>
                <button
                  type="button"
                  className="tab"
                  aria-pressed={lang === "en"}
                  onClick={() => setLang("en")}
                  style={{ minHeight: "32px", padding: "0 8px" }}
                >
                  EN
                </button>
                <button
                  type="button"
                  className="tab"
                  aria-pressed={lang === "es"}
                  onClick={() => setLang("es")}
                  style={{ minHeight: "32px", padding: "0 8px" }}
                >
                  ES
                </button>
              </div>
            </div>

            {lang === "en" ? (
              <p className="quote" style={{ fontSize: "36px", lineHeight: 1.15, marginBottom: "16px" }}>
                Cooks must <span className="u1">wash hands</span>{" "}
                <span className="tag t1">1</span> for{" "}
                <span className="hi">20 seconds</span>{" "}
                <span className="tag t2">2</span> at sink before handling{" "}
                <span className="u3">raw poultry</span>{" "}
                <span className="tag t3">3</span>
              </p>
            ) : (
              <p className="quote" style={{ fontSize: "36px", lineHeight: 1.15, marginBottom: "16px" }}>
                Los cocineros deben <span className="u1">lavarse las manos</span>{" "}
                <span className="tag t1">1</span> durante{" "}
                <span className="hi">20 segundos</span>{" "}
                <span className="tag t2">2</span> antes de manipular{" "}
                <span className="u3">aves crudas</span>{" "}
                <span className="tag t3">3</span>
              </p>
            )}

            {/* Audio Waveform Player */}
            <div
              style={{
                border: "1px solid var(--ink)",
                background: "var(--paper-2)",
                padding: "12px",
                display: "flex",
                alignItems: "center",
                gap: "14px",
              }}
            >
              <button
                type="button"
                className="btn"
                onClick={() => setPlaying(!playing)}
                aria-label={playing ? "Pause prompt audio" : "Play prompt audio"}
                style={{ width: "44px", minHeight: "44px", padding: 0, flexShrink: 0 }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="var(--paper)">
                  {playing ? <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" /> : <path d="M8 5v14l11-7z" />}
                </svg>
              </button>
              <svg viewBox="0 0 240 40" style={{ display: "block", flex: 1, height: "40px" }}>
                {bars.map((b, i) => (
                  <rect key={i} x={b.x} y={b.y} width="2.4" height={b.h} fill={b.c} />
                ))}
              </svg>
              <span className="dm" style={{ fontSize: "12px" }}>
                {playing ? "0:08" : "0:00"} / 0:12
              </span>
            </div>

            <div className="kv" style={{ marginTop: "12px" }}>
              <span>Acoustic Whisper Confidence</span>
              <b>99.4% · 48 kHz FLAC</b>
            </div>
          </div>

          {/* Compiled AST & Formal Verification */}
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
                alignItems: "center",
                marginBottom: "12px",
              }}
            >
              <span className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                2 · Compiled Rule AST
              </span>
              <span className="state ok">Z3 SOLVER · VERIFIED</span>
            </div>

            <div className="code" style={{ padding: "14px" }}>
              <div className="l">
                <span><b>WHEN</b> worker enters <b>ZoneB (Prep)</b></span>
                <span><span className="tag t3">3</span></span>
              </div>
              <div className="l">
                <span><b>REQUIRE</b> ZoneA.duration &gt;= 20.0 s</span>
                <span><span className="tag t1">1</span> <span className="tag t2">2</span></span>
              </div>
              <div className="l">
                <span><b>WITHIN</b> 180 s</span>
                <span />
              </div>
              <div className="l">
                <span><b>ON_FAIL</b> lock(slicer_interlock)</span>
                <span><span className="tag t3">3</span></span>
              </div>
            </div>

            <div className="kvs" style={{ marginTop: "12px" }}>
              <div className="kv">
                <span>Formal SMT Verification</span>
                <b>Satisfiable · Deadlock Free</b>
              </div>
              <div className="kv">
                <span>Rule Canonical Hash</span>
                <b className="dm">sha256 7ad1f…e03c</b>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column (6 cols): Spatial Geometry, Camera Probes & Legal Grounding */}
        <div className="s6" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          {/* Spatial Floorplan Card */}
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
                alignItems: "center",
                marginBottom: "12px",
              }}
            >
              <span className="small" style={{ fontWeight: 700, textTransform: "uppercase" }}>
                3 · Spatial Zone Geometry (CAM-01)
              </span>
              <span className="state ok">CALIBRATED</span>
            </div>

            <svg
              viewBox="0 0 500 240"
              style={{ display: "block", width: "100%", height: "auto", background: "var(--cam)" }}
              role="img"
              aria-label="Floor plan with Zone A and Zone B"
            >
              <rect width="500" height="240" fill="#15181A" />
              {/* Zone A: Sink */}
              <rect x="40" y="40" width="160" height="150" fill="none" stroke="#0B62A8" strokeWidth="2.5" strokeDasharray="6 4" />
              <text x="50" y="70" fill="#3DB8FF" fontFamily="var(--font-barlow-condensed, sans-serif)" fontWeight="700" fontSize="20">
                ZONE A · SINK
              </text>
              <text x="50" y="98" fill="#ECEAE3" fontFamily="var(--font-dm-mono, monospace)" fontSize="13">
                cam-01 · 20.0s min
              </text>
              <rect x="50" y="120" width="140" height="40" fill="#0B62A8" fillOpacity=".2" stroke="#3DB8FF" strokeWidth="1" />
              <text x="120" y="145" textAnchor="middle" fill="#ECEAE3" fontFamily="var(--font-barlow, sans-serif)" fontSize="14">
                Stainless Handwash Basin
              </text>

              {/* Trajectory Transition Arrow */}
              <path d="M220 115 H270 M255 105 L270 115 L255 125" stroke="#F2D100" strokeWidth="3.5" fill="none" />
              <text x="245" y="95" textAnchor="middle" fill="#F2D100" fontFamily="var(--font-dm-mono, monospace)" fontSize="11">
                &lt; 180s
              </text>

              {/* Zone B: Poultry */}
              <rect x="290" y="40" width="170" height="150" fill="none" stroke="#C93410" strokeWidth="2.5" strokeDasharray="6 4" />
              <text x="305" y="70" fill="#FF5A2B" fontFamily="var(--font-barlow-condensed, sans-serif)" fontWeight="700" fontSize="20">
                ZONE B · PREP TABLE
              </text>
              <text x="305" y="98" fill="#ECEAE3" fontFamily="var(--font-dm-mono, monospace)" fontSize="13">
                raw meat / interlock
              </text>
              <rect x="305" y="120" width="140" height="40" fill="#C93410" fillOpacity=".2" stroke="#FF5A2B" strokeWidth="1" />
              <text x="375" y="145" textAnchor="middle" fill="#ECEAE3" fontFamily="var(--font-barlow, sans-serif)" fontSize="14">
                Industrial Meat Slicer
              </text>
            </svg>

            <div className="kvs" style={{ marginTop: "12px" }}>
              <div className="kv">
                <span>Homography Ground Reference</span>
                <b>1.0 m floor square flat in view</b>
              </div>
              <div className="kv">
                <span>Camera Height / Tilt Angle</span>
                <b>2.8 m / 38° down landscape</b>
              </div>
            </div>
          </div>

          {/* Regulatory Citation Card */}
          <div
            style={{
              border: "2px solid var(--ink)",
              background: "var(--card)",
              padding: "20px",
            }}
          >
            <div className="small" style={{ fontWeight: 700, textTransform: "uppercase", marginBottom: "8px" }}>
              4 · Statutory Source Basis
            </div>
            <h3 className="h3" style={{ fontSize: "22px", margin: "4px 0" }}>
              FDA Food Code § 2-301.12 (Cleaning Procedure)
            </h3>
            <p className="lead" style={{ fontSize: "16px", marginTop: "8px" }}>
              &ldquo;Food employees shall clean their hands and exposed portions of their arms for{" "}
              <span className="hi">at least 20 seconds</span> using a cleaning compound at a
              handwashing sink before engaging in food preparation.&rdquo;
            </p>
            <div className="kv" style={{ marginTop: "12px" }}>
              <span>Government Source Document</span>
              <b className="dm">FDA-2026-FC.pdf (sealed SHA256 4b1e…d9a7)</b>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
