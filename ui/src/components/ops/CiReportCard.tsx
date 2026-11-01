"use client";

import { ciVerdict, type RuleCIReport } from "@/lib/ops";

/** One rule's Rule CI: boundary traces, mutations, scenes, lookalikes, injection, forecast. */
export function CiReportCard({ report }: { report: RuleCIReport }) {
  const v = ciVerdict(report);
  return (
    <article
      className={v.green ? "ci ci-green" : "ci ci-red"}
      data-testid="ci-card"
      data-green={v.green}
    >
      <header>
        <h3>{report.rule_id}</h3>
        <span className="pill">{v.green ? "CI green" : "CI red"}</span>
      </header>
      <ul className="ci-lines">
        <li>{v.boundary}</li>
        <li>{v.mutations}</li>
        {report.scenes && (
          <li>
            detectability scenes {report.scenes.passed}/{report.scenes.total}
          </li>
        )}
        {v.lookalikes && <li>{v.lookalikes}</li>}
        {report.injection && (
          <li>
            injection signs: alert still raised {report.injection.still_alerted}/
            {report.injection.variants}, tamper flagged {report.injection.tamper_flagged}
          </li>
        )}
        {report.forecast && (
          <li
            className={
              report.forecast.alerts_per_shift > report.forecast.budget_per_shift ? "over" : ""
            }
          >
            forecast {report.forecast.alerts_per_shift} alerts/shift (budget{" "}
            {report.forecast.budget_per_shift})
          </li>
        )}
      </ul>
      {v.fooled.length > 0 && (
        <ul className="fooled" aria-label="Fooled by">
          {v.fooled.map((l) => (
            <li key={l.name}>
              Fooled by {l.name} → {l.fix ?? "fix pending"}
            </li>
          ))}
        </ul>
      )}
      {report.failures.length > 0 && (
        <p className="muted">Failures: {report.failures.join("; ")}</p>
      )}
    </article>
  );
}
