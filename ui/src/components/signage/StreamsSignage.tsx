"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { api, type CameraInfo } from "../../lib/api";
import { SampleDataTag } from "../SampleDataTag";

interface FleetCamera {
  id: string;
  cameraId: string;
  name: string;
  where: string;
  videoSrc: string | null;
  rules: string[];
  ruleBadge: string;
  lat: string;
  state: "OK" | "BREACH" | "BLIND";
  blind: boolean;
  lastChunkAt?: string | null;
}

const FLEET_SPEC: FleetCamera[] = [
  {
    id: "01",
    cameraId: "CAM-01",
    name: "CAM-01 Dock A East",
    where: "North Loading Bay 1–3 · Multi-Worker Patrol",
    videoSrc: "/videos/cam01_dock.mp4",
    rules: ["keep-people-2m-from-moving-forklift"],
    ruleBadge: "Forklift 2.0m Guard · OSHA 1910.178",
    lat: "32 ms",
    state: "OK",
    blind: false,
  },
  {
    id: "02",
    cameraId: "CAM-02",
    name: "CAM-02 Aisle B2 Intersection",
    where: "Main Forklift Crossing · Blind Corner",
    videoSrc: "/videos/cam02_intersection.mp4",
    rules: ["keep-people-2m-from-moving-forklift"],
    ruleBadge: "BREACH: 1.18m < 2.00m (Act-First Horn)",
    lat: "38 ms",
    state: "BREACH",
    blind: false,
  },
  {
    id: "03",
    cameraId: "CAM-03",
    name: "CAM-03 East Visitor Corridor",
    where: "Mandatory Hardhat Zone · Tier-0 Head Zoom",
    videoSrc: "/videos/cam03_ppe.mp4",
    rules: ["require-hardhat-in-visitor-corridor"],
    ruleBadge: "BREACH: 3 Unhelmeted Visitors",
    lat: "41 ms",
    state: "BREACH",
    blind: false,
  },
  {
    id: "04",
    cameraId: "CAM-04",
    name: "CAM-04 Staging Bay C4",
    where: "Forklift Cab & 48×40in Reference Pallets",
    videoSrc: "/videos/cam04_forklift.mp4",
    rules: ["keep-people-2m-from-moving-forklift"],
    ruleBadge: "Operator Excluded (Seated in Cab)",
    lat: "34 ms",
    state: "OK",
    blind: false,
  },
  {
    id: "05",
    cameraId: "CAM-05",
    name: "CAM-05 Commercial Kitchen A",
    where: "Prep Line A · Sanitation & Hairnet Check",
    videoSrc: "/videos/cam05_kitchen.mp4",
    rules: ["commercial-kitchen-prep-sanitation-guard"],
    ruleBadge: "4/4 Chefs Compliant · FDA §2-402.11",
    lat: "29 ms",
    state: "OK",
    blind: false,
  },
  {
    id: "06",
    cameraId: "CAM-06",
    name: "CAM-06 West Assembly Zone",
    where: "Blueprint Review Table · Floor Supervision",
    videoSrc: "/videos/cam06_altercation.mp4",
    rules: ["require-hardhat-in-visitor-corridor"],
    ruleBadge: "BREACH: Physical Shove Detected",
    lat: "31 ms",
    state: "BREACH",
    blind: false,
  },
  {
    id: "07",
    cameraId: "CAM-07",
    name: "CAM-07 North Stairwell Cooler",
    where: "Cold Storage Entrance · Optical Feed Blocked",
    videoSrc: null,
    rules: [],
    ruleBadge: "UNCHECKED · Refuses to Guess",
    lat: "Offline",
    state: "BLIND",
    blind: true,
  },
  {
    id: "08",
    cameraId: "CAM-08",
    name: "CAM-08 South Freight Gate",
    where: "Backing Truck Apron · Rear Exclusion Zone",
    videoSrc: "/videos/cam08_packaging.mp4",
    rules: ["heavy-equipment-swing-radius-exclusion"],
    ruleBadge: "BREACH: Pedestrian 1.62m Behind Truck",
    lat: "35 ms",
    state: "BREACH",
    blind: false,
  },
  {
    id: "09",
    cameraId: "CAM-09",
    name: "CAM-09 Yard Heavy Equipment",
    where: "Excavator Boom Swing Radius (3.5m)",
    videoSrc: "/videos/cam09_excavator.mp4",
    rules: ["heavy-equipment-swing-radius-exclusion"],
    ruleBadge: "BREACH: Boom Swing 2.14m < 3.50m",
    lat: "36 ms",
    state: "BREACH",
    blind: false,
  },
  {
    id: "10",
    cameraId: "CAM-10",
    name: "CAM-10 High-Bay Pallet Racking",
    where: "Aisle 4 North · Reference Homography",
    videoSrc: "/videos/cam10_pallets.mp4",
    rules: ["keep-people-2m-from-moving-forklift"],
    ruleBadge: "Calibrated ±0.094m · Clear",
    lat: "33 ms",
    state: "OK",
    blind: false,
  },
  {
    id: "11",
    cameraId: "CAM-11",
    name: "CAM-11 Receiving Bay D1",
    where: "Active Forklift Transport Lane",
    videoSrc: "/videos/cam11_receiving.mp4",
    rules: ["keep-people-2m-from-moving-forklift"],
    ruleBadge: "Forklift Moving 1.4m/s · Clear",
    lat: "33 ms",
    state: "OK",
    blind: false,
  },
  {
    id: "12",
    cameraId: "CAM-12",
    name: "CAM-12 North Mezzanine Walkway",
    where: "Emergency Corridor & PPE Patrol",
    videoSrc: "/videos/cam12_corridor.mp4",
    rules: ["require-hardhat-in-visitor-corridor"],
    ruleBadge: "Hardhat Verified · Clear",
    lat: "30 ms",
    state: "OK",
    blind: false,
  },
];

export function StreamsSignage() {
  const [filter, setFilter] = useState<"all" | "ok" | "breach" | "bad">("all");
  const [liveCameras, setLiveCameras] = useState<Record<string, CameraInfo>>({});
  const [cameraList, setCameraList] = useState<CameraInfo[] | null>(null);
  const [cameraError, setCameraError] = useState<boolean>(false);
  const [selectedCam, setSelectedCam] = useState<FleetCamera | null>(null);

  useEffect(() => {
    api
      .cameras()
      .then((list) => {
        const map: Record<string, CameraInfo> = {};
        for (const c of list) {
          map[c.camera_id] = c;
        }
        setLiveCameras(map);
        setCameraList(list);
      })
      .catch(() => {
        // The API is not reachable: say so; the sample fleet below stays labelled as sample.
        setCameraError(true);
      });
  }, []);

  const enriched = FLEET_SPEC.map((c) => ({
    ...c,
    lastChunkAt: liveCameras[c.cameraId]?.last_chunk_at ?? null,
  }));

  const filtered = enriched.filter((c) => {
    if (filter === "all") return true;
    if (filter === "ok") return !c.blind;
    if (filter === "breach") return c.state === "BREACH";
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
              Camera Fleet &nbsp;·&nbsp;{" "}
              <b>
                GET /cameras (
                {cameraList ? `${cameraList.length} reported` : cameraError ? "not reachable" : "loading…"})
              </b>
            </div>
            <h1 className="h1">
              {cameraList
                ? `${cameraList.length} camera${cameraList.length === 1 ? "" : "s"} reported by the server.`
                : cameraError
                  ? "The Rulecam server is not reachable."
                  : "Asking the server for its cameras…"}
            </h1>
          </div>
          <div className="s3 end">
            <p className="small">
              A blind camera is never shown as compliant. Its rules are marked unchecked in the
              evidence chain until the lens is cleared.
            </p>
          </div>
        </div>
      </section>

      {/* Cameras the API reports (GET /cameras), the only live data on this page */}
      <section className="wrap sec" aria-label="Cameras reported by the server">
        {cameraList && cameraList.length > 0 ? (
          <table className="small" style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Camera</th>
                <th style={{ textAlign: "left" }}>Label</th>
                <th style={{ textAlign: "left" }}>Source</th>
                <th style={{ textAlign: "left" }}>Last chunk</th>
              </tr>
            </thead>
            <tbody>
              {cameraList.map((c) => (
                <tr key={c.camera_id}>
                  <td className="dm">{c.camera_id}</td>
                  <td>{c.label}</td>
                  <td>{c.source}</td>
                  <td className="dm">{c.last_chunk_at ?? "none yet"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="small">
            {cameraError
              ? "No live camera data: the server did not answer."
              : cameraList
                ? "The server reports no cameras yet. Pair a phone from the planner to add one."
                : "Loading…"}
          </p>
        )}
      </section>

      <div className="tape" />

      <section className="wrap sec">
        <SampleDataTag what="The fleet matrix below (12 named cameras, their latencies, states, breaches and rule badges) is a built-in example layout; only the 'last chunk' times come from the server." />
      </section>

      {/* Filter and Model Telemetry Bar */}
      <section className="wrap sec">
        <div className="grid" style={{ alignItems: "end" }}>
          <div className="s7">
            <span className="small" style={{ marginRight: "8px" }}>Filter Fleet:</span>
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
              Watching (11)
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "breach"}
              onClick={() => setFilter("breach")}
            >
              Active Breaches (5)
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={filter === "bad"}
              onClick={() => setFilter("bad")}
            >
              Blind / Unchecked (1)
            </button>
          </div>

          <div
            className="s5 mono"
            style={{ display: "flex", flexWrap: "wrap", gap: "var(--s1) var(--s3)", justifyContent: "flex-end" }}
          >
            <span>SAM 3.1 Keyframes · 38 ms</span>
            <span>Glimmer 30B · 412 ms</span>
            <Link href="/monitor" style={{ textDecoration: "underline", fontWeight: 700 }}>
              Open 7-Act Master Wall →
            </Link>
          </div>
        </div>

        {/* Selected Camera Modal / Expanded Inspector */}
        {selectedCam && (
          <div
            style={{
              marginTop: "var(--s3)",
              padding: "var(--s3)",
              background: "#0F1112",
              color: "#ECEAE3",
              border: "2px solid var(--yellow)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
              <div>
                <span className="mono" style={{ color: "#F2D100" }}>
                  LIVE STREAM INSPECTOR · {selectedCam.cameraId}
                </span>
                <h3 className="h3" style={{ color: "#ECEAE3", marginTop: "2px" }}>
                  {selectedCam.name} — {selectedCam.where}
                </h3>
              </div>
              <div style={{ display: "flex", gap: "8px" }}>
                <Link
                  href={`/worker?camera=${encodeURIComponent(selectedCam.cameraId)}`}
                  className="btn"
                  style={{ background: "#F2D100", color: "#141414", padding: "6px 12px", fontSize: "13px" }}
                >
                  View Worker Rules Card
                </Link>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => setSelectedCam(null)}
                  style={{ color: "#ECEAE3", borderColor: "#ECEAE3", padding: "6px 12px", fontSize: "13px" }}
                >
                  Close ✕
                </button>
              </div>
            </div>
            {selectedCam.videoSrc && (
              <video
                src={selectedCam.videoSrc}
                controls
                autoPlay
                loop
                muted
                playsInline
                style={{ width: "100%", maxHeight: "460px", objectFit: "contain", background: "#000" }}
              />
            )}
          </div>
        )}

        {/* Camera Cards Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(310px, 1fr))",
            gap: "var(--gap)",
            marginTop: "var(--s4)",
          }}
        >
          {filtered.map((c) => (
            <article
              key={c.id}
              style={{
                borderTop:
                  c.state === "BLIND"
                    ? "4px solid var(--alert)"
                    : c.state === "BREACH"
                      ? "4px solid #FF5A2B"
                      : "4px solid var(--ink)",
                paddingTop: "var(--s2)",
                background: "var(--paper)",
              }}
            >
              {c.videoSrc ? (
                <div
                  onClick={() => setSelectedCam(c)}
                  style={{
                    position: "relative",
                    width: "100%",
                    height: "190px",
                    background: "#0a0c0e",
                    overflow: "hidden",
                    cursor: "pointer",
                  }}
                  title="Click to expand HD stream"
                >
                  <video
                    src={c.videoSrc}
                    autoPlay
                    loop
                    muted
                    playsInline
                    style={{ width: "100%", height: "190px", objectFit: "cover", display: "block" }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      top: 8,
                      left: 8,
                      background: "rgba(10,12,14,0.82)",
                      padding: "2px 7px",
                      fontFamily: "var(--font-dm-mono, monospace)",
                      fontSize: "10px",
                      color: "#ECEAE3",
                      border: "1px solid rgba(255,255,255,0.2)",
                    }}
                  >
                    ● REC · {c.cameraId} · 1280×720 25fps
                  </div>
                  <div
                    style={{
                      position: "absolute",
                      bottom: 6,
                      left: 8,
                      right: 8,
                      background:
                        c.state === "BREACH"
                          ? "rgba(201, 52, 16, 0.92)"
                          : "rgba(10, 12, 14, 0.85)",
                      padding: "3px 8px",
                      fontSize: "11px",
                      color: c.state === "BREACH" ? "#fff" : "#3DB8FF",
                      fontWeight: 700,
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span>{c.ruleBadge}</span>
                    <span>EXPAND ↗</span>
                  </div>
                </div>
              ) : (
                <svg
                  viewBox="0 0 320 190"
                  style={{ display: "block", width: "100%", height: "190px", background: "var(--cam)" }}
                  role="img"
                  aria-label={c.name}
                >
                  <rect width="320" height="190" fill="#2A1510" />
                  <path
                    d="M0 40L140 0M0 100L200 0M0 160L260 0M40 190L320 20M120 190L320 80M200 190L320 140"
                    stroke="#FF5A2B"
                    strokeOpacity=".45"
                    strokeWidth="6"
                  />
                  <rect x="30" y="68" width="260" height="54" fill="#0F1112" stroke="#FF5A2B" strokeWidth="2" />
                  <text
                    x="160"
                    y="92"
                    textAnchor="middle"
                    fontFamily="var(--font-barlow-condensed, sans-serif)"
                    fontWeight="700"
                    fontSize="19"
                    fill="#FF5A2B"
                  >
                    OPTICAL FEED BLOCKED · BLIND
                  </text>
                  <text
                    x="160"
                    y="112"
                    textAnchor="middle"
                    fontFamily="var(--font-dm-mono, monospace)"
                    fontSize="10"
                    fill="#ECEAE3"
                  >
                    Coverage Watchdog: Rules Marked UNCHECKED
                  </text>
                </svg>
              )}

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "baseline",
                  gap: "var(--s1)",
                  margin: "var(--s2) 0 var(--s1)",
                }}
              >
                <h3 className="h3" style={{ fontSize: "22px" }}>
                  {c.name}
                </h3>
                <span className={`state ${c.state === "OK" ? "ok" : "bad"}`}>{c.state}</span>
              </div>

              <div className="small" style={{ marginBottom: "var(--s1)" }}>
                {c.where}
              </div>

              <div className="kv" style={{ borderTop: "1px solid var(--ink)", padding: "6px 0", fontSize: "13px" }}>
                <span>Enforced Rule</span>
                <b className="dm" style={{ fontSize: "11px" }}>
                  {c.rules[0] ?? "UNCHECKED (Lens Blocked)"}
                </b>
              </div>
              <div className="kv" style={{ padding: "6px 0", fontSize: "13px" }}>
                <span>Chunk Telemetry</span>
                <b className="dm">
                  {c.lastChunkAt ? `Synced ${c.lastChunkAt.slice(11, 19)} UTC · ${c.lat}` : c.lat}
                </b>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
