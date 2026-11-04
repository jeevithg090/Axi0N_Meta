"use client";

import React, { useRef, useState } from "react";
import Link from "next/link";

import { SampleDataTag } from "../SampleDataTag";
import { ClipPlayer } from "../ClipPlayer";

interface ActSpec {
  id: number;
  startS: number;
  endS: number;
  shortTitle: string;
  title: string;
  camId: string;
  camLabel: string;
  ruleId: string;
  ruleText: string;
  calibration: string;
  statusBadge: string;
  isBreach: boolean;
  glimmerScore: string;
  glimmerVerdict: string;
  glimmerReason: string;
  plcState: string;
  entities: { id: string; role: string; metric: string; status: "ok" | "warn" | "bad" }[];
}

const MASTER_ACTS: ActSpec[] = [
  {
    id: 1,
    startS: 0,
    endS: 15,
    shortTitle: "Act 1 · Forklift Breach",
    title: "Multi-Pedestrian Aisle & Active Forklift Proximity Breach",
    camId: "CAM-04",
    camLabel: "CAM-04 · Dock A West Aisle",
    ruleId: "forklift-proximity-2m",
    ruleText: "Keep people at least 2.0 metres away from a moving forklift (speed > 0.2 m/s).",
    calibration: "Ground Homography H · Euro Pallet 1.20×1.00m · Err 0.08m",
    statusBadge: "BREACH · 1.18 m < 2.0 m",
    isBreach: true,
    glimmerScore: "0.99",
    glimmerVerdict: "CONFIRMED VIOLATION",
    glimmerReason:
      "A person on the right walks inside the 2.0 m rear-right exclusion zone of the moving forklift (measured 1.18 m on the calibrated floor plane; TTC 0.42 s). The two people on the left and in the centre stay outside, at more than 2.4 m.",
    plcState: "TRIPPED · 8 ms Interlock + Horn",
    entities: [
      { id: "Forklift", role: "Vehicle, moving", metric: "2.4 m/s North", status: "warn" },
      { id: "Person", role: "Pedestrian, right", metric: "1.18 m (TTC 0.42s)", status: "bad" },
      { id: "Person", role: "Pedestrian, centre", metric: "2.45 m · PPE OK", status: "ok" },
      { id: "Person", role: "Pedestrian, left", metric: "3.60 m · PPE OK", status: "ok" },
    ],
  },
  {
    id: 2,
    startS: 15,
    endS: 30,
    shortTitle: "Act 2 · 3× PPE Violation",
    title: "Simultaneous Multi-Person PPE Audit & Unhelmeted Visitor Detection",
    camId: "CAM-03",
    camLabel: "CAM-03 · Aisle 4 High-Rack",
    ruleId: "ppe-hardhat-zone",
    ruleText: "All personnel and visitors inside active high-rack forklift zones must wear hardhats.",
    calibration: "SAM-2 Person + Head Keypoint Sub-Box · Confidence >= 0.85",
    statusBadge: "3× PPE VIOLATION · NO HARDHAT",
    isBreach: true,
    glimmerScore: "0.98",
    glimmerVerdict: "CONFIRMED VIOLATION",
    glimmerReason:
      "A person in a red hardhat walks three people without hardhats through the active forklift aisle, where head protection is mandatory.",
    plcState: "PA WARNING + SUPERVISOR ALERT",
    entities: [
      { id: "Person", role: "Leading, left", metric: "Red Hardhat Verified", status: "ok" },
      { id: "Person", role: "Centre-left", metric: "NO HARDHAT DETECTED", status: "bad" },
      { id: "Person", role: "Centre-right", metric: "NO HARDHAT DETECTED", status: "bad" },
      { id: "Person", role: "Far right", metric: "NO HARDHAT DETECTED", status: "bad" },
    ],
  },
  {
    id: 3,
    startS: 30,
    endS: 41,
    shortTitle: "Act 3 · Intersection TTC",
    title: "Cross-Aisle Blind-Spot Emergence & Time-To-Collision (TTC) Gate",
    camId: "CAM-02",
    camLabel: "CAM-02 · Cross-Aisle Junction",
    ruleId: "intersection-ttc-2m",
    ruleText: "Trigger intersection strobe and horn when converging vehicle-pedestrian TTC < 1.2s.",
    calibration: "2D Velocity Vector Projection · Homography Floor Grid · 10 Hz",
    statusBadge: "COLLISION VECTOR · TTC 0.58 s",
    isBreach: true,
    glimmerScore: "0.97",
    glimmerVerdict: "CONFIRMED VIOLATION",
    glimmerReason:
      "A forklift crosses from the right rack aisle at 2.6 m/s, straight across the path of a person walking north (the gap drops from 3.40 m to 1.28 m).",
    plcState: "JUNCTION STROBE + HORN TRIPPED",
    entities: [
      { id: "Forklift", role: "Cross-aisle vehicle", metric: "2.6 m/s Westbound", status: "bad" },
      { id: "Person", role: "Pedestrian, walking north", metric: "Dist 1.28m · TTC 0.58s", status: "bad" },
    ],
  },
  {
    id: 4,
    startS: 41,
    endS: 56,
    shortTitle: "Act 4 · Operator Exclusion",
    title: "Seated Operator Warm-Up Lock (P1-07) & Euro Pallet Calibration",
    camId: "CAM-04",
    camLabel: "CAM-04 · Warehouse Aisle",
    ruleId: "forklift-proximity-2m (P1-07)",
    ruleText: "Link person track born inside forklift cabin (IoU >= 0.65) as vehicle operator; suppress self-alerts.",
    calibration: "Reference Object: Euro Pallet 1.20×1.00m · Homography Err 0.09m",
    statusBadge: "0 FALSE ALERTS · OPERATOR EXEMPT",
    isBreach: false,
    glimmerScore: "0.00",
    glimmerVerdict: "BYPASSED (OPERATOR LINKED)",
    glimmerReason:
      "A person overlaps the forklift cabin at IoU 0.89 during the 1.0 s warm-up window, so they are linked as the seated operator; Rule CI mutant drop_operator_warmup verified.",
    plcState: "ARMED · CLEAR (0 False Alerts)",
    entities: [
      { id: "Forklift", role: "Active vehicle", metric: "2.2 m/s · Cabin Mask", status: "ok" },
      { id: "Operator", role: "Seated in the cab", metric: "IoU 0.89 · EXEMPT", status: "ok" },
      { id: "Pallet", role: "Calibration reference", metric: "1.20m × 1.00m (Err 0.09m)", status: "ok" },
    ],
  },
  {
    id: 5,
    startS: 56,
    endS: 68,
    shortTitle: "Act 5 · Excavator 5m Arc",
    title: "Overhead Drone View: Heavy Excavator 5.0m Swing Radius Exclusion",
    camId: "CAM-09",
    camLabel: "CAM-09 · Construction Yard Drone",
    ruleId: "excavator-swing-radius-5m",
    ruleText: "Maintain 5.0m 360° radial clearance around slewing excavator boom during haul loading.",
    calibration: "Nadir Orthophoto Metric Projection · Slew Pivot Locked",
    statusBadge: "SWING ZONE CLEAR · SLEW ACTIVE",
    isBreach: false,
    glimmerScore: "0.02",
    glimmerVerdict: "COMPLIANT (NO FOOT TRAFFIC)",
    glimmerReason:
      "The excavator boom slews from -42° to +12° to load the haul truck. The full 5.0 m radial exclusion zone is clear of people on the ground.",
    plcState: "SLEW INTERLOCK ARMED · CLEAR",
    entities: [
      { id: "Excavator", role: "Articulated boom", metric: "Slew -42° -> +12°", status: "warn" },
      { id: "Haul truck", role: "Dump bed target", metric: "Locked in Bay", status: "ok" },
      { id: "5.0m SWING ARC", role: "Radial Exclusion", metric: "0 Personnel Inside", status: "ok" },
    ],
  },
  {
    id: 6,
    startS: 68,
    endS: 76.4,
    shortTitle: "Act 6 · Altercation Spike",
    title: "Workplace Altercation, Kinetic Optical-Flow Spike & VLM Escalation",
    camId: "CAM-03",
    camLabel: "CAM-03 · Storage Aisle 4",
    ruleId: "workplace-anomaly-person-down",
    ruleText: "Escalate sudden physical altercations, kinetic spikes (>3.5G), or person-down events.",
    calibration: "Optical-Flow Kinetic Energy + Tier-1 Multi-Frame Action Reasoning",
    statusBadge: "CRITICAL · PHYSICAL ALTERCATION",
    isBreach: true,
    glimmerScore: "0.99",
    glimmerVerdict: "CONFIRMED WORKPLACE INCIDENT",
    glimmerReason:
      "Two people in the aisle come into sudden physical contact at T+73.2 s (a 4.8 G lateral motion spike); the event is escalated for a person to review.",
    plcState: "SECURITY DISPATCH + EVIDENCE LOCKED",
    entities: [
      { id: "Person", role: "Left", metric: "Sudden contact, 4.8 G", status: "bad" },
      { id: "Person", role: "Right", metric: "Sudden contact", status: "bad" },
    ],
  },
  {
    id: 7,
    startS: 76.4,
    endS: 90.0,
    shortTitle: "Act 7 · Dock & Hygiene",
    title: "Loading Dock Freight Truck Blind-Spot & Kitchen 20s Hygiene Dwell",
    camId: "CAM-01 / CAM-05",
    camLabel: "CAM-01 Dock Bay 4 & CAM-05 Kitchen",
    ruleId: "loading-dock-buffer + handwash-20s",
    ruleText: "Enforce 2.5m freight truck blind-spot buffer & 20.0s kitchen handwash dwell gate.",
    calibration: "Multi-Site Fleet Telemetry · Dock Homography + Sink Zone Dwell Timer",
    statusBadge: "DOCK BREACH 1.05m / KITCHEN OK",
    isBreach: true,
    glimmerScore: "0.99 / 0.01",
    glimmerVerdict: "DOCK BREACH · KITCHEN COMPLIANT",
    glimmerReason:
      "Part A: a person steps into the approach path of a freight truck (1.05 m). Part B: three people at the kitchen sink each wash for at least 20.0 s.",
    plcState: "DOCK SIREN TRIPPED · PREP LINE OPEN",
    entities: [
      { id: "Freight truck", role: "Heavy goods vehicle", metric: "3.1 m/s Dock Approach", status: "warn" },
      { id: "Person", role: "Dock pedestrian", metric: "1.05m Blind-Spot Breach!", status: "bad" },
      { id: "3 people", role: "At the handwash sink", metric: "22.4s / 20.0s Verified", status: "ok" },
    ],
  },
];

export function MonitorSignage() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [videoTime, setVideoTime] = useState<number>(0);
  const [t, setT] = useState<number>(-0.64);
  const [acked, setAcked] = useState<boolean>(false);
  const [activeCam, setActiveCam] = useState<string>("master");
  const [viewMode, setViewMode] = useState<"video" | "radar">("video");
  const [inspectorOpen, setInspectorOpen] = useState<boolean>(true);
  const [chaptersOpen, setChaptersOpen] = useState<boolean>(true);

  // Determine current act from videoTime (0..90s)
  const currentAct: ActSpec =
    MASTER_ACTS.find((a) => videoTime >= a.startS && videoTime < a.endS) ?? MASTER_ACTS[0]!;

  const jumpToAct = (act: ActSpec) => {
    setActiveCam("master");
    setViewMode("video");
    setVideoTime(act.startS + 0.1);
    if (videoRef.current) {
      videoRef.current.currentTime = act.startS + 0.1;
      videoRef.current.play().catch(() => {});
    }
  };

  // Dynamic values driven by timeline scrubber (-1.25s to +0.25s) for Overhead Radar mode
  const norm = (t - -1.25) / (0.25 - -1.25);
  const px = 60 + norm * 800;

  const dist = t < 0 ? Math.max(1.1, 1.1 + Math.pow(t / 0.7, 2) * 1.5) : 1.1 + t * 0.4;
  const dNum = dist.toFixed(1);
  const dText = `${dNum} m`;
  const isBreach = viewMode === "video" ? currentAct.isBreach : dist <= 2.0;

  const wx = 520 + dist * 130;
  const mid = (520 + wx) / 2;
  const wc = isBreach ? "var(--color-bad)" : "var(--color-scene-cool)";
  const py = 262 - (dist / 4.0) * 180;

  const ilText = viewMode === "video" ? currentAct.plcState : dist <= 2.0 ? "TRIPPED · 8 ms" : "Arm / Live";
  const ilCol = isBreach ? "var(--alert)" : "var(--machine)";

  // Illustrative pseudo-hash for the scripted walkthrough (not a hash of a real frame or record).
  const frameNum = Math.floor(videoTime * 25);
  const frameHex = (
    ((frameNum * 2654435761) >>> 0).toString(16).padStart(8, "0") +
    (((frameNum + 918273) * 1597334677) >>> 0).toString(16).padStart(8, "0")
  ).slice(0, 16);

  return (
    <div style={{ padding: "var(--s3) var(--gut) var(--s6)" }}>
      <SampleDataTag what="This monitor is a scripted walkthrough: its acts, Tier 1 scores, distances, interlock timings and frame hashes are written into the page." />
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
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-card)",
          marginBottom: "10px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <span className="small" style={{ fontWeight: 700 }}>
            Feed:
          </span>
          <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
            <button
              type="button"
              className="tab"
              aria-pressed={activeCam === "master"}
              onClick={() => setActiveCam("master")}
              style={{ minHeight: "36px", padding: "0 12px", fontWeight: 700 }}
            >
              90s Master Stress-Test (7 Acts)
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={activeCam === "cam-04"}
              onClick={() => setActiveCam("cam-04")}
              style={{ minHeight: "36px", padding: "0 10px" }}
            >
              CAM-04 · Dock A
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={activeCam === "cam-01"}
              onClick={() => setActiveCam("cam-01")}
              style={{ minHeight: "36px", padding: "0 10px" }}
            >
              CAM-01 · Kitchen
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

          <div
            style={{
              display: "flex",
              gap: "4px",
              borderLeft: "1px solid var(--color-rule)",
              paddingLeft: "12px",
            }}
          >
            <button
              type="button"
              className="tab"
              aria-pressed={viewMode === "video"}
              onClick={() => setViewMode("video")}
              style={{ minHeight: "36px", padding: "0 12px", fontWeight: viewMode === "video" ? 700 : 400 }}
            >
              Annotated CV Video
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={viewMode === "radar"}
              onClick={() => setViewMode("radar")}
              style={{ minHeight: "36px", padding: "0 12px", fontWeight: viewMode === "radar" ? 700 : 400 }}
            >
              Overhead Radar
            </button>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <span className={`state ${isBreach ? "bad" : "ok"}`}>
            {viewMode === "video" ? currentAct.statusBadge : "EVENT #8912-A"}
          </span>
          <span className="dm small">
            FRAME #{frameNum.toString().padStart(4, "0")} · T+{videoTime.toFixed(1)}s / 90.0s
          </span>
          <button
            type="button"
            className="btn o"
            style={{ minHeight: "40px", padding: "8px 14px", fontSize: "13px" }}
            onClick={() => setChaptersOpen(!chaptersOpen)}
            title="Toggle 7-Act Scenario Drawer"
          >
            {chaptersOpen ? "▴ Hide Acts" : "▾ Show 7 Acts"}
          </button>
          <button
            type="button"
            className="btn o"
            style={{ minHeight: "40px", padding: "8px 14px", fontSize: "13px" }}
            onClick={() => setInspectorOpen(!inspectorOpen)}
            title="Toggle Right Telemetry Inspector Drawer"
          >
            {inspectorOpen ? "Hide Inspector »" : "« Show Inspector"}
          </button>
          <button
            type="button"
            className={`btn ${acked ? "o" : ""}`}
            style={{ minHeight: "40px", padding: "8px 18px", fontSize: "14px" }}
            onClick={() => setAcked(!acked)}
          >
            {acked ? "✓ Acknowledged" : "Acknowledge"}
          </button>
        </div>
      </div>

      {/* 7-Act Interactive Scenario Chapter Bar (Collapsible) */}
      {chaptersOpen && (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
          gap: "6px",
          marginBottom: "var(--s3)",
          background: "var(--card)",
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-card)",
          padding: "10px",
        }}
      >
        {MASTER_ACTS.map((act) => {
          const isActive = activeCam === "master" && viewMode === "video" && currentAct.id === act.id;
          return (
            <button
              key={act.id}
              type="button"
              onClick={() => jumpToAct(act)}
              style={{
                textAlign: "left",
                padding: "8px 10px",
                border: isActive ? "1px solid var(--color-rule)" : "1px solid var(--rule)",
                background: isActive
                  ? act.isBreach
                    ? "var(--alert, var(--color-bad))"
                    : "var(--warn, var(--color-accent))"
                  : "var(--paper)",
                color: isActive && act.isBreach ? "var(--color-on-signal)" : "var(--ink)",
                cursor: "pointer",
                transition: "all 0.12s ease",
              }}
            >
              <div style={{ fontSize: "11px", fontWeight: 800, opacity: 0.85 }}>
                {act.startS.toFixed(0)}s–{act.endS.toFixed(0)}s · {act.camId}
              </div>
              <div style={{ fontSize: "13px", fontWeight: 700, marginTop: "2px", lineHeight: 1.2 }}>
                {act.shortTitle}
              </div>
            </button>
          );
        })}
      </div>
      )}

      {/* Main 2-Column Dashboard Grid */}
      <div className="grid">
        {/* Left Column: 8 Cols or Full 12 Cols when Inspector is Collapsed */}
        <div className={inspectorOpen ? "s8" : "s12"} style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          {/* Panel: Camera Overhead Visualization / Master Video */}
          <div style={{ border: "1px solid var(--color-rule)", background: "var(--cam)", overflow: "hidden" }}>
            <div className="camhud">
              <span>
                {activeCam === "master"
                  ? `${currentAct.camLabel} · RULE: ${currentAct.ruleId} · 1280×720@25fps`
                  : activeCam === "cam-04"
                  ? "CAM-04 Dock A west · 1920×1080 · 60 fps · 38 ms"
                  : activeCam === "cam-01"
                  ? "CAM-01 Kitchen Prep · 1920×1080 · 60 fps · 29 ms"
                  : "CAM-07 Walk-in Cooler · FEED BLIND"}
              </span>
              <span style={{ color: isBreach ? "var(--color-bad)" : "var(--color-scene-cool)", fontWeight: 700 }}>
                {viewMode === "video"
                  ? `${currentAct.statusBadge} · TIER 1 ${currentAct.glimmerScore}`
                  : `${isBreach ? "BREACH ACTIVE · EXCLUSION 2.0 m" : "CLEAR"} · T ${
                      t > 0 ? `+${t.toFixed(2)}` : t.toFixed(2)
                    } s`}
              </span>
            </div>

            {viewMode === "video" ? (
              activeCam === "cam-07" ? (
                <div style={{ padding: "100px 20px", textAlign: "center", background: "var(--color-scene)", color: "var(--color-scene-accent)" }}>
                  <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ margin: "0 auto 16px", display: "block" }}>
                    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
                    <path d="M12 9v4M12 17h.01" />
                  </svg>
                  <div style={{ fontSize: "24px", fontWeight: 500 }}>
                    CAM-07 Walk-in Cooler · FEED BLIND
                  </div>
                  <p style={{ maxWidth: "560px", margin: "14px auto", color: "var(--color-on-scene-2)", fontSize: "14px", lineHeight: 1.5 }}>
                    Lens occlusion or connection drop detected at 14:12:08 UTC. Safety rules for this zone are suspended and cannot evaluate compliance. Operator manual clearance required.
                  </p>
                </div>
              ) : (
                <div style={{ position: "relative", width: "100%", background: "var(--color-scene)" }}>
                  <ClipPlayer
                    ref={videoRef}
                    key={activeCam}
                    poster={`/strips/${activeCam === "master" ? currentAct.camId.split(" ")[0] : activeCam === "cam-04" ? "CAM-04" : "CAM-05"}_strip_1.jpg`}
                    src={
                      activeCam === "master"
                        ? "/videos/master_capabilities_demo.mp4"
                        : activeCam === "cam-04"
                        ? "/videos/warehouse_forklift.mp4"
                        : "/videos/kitchen_prep.mp4"
                    }
                    autoPlay
                    loop
                    muted
                    playsInline
                    controls
                    onTimeUpdate={(e) => setVideoTime(e.currentTarget.currentTime)}
                    style={{
                      width: "100%",
                      maxHeight: "540px",
                      display: "block",
                      objectFit: "contain",
                      background: "var(--color-scene)",
                    }}
                  />
                </div>
              )
            ) : (
              <svg
                viewBox="0 0 1440 520"
                style={{ display: "block", width: "100%", height: "auto" }}
                role="img"
                aria-label="Overhead plan of the dock"
              >
                <defs>
                  <pattern id="hy" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
                    <rect width="6" height="12" fill="var(--color-accent)" fillOpacity=".22" />
                  </pattern>
                  <pattern id="hb" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                    <rect width="4" height="10" fill="var(--color-scene-cool)" fillOpacity=".3" />
                  </pattern>
                  <pattern id="gr" width="100" height="100" patternUnits="userSpaceOnUse">
                    <path d="M100 0H0V100" fill="none" stroke="#fff" strokeOpacity=".05" />
                  </pattern>
                </defs>

                <rect width="1440" height="520" fill="var(--color-scene-2)" />
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
                  stroke="var(--color-accent)"
                  strokeOpacity=".45"
                  strokeWidth="3"
                  strokeDasharray="22 12"
                />

                {/* Exclusion zone */}
                <polygon
                  points="520,262 780,180 780,420 520,338"
                  fill="url(#hy)"
                  stroke="var(--color-accent)"
                  strokeWidth="2"
                  strokeDasharray="8 6"
                />
                <text
                  x="520"
                  y="445"
                  fill="var(--color-accent)"
                  fontFamily="var(--font-barlow-condensed, sans-serif)"
                  fontWeight="600"
                  fontSize="20"
                  letterSpacing="1"
                >
                  EXCLUSION ZONE 2.0 m
                </text>

                {/* Forklift chassis */}
                <rect x="300" y="250" width="200" height="100" fill="url(#hb)" stroke="var(--color-scene-cool)" strokeWidth="2" />
                <rect x="300" y="250" width="66" height="100" fill="var(--color-scene-2)" stroke="var(--color-scene-cool)" strokeWidth="2" />
                <path d="M500 262H520M500 338H520" stroke="var(--color-scene-cool)" strokeWidth="5" />
                <path d="M360 250V214" stroke="var(--color-on-scene)" />
                <text
                  x="360"
                  y="204"
                  textAnchor="middle"
                  fill="var(--color-on-scene)"
                  fontFamily="var(--font-barlow, sans-serif)"
                  fontWeight="600"
                  fontSize="16"
                >
                  Forklift · 2.4 m/s
                </text>

                {/* Pedestrian */}
                <g transform={`translate(${wx}, 300)`}>
                  <ellipse rx="10" ry="22" fill={wc} fillOpacity=".3" stroke={wc} strokeWidth="2" />
                  <circle r="9" fill={wc} />
                  <circle r="44" fill="none" stroke={wc} strokeWidth="2" strokeDasharray="3 6" />
                  <path d="M0 44V70" stroke="var(--color-on-scene)" />
                  <text
                    x="0"
                    y="90"
                    textAnchor="middle"
                    fill="var(--color-on-scene)"
                    fontFamily="var(--font-barlow, sans-serif)"
                    fontWeight="600"
                    fontSize="16"
                  >
                    Person
                  </text>
                </g>

                {/* Measure callout */}
                <g stroke={wc} strokeWidth="2" fill="none">
                  <path d={`M520 168V262M${wx} 168V254M520 168H${wx}M520 168l9 -5M520 168l9 5M${wx} 168l-9 -5M${wx} 168l-9 5`} />
                </g>
                <g transform={`translate(${mid}, 130)`}>
                  <rect x="-56" y="-26" width="112" height="46" fill="var(--color-scene)" stroke={wc} strokeWidth="2" />
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
            )}
          </div>

          {/* Synchronized Active Scenario & Real-Time Tracked Objects Matrix */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              background: "var(--card)",
              padding: "18px 20px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "8px", marginBottom: "10px" }}>
              <div>
                <div className="small" style={{ fontWeight: 700, color: "var(--muted)" }}>
                  Active Scenario · Act {currentAct.id} of 7 ({currentAct.startS}s–{currentAct.endS}s)
                </div>
                <h2 className="h3" style={{ fontSize: "21px", marginTop: "2px" }}>
                  {currentAct.title}
                </h2>
              </div>
              <span className="dm small" style={{ fontWeight: 700 }}>
                {currentAct.calibration}
              </span>
            </div>

            <p className="small" style={{ marginBottom: "14px", padding: "8px 12px", background: "var(--paper)", borderLeft: "1px solid var(--color-rule-2)" }}>
              <strong>Compiled Rule ({currentAct.ruleId}):</strong> &ldquo;{currentAct.ruleText}&rdquo;
            </p>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: "10px" }}>
              {currentAct.entities.map((ent) => (
                <div
                  key={`${ent.id}-${ent.role}`}
                  style={{
                    padding: "10px 12px",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-pill)",
                    background:
                      ent.status === "bad"
                        ? "var(--color-bad-soft)"
                        : ent.status === "warn"
                        ? "var(--color-accent-soft)"
                        : "var(--color-paper)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong style={{ fontSize: "13px" }}>{ent.id}</strong>
                    <span className={`state ${ent.status === "bad" ? "bad" : ent.status === "warn" ? "warn" : "ok"}`} style={{ fontSize: "10px", padding: "1px 6px" }}>
                      {ent.status.toUpperCase()}
                    </span>
                  </div>
                  <div className="small" style={{ fontSize: "12px", marginTop: "2px" }}>{ent.role}</div>
                  <div className="dm" style={{ fontSize: "13px", fontWeight: 700, marginTop: "4px" }}>{ent.metric}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Panel: Timeline & Curve Scrubber */}
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
                alignItems: "baseline",
                flexWrap: "wrap",
                gap: "8px",
                marginBottom: "12px",
              }}
            >
              <h2 className="h3" style={{ fontSize: "22px" }}>
                The 1.5 Seconds Measured · Distance Curve
              </h2>
              <span className="small">
                Homography Ground-Plane · 10 Hz · Threshold <b>2.0 m</b>
              </span>
            </div>

            <svg
              viewBox="0 0 920 300"
              style={{ width: "100%", height: "auto", display: "block", background: "var(--paper)", border: "1px solid var(--color-rule)", borderRadius: "var(--radius-pill)" }}
              role="img"
              aria-label="Distance over time curve"
            >
              <g stroke="var(--color-scene-2)" strokeOpacity=".12">
                <line x1="60" y1="40" x2="860" y2="40" />
                <line x1="60" y1="100" x2="860" y2="100" />
                <line x1="60" y1="160" x2="860" y2="160" />
                <line x1="60" y1="220" x2="860" y2="220" />
              </g>

              <rect x="60" y="172" width="800" height="88" fill="var(--color-bad)" fillOpacity=".09" />
              <line x1="60" y1="172" x2="860" y2="172" stroke="var(--color-bad)" strokeWidth="2" strokeDasharray="8 6" />
              <text
                x="852"
                y="164"
                textAnchor="end"
                fontFamily="var(--font-barlow-condensed, sans-serif)"
                fontWeight="700"
                fontSize="14"
                fill="var(--color-bad)"
              >
                2.0 m THRESHOLD
              </text>

              <g
                fontFamily="var(--font-jetbrains-mono, monospace)"
                fontSize="12"
                fill="#5A5852"
                textAnchor="end"
              >
                <text x="50" y="44">4.0 m</text>
                <text x="50" y="104">3.0 m</text>
                <text x="50" y="176">2.0 m</text>
                <text x="50" y="224">1.0 m</text>
                <text x="50" y="264">0.0 m</text>
              </g>

              <g
                fontFamily="var(--font-jetbrains-mono, monospace)"
                fontSize="12"
                fill="#5A5852"
                textAnchor="middle"
              >
                <text x="60" y="284">-1.25 s</text>
                <text x="273" y="284">-0.85 s</text>
                <text x="487" y="284">-0.45 s</text>
                <text x="727" y="284">0.00 s · TRIP</text>
                <text x="860" y="284">+0.25 s</text>
              </g>

              <path
                d="M60 100 C 220 112, 340 148, 430 172 C 520 196, 620 218, 727 213 C 790 210, 830 202, 860 196"
                fill="none"
                stroke="var(--color-scene-2)"
                strokeWidth="3.5"
              />

              <line x1={px} y1="28" x2={px} y2="262" stroke="var(--color-bad)" strokeWidth="2" />
              <circle cx={px} cy={py} r="7" fill="var(--color-bad)" stroke="#F4F1EA" strokeWidth="2" />
            </svg>

            <div className="scrub" style={{ marginTop: "14px" }}>
              <label htmlFor="t" className="small" style={{ fontWeight: 700, color: "var(--ink)" }}>
                Scrub Radar Window
              </label>
              <input
                id="t"
                type="range"
                min="-1.25"
                max="0.25"
                step="0.01"
                value={t}
                onChange={(e) => {
                  setViewMode("radar");
                  setT(parseFloat(e.target.value));
                }}
                aria-label="Event time in seconds"
              />
              <output className="dm" style={{ fontSize: "16px", fontWeight: 700 }}>
                {t > 0 ? `+${t.toFixed(2)}` : t.toFixed(2)} s · {dText}
              </output>
            </div>
          </div>
        </div>

        {/* Right Column: 4 Cols (Live Telemetry + Tier-1 VLM Verdict + Merkle Hash Chain) */}
        {inspectorOpen && (
        <div className="s4" style={{ display: "flex", flexDirection: "column", gap: "var(--s3)" }}>
          {/* Panel: Live Measurements */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              background: "var(--card)",
              padding: "20px",
            }}
          >
            <div className="small" style={{ fontWeight: 700, marginBottom: "8px" }}>
              Live Telemetry · {currentAct.camId}
            </div>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", borderBottom: "1px solid var(--color-rule)", paddingBottom: "12px", marginBottom: "12px" }}>
              <span className="small">Tier-1 VLM Score</span>
              <div className="num" style={{ fontSize: "48px", color: wc }}>
                {currentAct.glimmerScore}
              </div>
            </div>

            <div className="kv">
              <div className="k">Active Rule</div>
              <div className="v dm">{currentAct.ruleId}</div>
            </div>
            <div className="kv">
              <div className="k">Tier-1 Verdict</div>
              <div className="v dm" style={{ color: wc, fontWeight: 700 }}>
                {currentAct.glimmerVerdict}
              </div>
            </div>
            <div className="kv">
              <div className="k">PLC / Action State</div>
              <div className="v dm" style={{ color: ilCol, fontWeight: 700 }}>
                {ilText}
              </div>
            </div>
            <div className="kv" style={{ borderBottom: "none" }}>
              <div className="k">Merkle Leaf #{frameNum}</div>
              <div className="v dm" style={{ fontSize: "12px" }}>
                sha256:{frameHex}…
              </div>
            </div>
          </div>

          {/* Panel: Tier-1 VLM Verification Reasoning */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              background: "var(--card)",
              padding: "20px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
              <h3 className="h3" style={{ fontSize: "20px" }}>
                Tier-1 VLM Review
              </h3>
              <span className={`state ${isBreach ? "bad" : "ok"}`} style={{ fontSize: "12px", padding: "2px 8px" }}>
                {currentAct.glimmerVerdict}
              </span>
            </div>

            <p className="small" style={{ marginBottom: "14px", color: "var(--ink)", lineHeight: 1.45 }}>
              {currentAct.glimmerReason}
            </p>

            {/* 6-Frame Contact Strip Indicator */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "4px", marginBottom: "14px" }}>
              {[-1.0, -0.75, -0.5, -0.25, 0.0, +0.25].map((offset, idx) => (
                <div
                  key={idx}
                  style={{
                    background: "var(--color-scene-2)",
                    border: idx === 4 ? "2px solid var(--alert, var(--color-bad))" : "1px solid var(--color-rule)",
                    padding: "6px 4px",
                    textAlign: "center",
                    color: "var(--color-on-scene)",
                  }}
                >
                  <div className="dm" style={{ fontSize: "10px", color: idx === 4 ? "var(--color-bad)" : "var(--color-scene-cool)" }}>
                    KF#{idx + 1}
                  </div>
                  <div className="dm" style={{ fontSize: "9px", opacity: 0.8 }}>
                    {offset >= 0 ? `+${offset.toFixed(2)}s` : `${offset.toFixed(2)}s`}
                  </div>
                </div>
              ))}
            </div>

            <div className="kv" style={{ padding: "8px 0" }}>
              <div className="k" style={{ fontSize: "13px" }}>Operator Warm-up (P1-07)</div>
              <div className="v dm" style={{ fontSize: "13px", color: "var(--ok)" }}>
                Active · IoU &ge; 0.65
              </div>
            </div>
            <div className="kv" style={{ padding: "8px 0", borderBottom: "none" }}>
              <div className="k" style={{ fontSize: "13px" }}>Rule CI Gate</div>
              <div className="v dm" style={{ fontSize: "13px" }}>
                16/16 Boundary · 6/6 Mutants
              </div>
            </div>
          </div>

          {/* Panel: Operator Actions & Cryptographic Chain */}
          <div
            style={{
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              background: "var(--card)",
              padding: "20px",
            }}
          >
            <h3 className="h3" style={{ fontSize: "20px", marginBottom: "8px" }}>
              Cryptographic Evidence Log
            </h3>
            <p className="small" style={{ marginBottom: "14px" }}>
              Every perception window, Tier-1 strip verdict, and PLC interlock action is chained with SHA-256.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <Link href="/audit" className="btn" style={{ width: "100%" }}>
                Verify SHA-256 Chain in Browser
              </Link>
              <Link href="/ci" className="btn o" style={{ width: "100%" }}>
                Inspect Rule CI Matrix (22/22)
              </Link>
            </div>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
