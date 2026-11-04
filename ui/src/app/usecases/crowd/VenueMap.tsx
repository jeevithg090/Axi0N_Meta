"use client";

import styles from "./crowd.module.css";
import type { Assessment, Place, RouteCheck } from "./crowdApi";
import { densityColour, fmt } from "./crowdApi";

const W = 640;
const COLUMN = 150;
const ROW = 70;

/** Places laid out left to right by how many links lead to them. */
function layout(places: Place[]): Map<string, { x: number; y: number }> {
  const names = new Set(places.map((p) => p.name));
  const incoming = new Map<string, number>(places.map((p) => [p.name, 0]));
  places.forEach((p) =>
    (p.leads_to ?? []).forEach((to) => {
      if (names.has(to)) incoming.set(to, (incoming.get(to) ?? 0) + 1);
    }),
  );
  const depth = new Map<string, number>();
  const queue = places.filter((p) => (incoming.get(p.name) ?? 0) === 0).map((p) => p.name);
  queue.forEach((n) => depth.set(n, 0));
  for (let i = 0; i < queue.length; i += 1) {
    const name = queue[i] ?? "";
    const here = depth.get(name) ?? 0;
    const place = places.find((p) => p.name === name);
    (place?.leads_to ?? []).forEach((to) => {
      if (names.has(to) && !depth.has(to)) {
        depth.set(to, here + 1);
        queue.push(to);
      }
    });
  }
  places.forEach((p) => {
    if (!depth.has(p.name)) depth.set(p.name, 0);
  });
  const rows = new Map<number, number>();
  const out = new Map<string, { x: number; y: number }>();
  places.forEach((p) => {
    const d = depth.get(p.name) ?? 0;
    const r = rows.get(d) ?? 0;
    rows.set(d, r + 1);
    out.set(p.name, { x: 70 + d * COLUMN, y: 40 + r * ROW });
  });
  return out;
}

/** The venue's places and links; colour is the measured density, outlines mark Spark's targets. */
export function VenueMap({
  places,
  sample,
  assessment,
  routes,
}: {
  places: Place[];
  sample: boolean;
  assessment: Assessment | null;
  routes: RouteCheck[];
}) {
  if (places.length === 0) {
    return <p className={styles.muted}>No venue layout yet (PUT /crowd/venue).</p>;
  }
  const at = layout(places);
  const height = Math.max(...[...at.values()].map((p) => p.y)) + 50;
  const targets = new Map((assessment?.actions ?? []).map((a) => [a.target.toLowerCase(), a.do]));
  const destinations = new Set((assessment?.actions ?? []).map((a) => (a.to ?? "").toLowerCase()));
  const filling = new Set(routes.filter((r) => r.filling).map((r) => r.to.toLowerCase()));
  return (
    <div>
      {sample && <span className={styles.sample}>SAMPLE layout</span>}
      <svg width="100%" viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Venue layout">
        <defs>
          <marker id="venue-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="currentColor" />
          </marker>
        </defs>
        {places.flatMap((p) =>
          (p.leads_to ?? []).map((to) => {
            const a = at.get(p.name);
            const b = at.get(to);
            if (!a || !b) return null;
            return (
              <line
                key={`${p.name}->${to}`}
                x1={a.x + 50}
                y1={a.y}
                x2={b.x - 52}
                y2={b.y}
                stroke="currentColor"
                strokeOpacity={0.6}
                markerEnd="url(#venue-arrow)"
              />
            );
          }),
        )}
        {places.map((p) => {
          const pos = at.get(p.name);
          if (!pos) return null;
          const key = p.name.toLowerCase();
          const action = targets.get(key);
          const density = p.now?.density_ppm2;
          return (
            <g key={p.name}>
              <rect
                x={pos.x - 50}
                y={pos.y - 22}
                width={100}
                height={44}
                rx={8}
                fill={p.now ? densityColour(density, 0.45) : "transparent"}
                stroke={
                  filling.has(key)
                    ? "#c62828"
                    : action || destinations.has(key)
                      ? "#ff9800"
                      : "currentColor"
                }
                strokeWidth={action || destinations.has(key) || filling.has(key) ? 3 : 1}
                strokeDasharray={p.camera_id ? undefined : "4 3"}
              />
              <text x={pos.x} y={pos.y - 4} textAnchor="middle" fontSize={11} fill="currentColor">
                {p.name.length > 18 ? `${p.name.slice(0, 17)}…` : p.name}
              </text>
              <text x={pos.x} y={pos.y + 12} textAnchor="middle" fontSize={10} fill="currentColor">
                {p.now ? `${fmt(density)}/m² · ${p.now.people}` : p.kind || "no camera"}
              </text>
              {action && (
                <text
                  x={pos.x}
                  y={pos.y - 27}
                  textAnchor="middle"
                  fontSize={11}
                  fill="#ff9800"
                  fontWeight={700}
                >
                  {action.toUpperCase()}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <p className={styles.small}>
        Filled boxes are seen by a camera (colour = people per m²); dashed boxes are not measured.
        Orange: places Muse Spark acts on or sends people to; red: a destination the route guard
        found filling.
      </p>
    </div>
  );
}
