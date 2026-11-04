"use client";

import styles from "./crowd.module.css";
import type { Assessment, CameraStatus, PhaseMeter, RouteCheck, ZoneForecast } from "./crowdApi";
import { LEVEL_COLOUR, densityColour, fmt, seconds } from "./crowdApi";

const REFERENCES = ["4", "5", "6"];

function Sparkline({ values }: { values: (number | null)[] }) {
  const points = values.map((v, k) => [k, v ?? 0] as const);
  if (points.length < 2) return null;
  const top = Math.max(6, ...points.map(([, v]) => v));
  const w = 200;
  const h = 40;
  const path = points
    .map(([k, v], i) => `${i ? "L" : "M"}${(k / (points.length - 1)) * w},${h - (v / top) * h}`)
    .join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      {[4, 6].map((ref) => (
        <line
          key={ref}
          x1={0}
          x2={w}
          y1={h - (ref / top) * h}
          y2={h - (ref / top) * h}
          stroke="rgba(128,128,128,0.5)"
          strokeDasharray="3 3"
        />
      ))}
      <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}

/** The phase meter: speed over the last minutes, its stop-and-go swings, the pressure. */
export function PhaseView({ phase }: { phase: PhaseMeter }) {
  return (
    <div className={styles.small}>
      <strong>Phase meter</strong> ({Math.round(phase.window_s)} s): speed {fmt(phase.speed_mps, 2)}{" "}
      m/s
      {phase.speed_per_min != null
        ? ` (${phase.speed_per_min >= 0 ? "+" : ""}${fmt(phase.speed_per_min, 2)}/min)`
        : ""}
      {" · "}stop-and-go swings {phase.swings}
      {phase.swing_share != null ? ` (±${Math.round(phase.swing_share * 50)}%)` : ""}
      {" · "}pressure {fmt(phase.pressure, 3)} (peak {fmt(phase.pressure_peak, 3)})
      <SpeedLine speeds={phase.speeds} />
    </div>
  );
}

function SpeedLine({ speeds }: { speeds: number[] }) {
  if (speeds.length < 2) return null;
  const top = Math.max(0.5, ...speeds);
  const w = 200;
  const h = 30;
  const path = speeds
    .map((v, k) => `${k ? "L" : "M"}${(k / (speeds.length - 1)) * w},${h - (v / top) * h}`)
    .join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      <path d={path} fill="none" stroke="#00897b" strokeWidth={1.5} />
    </svg>
  );
}

function Countdown({ forecast }: { forecast: ZoneForecast | undefined }) {
  if (!forecast) return null;
  const rows = REFERENCES.map((ref) => ({
    ref,
    trend: forecast.eta_s[ref],
    fill: forecast.fill_eta_s?.[ref],
    above: forecast.above_s[ref] ?? 0,
  }));
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th>per m²</th>
          <th title="The last minute's density line">trend</th>
          <th title="Net people crossing the zone's edge (optical flow)">fill clock</th>
          <th>held for</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.ref}>
            <td>{r.ref}</td>
            <td>{r.trend != null ? `in ${seconds(r.trend)}` : "–"}</td>
            <td>{r.fill != null ? `in ${seconds(r.fill)}` : "–"}</td>
            <td>{r.above > 0 ? seconds(r.above) : "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Each zone: people, density, motion, the edge flow and the measured countdown. */
export function ZonePanel({ camera }: { camera: CameraStatus }) {
  const latest = camera.latest;
  if (!latest) return null;
  const forecasts = new Map(camera.forecast.map((f) => [f.zone, f]));
  return (
    <div className={styles.zones}>
      {latest.zones.map((z) => {
        const f = forecasts.get(z.zone);
        return (
          <div
            key={z.zone}
            className={styles.card}
            style={{ borderColor: densityColour(z.density_ppm2) }}
          >
            <h3>{z.zone === "view" ? "Whole view" : z.zone}</h3>
            <div className={styles.row}>
              <span className={styles.big}>{fmt(z.density_ppm2)}</span>
              <span>people/m² · {z.people} people</span>
            </div>
            <span className={styles.small}>
              {f?.per_min != null
                ? `${f.per_min >= 0 ? "+" : ""}${fmt(f.per_min, 2)}/m² per min · `
                : ""}
              speed {fmt(z.speed_mps, 2)} m/s · counterflow{" "}
              {fmt((z.counterflow_share ?? 0) * 100, 0)}% · pressure {fmt(z.pressure, 3)}
            </span>
            {f?.in_per_min != null && (
              <span className={styles.small}>
                edge flow: {fmt(f.in_per_min, 0)} in / {fmt(f.out_per_min, 0)} out per min
              </span>
            )}
            <Sparkline values={camera.zone_series[z.zone]?.density ?? []} />
            <Countdown forecast={f} />
            {f?.phase && <PhaseView phase={f.phase} />}
          </div>
        );
      })}
    </div>
  );
}

/** Muse Spark's judgement: level, why, forecast, the actions tied to venue places. */
export function AssessmentCard({ camera }: { camera: CameraStatus }) {
  const a = camera.assessment;
  if (!a) return <p className={styles.muted}>Waiting for Muse Spark’s first assessment.</p>;
  return (
    <AssessmentView
      assessment={a}
      routes={camera.last_alert?.route_checks ?? []}
      readingAt={camera.assessed_at}
      seconds={camera.assess_seconds}
    />
  );
}

/** One assessment of Muse Spark's, with the route guard's checks behind its last alert. */
export function AssessmentView({
  assessment: a,
  routes,
  readingAt,
  seconds,
}: {
  assessment: Assessment;
  routes: RouteCheck[];
  readingAt: string | null;
  seconds: number | null;
}) {
  return (
    <div className={styles.card}>
      <div className={styles.row}>
        <span className={styles.badge} style={{ background: LEVEL_COLOUR[a.level] }}>
          {a.level}
        </span>
        <strong>Muse Spark 1.3</strong>
        <span className={styles.small}>
          reading {new Date(readingAt ?? "").toLocaleTimeString([], { hour12: false })} ·{" "}
          {fmt(seconds)} s
        </span>
        {a.phase && (
          <span className={styles.badge} style={{ background: "#455a64" }}>
            {a.phase}
          </span>
        )}
        {a.minutes_to_danger != null && (
          <strong>dangerous in ~{fmt(a.minutes_to_danger, 1)} min</strong>
        )}
      </div>
      <p style={{ margin: 0 }}>{a.why}</p>
      <p style={{ margin: 0 }}>
        <em>Next: </em>
        {a.forecast}
      </p>
      {a.actions.length > 0 ? (
        <ul className={styles.list}>
          {a.actions.map((x, k) => (
            <li key={k}>
              <strong>{x.do}</strong> {x.target}
              {x.to ? ` → ${x.to}` : ""} <span className={styles.muted}>({x.role})</span> — {x.why}
            </li>
          ))}
        </ul>
      ) : (
        a.staff_actions.length > 0 && (
          <ul className={styles.list}>
            {a.staff_actions.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        )
      )}
      {routes.length > 0 && (
        <div className={styles.small}>
          <strong>Route guard (last alert): </strong>
          {routes.map((r, k) => (
            <span key={k} style={{ marginRight: 8 }}>
              {r.to}: {r.measured ? `${fmt(r.density_ppm2)}/m², ${r.note}` : r.note}
              {r.replaced ? " → Spark chose again" : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
