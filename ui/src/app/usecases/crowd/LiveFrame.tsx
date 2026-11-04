"use client";

import { useState } from "react";

import styles from "./crowd.module.css";
import type { CameraStatus, CrowdZone } from "./crowdApi";
import { densityColour, fileUrl, fmt } from "./crowdApi";

type Mode = "heatmap" | "frame";

const ARROW_MAX = 40;

/** The newest frame (or its heatmap) with zones, density cells, flow and counterflow drawn on. */
export function LiveFrame({
  camera,
  extraZones = [],
}: {
  camera: CameraStatus;
  extraZones?: CrowdZone[];
}) {
  const [mode, setMode] = useState<Mode>("frame");
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const latest = camera.latest;
  const name = mode === "heatmap" ? latest?.heatmap : latest?.frame;
  if (!latest || !name) return <p className={styles.muted}>No reading yet.</p>;
  const cells = latest.cells;
  const cols = cells.reduce((m, c) => Math.max(m, c.col + 1), 0) || 8;
  const rows = cells.reduce((m, c) => Math.max(m, c.row + 1), 0) || 6;
  const zones = [...(camera.watched?.zones ?? []), ...extraZones];
  const byZone = new Map(latest.zones.map((z) => [z.zone, z]));
  return (
    <div>
      <div className={styles.row}>
        {(["frame", "heatmap"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            className={mode === m ? styles.tabActive : styles.tab}
            onClick={() => setMode(m)}
          >
            {m === "frame" ? "Frame + overlay" : "Heatmap (as Glimmer sees it)"}
          </button>
        ))}
        <span className={styles.small}>
          {latest.people} people · counted as “{latest.concept}” · calibration {latest.calibration}
          {latest.calibration_err_m != null ? ` (±${fmt(latest.calibration_err_m, 2)} m)` : ""}
        </span>
      </div>
      <div className={styles.frame}>
        {/* eslint-disable-next-line @next/next/no-img-element -- live API image, not optimisable */}
        <img
          src={fileUrl(name)}
          alt={`Newest ${mode} of ${camera.name}`}
          onLoad={(e) =>
            setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
          }
        />
        {size && mode === "frame" && (
          <svg className={styles.overlay} viewBox={`0 0 ${size.w} ${size.h}`} aria-hidden>
            {cells.map((c) => {
              const w = size.w / cols;
              const h = size.h / rows;
              const x = c.col * w;
              const y = c.row * h;
              const flow = c.flow_px;
              const len = flow ? Math.hypot(flow[0], flow[1]) : 0;
              const k = len > 0 ? Math.min(ARROW_MAX, len) / len : 0;
              return (
                <g key={`${c.row}-${c.col}`}>
                  <rect
                    x={x}
                    y={y}
                    width={w}
                    height={h}
                    fill={c.people ? densityColour(c.density_ppm2, 0.35) : "none"}
                    stroke={c.counterflow_share ? "#ff00ff" : "none"}
                    strokeWidth={1 + 5 * (c.counterflow_share ?? 0)}
                  />
                  {c.people > 0 && c.density_ppm2 != null && (
                    <text
                      x={x + 4}
                      y={y + 14}
                      fontSize={12}
                      fill="#fff"
                      stroke="#000"
                      strokeWidth={0.6}
                    >
                      {fmt(c.density_ppm2)}
                    </text>
                  )}
                  {flow && len > 0.5 && (
                    <line
                      x1={x + w / 2}
                      y1={y + h / 2}
                      x2={x + w / 2 + flow[0] * k}
                      y2={y + h / 2 + flow[1] * k}
                      stroke="#fff"
                      strokeWidth={2.5}
                      markerEnd="url(#crowd-arrow)"
                    />
                  )}
                </g>
              );
            })}
            {zones.map((z) => {
              const reading = byZone.get(z.name);
              const points = z.polygon_px.map(([x, y]) => `${x},${y}`).join(" ");
              const [lx, ly] = z.polygon_px[0] ?? [0, 0];
              return (
                <g key={z.name}>
                  <polygon
                    points={points}
                    fill="none"
                    stroke="#ffeb3b"
                    strokeWidth={3}
                    strokeDasharray="10 6"
                  />
                  <text
                    x={Math.max(4, Math.min(size.w - 200, lx))}
                    y={Math.max(16, Math.min(size.h - 6, ly))}
                    fontSize={16}
                    fill="#ffeb3b"
                    stroke="#000"
                    strokeWidth={0.8}
                  >
                    {z.name}
                    {reading?.density_ppm2 != null ? `: ${fmt(reading.density_ppm2)}/m²` : ""}
                  </text>
                </g>
              );
            })}
            <defs>
              <marker
                id="crowd-arrow"
                markerWidth="6"
                markerHeight="6"
                refX="3"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L6,3 L0,6 z" fill="#fff" />
              </marker>
            </defs>
          </svg>
        )}
      </div>
      <p className={styles.small}>
        Cells tinted by people per m² (blue low, red high), arrows show one second of mean motion,
        magenta borders mark counterflow (people moving against each other). Yellow: named zones.
      </p>
    </div>
  );
}
