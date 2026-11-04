"use client";

import Link from "next/link";
import React, { useState } from "react";
import { api, errorText } from "../../lib/api";
import { PairPhoneButton } from "../PairPhoneButton";
import { SampleDataTag } from "../SampleDataTag";

interface RulePreset {
  id: string;
  label: string;
  textEn: string;
  textEs: string;
  cameraId: string;
  videoSrc: string;
  conceptA: string;
  conceptB: string;
  op: "distance_m" | "without";
  threshold: number;
  unit: string;
  speedMps: number;
  severity: "high" | "medium" | "low";
  role: string;
  actionKind: "speak" | "notify" | "interlock_cut";
  standard: string;
}

const PRESETS: RulePreset[] = [
  {
    id: "keep-people-2m-from-moving-forklift",
    label: "Forklift 2.0m Proximity (OSHA 1910.178)",
    textEn: "Keep people at least 2.0 metres away from a moving forklift (>0.2 m/s); exclude seated operator.",
    textEs: "Mantener a las personas a 2.0 metros de un montacargas en movimiento (>0.2 m/s).",
    cameraId: "CAM-02",
    videoSrc: "/videos/cam02_intersection.mp4",
    conceptA: "person",
    conceptB: "forklift",
    op: "distance_m",
    threshold: 2.0,
    unit: "m",
    speedMps: 0.2,
    severity: "high",
    role: "safety_officer",
    actionKind: "speak",
    standard: "OSHA-1910.178(n)(1)",
  },
  {
    id: "require-hardhat-in-visitor-corridor",
    label: "Visitor Corridor Hardhat PPE (OSHA 1910.135)",
    textEn: "Every person entering the East Visitor Corridor must wear a safety helmet (Tier-0 head crop-zoom).",
    textEs: "Toda persona que ingrese al Corredor Este de Visitantes debe usar casco de seguridad.",
    cameraId: "CAM-03",
    videoSrc: "/videos/cam03_ppe.mp4",
    conceptA: "person",
    conceptB: "safety_helmet",
    op: "without",
    threshold: 2.0,
    unit: "s",
    speedMps: 0.0,
    severity: "medium",
    role: "supervisor",
    actionKind: "notify",
    standard: "OSHA-1910.135(a)(1)",
  },
  {
    id: "heavy-equipment-swing-radius-exclusion",
    label: "Excavator 3.5m Swing Radius (OSHA 1926.1424)",
    textEn: "Maintain a 3.5 metre exclusion zone around moving excavators and backing freight trucks.",
    textEs: "Mantener una zona de exclusión de 3.5 metros alrededor de excavadoras en movimiento.",
    cameraId: "CAM-09",
    videoSrc: "/videos/cam09_excavator.mp4",
    conceptA: "person",
    conceptB: "excavator",
    op: "distance_m",
    threshold: 3.5,
    unit: "m",
    speedMps: 0.15,
    severity: "high",
    role: "site_lead",
    actionKind: "interlock_cut",
    standard: "OSHA-1926.1424(a)(2)",
  },
  {
    id: "commercial-kitchen-prep-sanitation-guard",
    label: "Kitchen Prep Head Covering (FDA §2-402.11)",
    textEn: "Ensure commercial kitchen prep line staff wear chef head coverings at all times.",
    textEs: "Asegurar que el personal de línea de cocina use gorro o malla para el cabello.",
    cameraId: "CAM-05",
    videoSrc: "/videos/cam05_kitchen.mp4",
    conceptA: "person",
    conceptB: "head_covering",
    op: "without",
    threshold: 5.0,
    unit: "s",
    speedMps: 0.0,
    severity: "low",
    role: "kitchen_lead",
    actionKind: "notify",
    standard: "FDA-FoodCode-2-402.11",
  },
];

export function PlannerSignage() {
  const [selectedPresetIdx, setSelectedPresetIdx] = useState<number>(0);
  const preset: RulePreset = PRESETS[selectedPresetIdx] ?? PRESETS[0]!;

  const [promptText, setPromptText] = useState<string>(preset.textEn);
  const [cameraId, setCameraId] = useState<string>(preset.cameraId);
  const [threshold, setThreshold] = useState<number>(preset.threshold);
  const [severity, setSeverity] = useState<"high" | "medium" | "low">(preset.severity);
  const [role, setRole] = useState<string>(preset.role);
  const [lang, setLang] = useState<"en" | "es">("en");

  const [compiling, setCompiling] = useState<boolean>(false);
  // Nothing has run in this browser: the strip lists the steps; the server reports real progress.
  const activeStepIdx = -1;
  const [compiledPlanId, setCompiledPlanId] = useState<string>("");
  const [approved, setApproved] = useState<boolean>(false);
  const [bannerMsg, setBannerMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const selectPreset = (idx: number) => {
    const p = PRESETS[idx] ?? PRESETS[0]!;
    setSelectedPresetIdx(idx);
    setPromptText(lang === "en" ? p.textEn : p.textEs);
    setCameraId(p.cameraId);
    setThreshold(p.threshold);
    setSeverity(p.severity);
    setRole(p.role);
    setApproved(false);
    setBannerMsg(null);
  };

  // The Site Planner's steps and who runs each one. The real run happens on the server
  // (`POST /plans/requests`); its timings, acceptance trace and Rule CI result arrive with the
  // plan, so none are invented here.
  const steps = [
    { n: "01", name: "Understand", ms: "Muse Spark" },
    { n: "02", name: "Survey", ms: "SAM 3.1" },
    { n: "03", name: "24 Probes", ms: "SAM 3.1" },
    { n: "04", name: "Gauge", ms: "Wilson bound" },
    { n: "05", name: "Ask", ms: "owner questions" },
    { n: "06", name: "Compile IR", ms: "Muse Spark" },
    { n: "07", name: "Lint", ms: "plan linter" },
    { n: "08", name: "Accept", ms: "acceptance checks" },
    { n: "09", name: "Audit & BT", ms: "Spark Open · Glimmer" },
    { n: "10", name: "Prove (CI)", ms: "Rule CI" },
  ];

  const handleCompileAndStage = async () => {
    const text = promptText.trim();
    if (!text) return;
    setCompiling(true);
    setErrorMsg(null);
    setBannerMsg(null);
    setApproved(false);
    try {
      const { plan_id } = await api.requestPlan([{ kind: "text", text, lang }]);
      setCompiledPlanId(plan_id);
      setBannerMsg(
        `Sent to the planner: ${plan_id} is being planned with the live models. Its acceptance ` +
          `trace and Rule CI result appear under Plans → ${plan_id} when it is ready; approve it ` +
          "only if they pass.",
      );
    } catch (err) {
      setErrorMsg(errorText(err, "The planner did not accept the request"));
    } finally {
      setCompiling(false);
    }
  };

  const handleApproveAndDeploy = async () => {
    setErrorMsg(null);
    try {
      await api.approve(compiledPlanId);
      setApproved(true);
      setBannerMsg(
        `✓ Plan ${compiledPlanId} approved via POST /plans/${compiledPlanId}/approve, activated in RuntimeLoop, and anchored to the SHA-256 EventChain!`,
      );
    } catch (err) {
      setErrorMsg(errorText(err, "Could not approve plan"));
    }
  };

  return (
    <div style={{ padding: "var(--s3) var(--gut) var(--s6)" }}>
      <SampleDataTag what="The rule preview, calibration table, probe numbers and badges on this page are built in the browser from the chosen preset. 'Plan this rule' sends your words to the real planner, whose plan, acceptance trace and Rule CI result appear under Plans." />
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
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-card)",
          marginBottom: "var(--s3)",
        }}
      >
        <div>
          <div className="ctx" style={{ marginBottom: "2px" }}>
            Site Planner Studio &nbsp;·&nbsp; 10-step planner on the server &nbsp;·&nbsp;{" "}
            <b>Plan: {compiledPlanId || "none requested yet"}</b>
          </div>
          <h1 className="h2" style={{ fontSize: "28px", margin: 0 }}>
            Natural-Language Rule Compiler &amp; Spatial Homography Studio
          </h1>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <PairPhoneButton />
          <button
            type="button"
            className="btn"
            style={{ minHeight: "40px", padding: "8px 16px", fontSize: "14px" }}
            onClick={() => void handleCompileAndStage()}
            disabled={compiling}
          >
            {compiling ? "Sending to the planner…" : "Plan this rule (live planner)"}
          </button>
          <button
            type="button"
            className={`btn ${approved ? "o" : "alert"}`}
            style={{ minHeight: "40px", padding: "8px 16px", fontSize: "14px" }}
            onClick={() => void handleApproveAndDeploy()}
            disabled={!compiledPlanId}
          >
            {approved ? `✓ ${compiledPlanId} Live in Runtime` : "Approve & Deploy to Runtime"}
          </button>
          <Link
            href="/ci"
            className="btn o"
            style={{ minHeight: "40px", padding: "8px 16px", fontSize: "14px" }}
          >
            Run Rule CI Suite →
          </Link>
        </div>
      </div>

      {bannerMsg && (
        <div
          style={{
            marginBottom: "var(--s3)",
            padding: "12px 16px",
            background: "var(--color-accent-soft)",
            color: "var(--color-ink)",
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-card)",
            fontWeight: 700,
          }}
        >
          {bannerMsg}
        </div>
      )}
      {errorMsg && (
        <div
          style={{
            marginBottom: "var(--s3)",
            padding: "12px 16px",
            background: "var(--color-bad-soft)",
            color: "var(--color-bad)",
            fontWeight: 700,
          }}
        >
          {errorMsg}
        </div>
      )}

      {/* 10-Step Pipeline Strip */}
      <div
        style={{
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-card)",
          background: "var(--card)",
          padding: "12px 16px",
          marginBottom: "var(--s3)",
        }}
      >
        <div className="pipeline">
          {steps.map((s, idx) => {
            const isCurrent = idx === activeStepIdx;
            const isDone = idx < activeStepIdx;
            const barColor = isCurrent
              ? "var(--color-accent)"
              : isDone
                ? "var(--ink)"
                : "var(--rule-2)";
            const bg = isCurrent ? "var(--paper-2)" : "transparent";
            return (
              <div key={s.n} className="step" style={{ borderTopColor: barColor, background: bg }}>
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
        {/* Left Column (6 cols): Interactive Rule Composer & Compiled RuleIR AST */}
        <div className="s6" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              background: "var(--card)",
              padding: "20px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "10px",
                flexWrap: "wrap",
                gap: "8px",
              }}
            >
              <span className="small" style={{ fontWeight: 700 }}>
                1 · Natural-Language Safety Request Composer
              </span>
              <div>
                <button
                  type="button"
                  className="tab"
                  aria-pressed={lang === "en"}
                  onClick={() => {
                    setLang("en");
                    setPromptText(preset.textEn);
                  }}
                  style={{ minHeight: "30px", padding: "0 8px" }}
                >
                  EN
                </button>
                <button
                  type="button"
                  className="tab"
                  aria-pressed={lang === "es"}
                  onClick={() => {
                    setLang("es");
                    setPromptText(preset.textEs);
                  }}
                  style={{ minHeight: "30px", padding: "0 8px" }}
                >
                  ES
                </button>
              </div>
            </div>

            {/* Industrial Rule Presets */}
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "12px" }}>
              {PRESETS.map((p, idx) => (
                <button
                  key={p.id}
                  type="button"
                  className="tab"
                  aria-pressed={selectedPresetIdx === idx}
                  onClick={() => selectPreset(idx)}
                  style={{ fontSize: "12px", padding: "4px 10px" }}
                >
                  {p.label}
                </button>
              ))}
            </div>

            <textarea
              rows={3}
              value={promptText}
              onChange={(e) => setPromptText(e.target.value)}
              placeholder="Describe the safety rule in plain English or Spanish…"
              style={{
                width: "100%",
                padding: "12px",
                border: "1px solid var(--color-rule)",
                borderRadius: "var(--radius-card)",
                background: "var(--paper)",
                fontFamily: "var(--font-barlow-condensed, sans-serif)",
                fontSize: "24px",
                fontWeight: 700,
                lineHeight: 1.2,
                marginBottom: "14px",
              }}
            />

            {/* Parametric Controls */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4, 1fr)",
                gap: "10px",
                padding: "12px",
                background: "var(--paper-2)",
                border: "1px solid var(--color-rule)",
                borderRadius: "var(--radius-card)",
              }}
            >
              <div>
                <label className="mono" style={{ fontSize: "11px", display: "block", marginBottom: "4px" }}>
                  TARGET CAMERA
                </label>
                <select
                  value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "6px",
                    border: "1px solid var(--color-rule-2)", borderRadius: "var(--radius-pill)",
                    background: "var(--paper)",
                    fontFamily: "var(--font-dm-mono, monospace)",
                    fontWeight: 700,
                  }}
                >
                  {["CAM-01", "CAM-02", "CAM-03", "CAM-04", "CAM-05", "CAM-06", "CAM-08", "CAM-09", "CAM-10", "CAM-11", "CAM-12", "phone-27"].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mono" style={{ fontSize: "11px", display: "block", marginBottom: "4px" }}>
                  THRESHOLD ({preset.unit})
                </label>
                <input
                  type="number"
                  step="0.1"
                  min="0.5"
                  max="20"
                  value={threshold}
                  onChange={(e) => setThreshold(parseFloat(e.target.value) || 2.0)}
                  style={{
                    width: "100%",
                    padding: "6px",
                    border: "1px solid var(--color-rule-2)", borderRadius: "var(--radius-pill)",
                    background: "var(--paper)",
                    fontFamily: "var(--font-dm-mono, monospace)",
                    fontWeight: 700,
                  }}
                />
              </div>

              <div>
                <label className="mono" style={{ fontSize: "11px", display: "block", marginBottom: "4px" }}>
                  SEVERITY GATE
                </label>
                <select
                  value={severity}
                  onChange={(e) => setSeverity(e.target.value as "high" | "medium" | "low")}
                  style={{
                    width: "100%",
                    padding: "6px",
                    border: "1px solid var(--color-rule-2)", borderRadius: "var(--radius-pill)",
                    background: "var(--paper)",
                    fontFamily: "var(--font-dm-mono, monospace)",
                    fontWeight: 700,
                  }}
                >
                  <option value="high">HIGH (Act-First)</option>
                  <option value="medium">MEDIUM</option>
                  <option value="low">LOW</option>
                </select>
              </div>

              <div>
                <label className="mono" style={{ fontSize: "11px", display: "block", marginBottom: "4px" }}>
                  RESPONDER ROLE
                </label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "6px",
                    border: "1px solid var(--color-rule-2)", borderRadius: "var(--radius-pill)",
                    background: "var(--paper)",
                    fontFamily: "var(--font-dm-mono, monospace)",
                    fontWeight: 700,
                  }}
                >
                  <option value="safety_officer">safety_officer</option>
                  <option value="supervisor">supervisor</option>
                  <option value="site_lead">site_lead</option>
                  <option value="kitchen_lead">kitchen_lead</option>
                </select>
              </div>
            </div>
          </div>

          {/* Compiled RuleIR AST Preview */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
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
              <span className="small" style={{ fontWeight: 700 }}>
                2 · Compiled Canonical RuleIR AST (Discriminated Union)
              </span>
              <span className="state">PREVIEW · NOT COMPILED</span>
            </div>

            <pre
              className="code"
              style={{
                padding: "14px",
                fontSize: "12px",
                lineHeight: 1.45,
                maxHeight: "300px",
                overflowY: "auto",
              }}
            >
              {JSON.stringify(
                {
                  id: preset.id,
                  calibration: `ground-plane@${cameraId}`,
                  concepts: {
                    [preset.conceptA]: { prompt: preset.conceptA, static: false },
                    [preset.conceptB]: { prompt: preset.conceptB.replace("_", " "), static: false },
                  },
                  predicate:
                    preset.op === "distance_m"
                      ? {
                          op: "all",
                          of: [
                            {
                              op: "distance_m",
                              a: preset.conceptA,
                              b: preset.conceptB,
                              lt: threshold,
                              gt: null,
                            },
                            {
                              op: "moving",
                              object: preset.conceptB,
                              speed_mps_gt: preset.speedMps,
                            },
                          ],
                        }
                      : {
                          op: "all",
                          of: [
                            {
                              op: "inside",
                              object: preset.conceptA,
                              zone: `zone_${cameraId.toLowerCase()}`,
                            },
                            {
                              op: "without",
                              subject: preset.conceptA,
                              item: preset.conceptB,
                              region: "top_25pct",
                            },
                          ],
                        },
                  severity,
                  actions: [{ kind: preset.actionKind, role, lang: "en", after_s: 0.0 }],
                  verify: {
                    tier1: "glimmer",
                    tier2: "spark-open-on-disagree",
                    min_confidence: 0.7,
                  },
                },
                null,
                2,
              )}
            </pre>
          </div>
        </div>

        {/* Right Column (6 cols): Live Camera Homography & 24-Frame Probe Evidence */}
        <div className="s6" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
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
              <span className="small" style={{ fontWeight: 700 }}>
                3 · Live Camera Ground-Plane Calibration ({cameraId})
              </span>
              <span className="state ok">HOMOGRAPHY ERR ±0.094m</span>
            </div>

            <div style={{ position: "relative", background: "var(--color-scene)", border: "1px solid var(--color-rule)" }}>
              <video
                key={preset.videoSrc}
                src={preset.videoSrc}
                autoPlay
                loop
                muted
                playsInline
                style={{ width: "100%", height: "250px", objectFit: "cover", display: "block" }}
              />
              <svg
                viewBox="0 0 640 250"
                style={{
                  position: "absolute",
                  inset: 0,
                  width: "100%",
                  height: "100%",
                  pointerEvents: "none",
                }}
              >
                <polygon
                  points="60,185 210,115 470,115 590,195"
                  fill="rgba(242, 209, 0, 0.14)"
                  stroke="var(--color-accent)"
                  strokeWidth="2"
                  strokeDasharray="6 4"
                />
                <text
                  x="76"
                  y="175"
                  fill="var(--color-accent)"
                  fontFamily="var(--font-dm-mono, monospace)"
                  fontSize="11"
                  fontWeight="700"
                >
                  CALIBRATED GROUND PLANE ({cameraId}) · {threshold.toFixed(1)} {preset.unit} GUARD
                </text>
              </svg>
            </div>

            <div className="kvs" style={{ marginTop: "12px" }}>
              <div className="kv">
                <span>Reference Object</span>
                <b className="dm">Euro / GMA Pallet 48×40in + MoGe-2 Metric Point Map</b>
              </div>
              <div className="kv">
                <span>1-Sigma Ground Error</span>
                <b className="dm">0.094 m (Tolerance &lt;= 0.100 m)</b>
              </div>
            </div>
          </div>

          {/* 24-Frame SAM 3.1 Probe Table */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
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
              <span className="small" style={{ fontWeight: 700 }}>
                4 · 24-Frame SAM 3.1 Feasibility Probes &amp; Regulatory Grounding
              </span>
              <span className="state ok">OBSERVABLE</span>
            </div>

            <div className="scroll-x">
              <table className="tbl" style={{ fontSize: "13px" }}>
                <thead>
                  <tr>
                    <th>Concept</th>
                    <th>Camera</th>
                    <th>Frames</th>
                    <th>Wilson 95% LB</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="dm">
                      <b>{preset.conceptA}</b>
                    </td>
                    <td className="dm">{cameraId}</td>
                    <td className="dm">24 / 24</td>
                    <td className="dm">0.924 (&gt;= 0.80)</td>
                    <td>
                      <span className="state ok">PASS</span>
                    </td>
                  </tr>
                  <tr>
                    <td className="dm">
                      <b>{preset.conceptB}</b>
                    </td>
                    <td className="dm">{cameraId}</td>
                    <td className="dm">24 / 24</td>
                    <td className="dm">0.895 (&gt;= 0.80)</td>
                    <td>
                      <span className="state ok">PASS</span>
                    </td>
                  </tr>
                  <tr>
                    <td className="dm">
                      <b>pallet_48x40in (ref)</b>
                    </td>
                    <td className="dm">{cameraId}</td>
                    <td className="dm">24 / 24</td>
                    <td className="dm">0.948 (&gt;= 0.80)</td>
                    <td>
                      <span className="state ok">CALIBRATED</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="kv" style={{ marginTop: "12px" }}>
              <span>Cited Regulatory Standard</span>
              <b className="dm">{preset.standard} · preset citation (the planner checks it)</b>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
