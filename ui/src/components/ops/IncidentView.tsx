import { incidents, type IncidentReport } from "@/lib/incidents";

function time(iso: string): string {
  return iso.slice(11, 19);
}

/** One incident: what fired, everything chained for it, and the one-click report download. */
export function IncidentView({ report }: { report: IncidentReport }) {
  return (
    <article className="incident" data-testid="incident">
      <header className="plan-header">
        <h2>
          Incident <span className="muted">· {report.event_id}</span>
        </h2>
        <a
          className="pill"
          href={incidents.reportUrl(report.event_id)}
          download
          data-testid="incident-download"
        >
          Download report (PDF)
        </a>
        <a href={incidents.reportUrl(report.event_id, "html")} target="_blank" rel="noreferrer">
          Printable page
        </a>
      </header>
      <p>
        {report.rule_text || report.rule_id} <span className="muted">({report.rule_id})</span>
      </p>
      <p className="muted">
        {report.clauses.join(", ")} · {report.severity} · {report.camera_id} · {report.day}{" "}
        {time(report.ts_start)}–{time(report.ts_end)} UTC
        {report.measurement ? ` · measured ${report.measurement}` : ""}
      </p>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Time (UTC)</th>
            <th>Record</th>
            <th>What</th>
          </tr>
        </thead>
        <tbody>
          {report.timeline.map((entry) => (
            <tr key={entry.seq}>
              <td>{entry.seq}</td>
              <td>{time(entry.ts)}</td>
              <td>
                {entry.kind} <code className="muted">{entry.hash.slice(0, 12)}</code>
              </td>
              <td>{entry.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" data-testid="incident-chain">
        {report.chain_ok
          ? `Evidence chain intact: ${report.chain_records} records that day.`
          : "Evidence chain failed verification."}{" "}
        {report.stills} anonymised still{report.stills === 1 ? "" : "s"} in the report.
      </p>
    </article>
  );
}
