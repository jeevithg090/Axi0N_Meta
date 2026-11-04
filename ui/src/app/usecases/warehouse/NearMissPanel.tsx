"use client";

// The near-miss log of the day and the heatmap of where they happened on the camera's floor.

import { heatFill, metres, type Heatmap, type NearMiss } from "./warehouse";
import styles from "./warehouse.module.css";

export function HeatmapView({ heat, frameUrl }: { heat: Heatmap; frameUrl: string | null }) {
  const max = Math.max(0, ...heat.cells.flat());
  const cellW = heat.width / heat.cols;
  const cellH = heat.height / heat.rows;
  return (
    <div className={styles.heat}>
      {frameUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={frameUrl} alt={`Recorded frame of ${heat.camera_id}`} />
      ) : (
        <svg viewBox={`0 0 ${heat.width} ${heat.height}`} style={{ position: "static" }} aria-hidden="true" />
      )}
      <svg viewBox={`0 0 ${heat.width} ${heat.height}`} role="img" aria-label="Near-miss heatmap">
        {heat.cells.map((row, r) =>
          row.map((count, c) => (
            <rect
              key={`${r}-${c}`}
              x={c * cellW}
              y={r * cellH}
              width={cellW}
              height={cellH}
              fill={heatFill(count, max)}
            />
          )),
        )}
        {heat.points.map((p) => (
          <circle
            key={p.event_id}
            cx={p.x}
            cy={p.y}
            r={Math.max(6, heat.width / 120)}
            fill={p.alert ? "#dc2626" : "#f59e0b"}
            stroke="#fff"
            strokeWidth={2}
          >
            <title>{p.event_id}</title>
          </circle>
        ))}
      </svg>
    </div>
  );
}

export function NearMissPanel({
  rows,
  heat,
  frameUrl,
  selected,
  onSelect,
}: {
  rows: NearMiss[];
  heat: Heatmap | null;
  frameUrl: string | null;
  selected: string | null;
  onSelect: (eventId: string) => void;
}) {
  return (
    <section className={styles.card} aria-labelledby="wh-near">
      <h2 id="wh-near">Near-miss log and heatmap</h2>
      <p className={styles.muted}>
        Every time a person came inside the owner&apos;s own distance of a moving vehicle today, as
        measured on the calibrated floor; the verifier&apos;s verdict says whether it was real.
      </p>
      {heat && (
        <>
          <HeatmapView heat={heat} frameUrl={frameUrl} />
          <p className={styles.muted}>
            {heat.points.length} placed on {heat.camera_id}
            {heat.unplaced ? `, ${heat.unplaced} without a kept position` : ""}. Red: alerted;
            amber: not alerted.
          </p>
        </>
      )}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Time</th>
              <th>Camera</th>
              <th>Gap</th>
              <th>Limit</th>
              <th>Speed</th>
              <th>Verdict</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.event_id}
                onClick={() => onSelect(row.event_id)}
                style={{
                  cursor: "pointer",
                  fontWeight: row.event_id === selected ? 600 : undefined,
                }}
              >
                <td>{new Date(row.ts).toLocaleTimeString()}</td>
                <td className={styles.mono}>{row.camera_id}</td>
                <td>{metres(row.separation_m)}</td>
                <td>{metres(row.limit_m)}</td>
                <td>
                  {row.vehicle_speed_mps === null ? "–" : `${row.vehicle_speed_mps.toFixed(2)} m/s`}
                </td>
                <td>
                  {row.verdict ?? "…"}
                  {row.verdict_score !== null ? ` ${row.verdict_score.toFixed(2)}` : ""}
                </td>
                <td>
                  {row.alert === null ? "pending" : row.alert ? "alert" : "suppressed"}
                  {row.acked_by.length ? " · acked" : ""}
                  {row.disputes.length ? ` · disputed (${row.disputes.join("/")})` : ""}
                  {row.regression ? " · regression" : ""}
                  {row.training_done ? " · trained" : ""}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className={styles.muted}>
                  No near miss today.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
