"use client";

import { useState } from "react";

import styles from "./crowd.module.css";
import type { EventReport, TruthZone } from "./crowdApi";
import { LEVEL_COLOUR, clock, crowdApi, fileUrl, fmt, seconds } from "./crowdApi";

/** System density (dots) against the true density (line) over the clip's seconds. */
function TruthChart({ zone }: { zone: TruthZone }) {
  const w = 600;
  const h = 160;
  const end = Math.max(1, ...zone.truth_curve_s);
  const top = Math.max(
    1,
    ...zone.truth_curve_density.map((v) => v ?? 0),
    ...zone.sys_density.map((v) => v ?? 0),
  );
  const x = (s: number): number => 30 + (s / end) * (w - 40);
  const y = (v: number): number => h - 20 - (v / top) * (h - 30);
  const truth = zone.truth_curve_s
    .map((s, k) => {
      const v = zone.truth_curve_density[k];
      return v == null ? "" : `${k ? "L" : "M"}${x(s).toFixed(1)},${y(v).toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Density in ${zone.zone}`}>
      {[0, Math.round(top / 2), Math.round(top)].map((v) => (
        <g key={v}>
          <line x1={30} x2={w - 10} y1={y(v)} y2={y(v)} stroke="rgba(128,128,128,0.3)" />
          <text x={2} y={y(v) + 4} fontSize={10} fill="currentColor">
            {v}
          </text>
        </g>
      ))}
      <path d={truth} fill="none" stroke="#2e7d32" strokeWidth={2} />
      {zone.clip_s.map((s, k) => {
        const v = zone.sys_density[k];
        return v == null ? null : <circle key={k} cx={x(s)} cy={y(v)} r={4} fill="#e65100" />;
      })}
      <text x={w - 10} y={h - 4} fontSize={10} textAnchor="end" fill="currentColor">
        clip seconds →
      </text>
    </svg>
  );
}

/** Save the report as a JSON file (the incident record). */
function download(report: EventReport): void {
  const blob = new Blob([JSON.stringify(report, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `crowd-incident-${report.camera_id}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

/** Step 5: the report after the event, with the evidence and the ground-truth score. */
export function ReportPanel({
  cameraIds,
  current,
}: {
  cameraIds: { id: string; name: string }[];
  current?: string;
}) {
  const [camera, setCamera] = useState("");
  const [report, setReport] = useState<EventReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const chosen = camera || current || cameraIds[0]?.id || "";

  const load = async (narrate: boolean): Promise<void> => {
    setBusy(true);
    setMessage(
      narrate ? "Muse Spark is writing the after-action summary…" : "Building the report…",
    );
    try {
      setReport(await crowdApi.report(chosen, narrate));
      setMessage("");
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : "Report failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.row}>
        <h2>After the event: report</h2>
        <select className={styles.input} value={chosen} onChange={(e) => setCamera(e.target.value)}>
          {cameraIds.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={styles.button}
          disabled={busy || !chosen}
          onClick={() => void load(false)}
        >
          Build report
        </button>
        <button
          type="button"
          className={styles.button}
          disabled={busy || !chosen}
          onClick={() => void load(true)}
        >
          + Muse Spark after-action
        </button>
      </div>
      {message && <span className={styles.small}>{message}</span>}
      {report && (
        <>
          {report.after_action && (
            <div>
              <strong>{report.after_action.headline}</strong>
              {report.after_action.cited.length > 0 ? (
                <p style={{ margin: "4px 0" }}>
                  {report.after_action.cited.map((c, k) => (
                    <span key={k}>
                      {c.text}{" "}
                      <span className={styles.small}>
                        {c.records.length ? `[#${c.records.join(", #")}]` : "[no record]"}
                      </span>{" "}
                    </span>
                  ))}
                </p>
              ) : (
                <p style={{ margin: "4px 0" }}>{report.after_action.narrative}</p>
              )}
              <div className={styles.row}>
                <div>
                  <em>Worked</em>
                  <ul className={styles.list}>
                    {report.after_action.worked.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <em>Change</em>
                  <ul className={styles.list}>
                    {report.after_action.change.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}
          <div className={styles.row}>
            <span
              className={styles.badge}
              style={{
                background:
                  report.evidence.chain_ok == null
                    ? "#757575"
                    : report.evidence.chain_ok
                      ? "#2e7d32"
                      : "#c62828",
              }}
            >
              {report.evidence.chain_ok == null
                ? "no chain"
                : report.evidence.chain_ok
                  ? "chain verified"
                  : "chain broken"}
            </span>
            <button type="button" className={styles.button} onClick={() => download(report)}>
              Download incident record (JSON)
            </button>
          </div>
          <span className={styles.small}>
            {report.readings} readings · evidence chain{" "}
            {report.evidence.chain_ok == null
              ? "not written"
              : report.evidence.chain_ok
                ? `verified (${report.evidence.records.length} records for these alerts)`
                : `BROKEN: ${report.evidence.problem}`}
          </span>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>zone</th>
                <th>peak /m²</th>
                <th>at</th>
                <th>mean</th>
                {report.reference_ppm2.map((r) => (
                  <th key={r}>≥{r}/m²</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.zones.map((z) => (
                <tr key={z.zone}>
                  <td>{z.zone}</td>
                  <td>
                    {z.peak_heatmap ? (
                      <a href={fileUrl(z.peak_heatmap)} target="_blank" rel="noreferrer">
                        {fmt(z.peak_density_ppm2)}
                      </a>
                    ) : (
                      fmt(z.peak_density_ppm2)
                    )}
                  </td>
                  <td>{clock(z.peak_at)}</td>
                  <td>{fmt(z.mean_density_ppm2)}</td>
                  {report.reference_ppm2.map((r) => (
                    <td key={r}>{seconds(z.above_s[`${r}`] ?? 0)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {report.latency.length > 0 && (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>alert</th>
                  <th>reading</th>
                  <th>→ Spark</th>
                  <th>→ sent</th>
                  <th>frame check</th>
                </tr>
              </thead>
              <tbody>
                {report.latency.map((l) => (
                  <tr key={l.alert_id}>
                    <td style={{ color: LEVEL_COLOUR[l.level] }}>
                      {l.level} {l.sent ? "" : "(held)"}
                    </td>
                    <td>{clock(l.reading_at)}</td>
                    <td>{seconds(l.assess_s)}</td>
                    <td>{seconds(l.alert_s)}</td>
                    <td>{l.checked}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {report.truth && (
            <>
              <strong>Against the ground truth</strong>
              <span className={styles.small}>{report.truth_source}</span>
              {report.truth.map((z) => (
                <div key={z.zone}>
                  <span className={styles.small}>
                    <strong>{z.zone}</strong>: {z.readings} readings · count{" "}
                    {fmt((z.count_ratio ?? 0) * 100, 0)}% of the true people (mean error{" "}
                    {fmt(z.count_mae)}) · density ×{fmt(z.density_ratio_median, 2)} (median) · peak
                    system {fmt(z.sys_peak_ppm2)} vs true {fmt(z.true_peak_ppm2)}/m² at{" "}
                    {fmt(z.true_peak_clip_s)} s
                  </span>
                  <TruthChart zone={z} />
                </div>
              ))}
              <span className={styles.small}>
                Green line: true density; orange dots: the live readings.
              </span>
            </>
          )}
          {report.unknowns.length > 0 && (
            <>
              <strong>What the system did not know</strong>
              <ul className={styles.list}>
                {report.unknowns.map((u) => (
                  <li key={u} className={styles.small}>
                    {u}
                  </li>
                ))}
              </ul>
            </>
          )}
          <strong>Timeline</strong>
          <ul className={styles.list}>
            {report.timeline.map((e, k) => (
              <li key={k} className={styles.small}>
                {clock(e.at)}{" "}
                <strong style={{ color: e.level ? LEVEL_COLOUR[e.level] : undefined }}>
                  {e.kind}
                </strong>{" "}
                {e.level && e.kind !== "alert" ? `${e.level}: ` : ""}
                {e.text}
                {e.seq != null && (
                  <span className={styles.muted}>
                    {" "}
                    · chain #{e.seq} {e.hash?.slice(0, 10)}…
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
