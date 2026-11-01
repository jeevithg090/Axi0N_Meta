"use client";

import { lookalikeFooled, type CIResult, type RedTeamReport } from "@/lib/ops";

/** The part-2 lines (scenes, lookalikes, injection, forecast) of a report that ran them. */
function RedTeamLines({ redTeam }: { redTeam: RedTeamReport }) {
  const { scenes, lookalikes, injection, forecast } = redTeam;
  const fooled = lookalikes ? lookalikes.findings.filter(lookalikeFooled) : [];
  const kept = injection ? injection.results.filter((r) => r.alert).length : 0;
  const flagged = injection ? injection.results.filter((r) => r.tamper).length : 0;
  const budget = forecast?.budget.alerts_per_shift;
  const over = forecast?.per_shift != null && budget != null && forecast.per_shift > budget;
  return (
    <>
      {scenes && (
        <li>
          detectability scenes {scenes.results.filter((r) => r.passed).length}/
          {scenes.results.length}
        </li>
      )}
      {lookalikes && (
        <li>
          {lookalikes.findings.length} lookalikes, fooled {fooled.length}
        </li>
      )}
      {injection && (
        <li>
          injection signs: alert still raised {kept}/{injection.results.length}, tamper flagged{" "}
          {flagged}
        </li>
      )}
      {forecast && (
        <li className={over ? "over" : ""}>
          {forecast.per_shift == null
            ? `forecast unavailable (budget ${budget})`
            : `forecast ${forecast.per_shift.toFixed(1)} alerts/shift (budget ${budget})`}
        </li>
      )}
      {redTeam.skipped.length > 0 && (
        <li className="muted">not run: {redTeam.skipped.join("; ")}</li>
      )}
    </>
  );
}

/**
 * One rule's Rule CI from its `ci.result` payload. Green or red is the backend's `ci_green`
 * (traces, mutation bar or justification, and red-team pass); it is never recomputed here.
 */
export function CiReportCard({ result }: { result: CIResult }) {
  const { report, ci_green: green } = result;
  const fooled = report.red_team?.lookalikes?.findings.filter(lookalikeFooled) ?? [];
  return (
    <article
      className={green ? "ci ci-green" : "ci ci-red"}
      data-testid="ci-card"
      data-green={green}
    >
      <header>
        <h3>{result.rule_id}</h3>
        <span className="pill">{green ? "CI green" : "CI red"}</span>
      </header>
      <p className="ci-summary">{result.summary}</p>
      <ul className="ci-lines">
        <li>
          boundary {report.traces_passed}/{report.traces_total}
        </li>
        <li>
          mutations {report.mutants_killed}/{report.mutants_total}
        </li>
        {report.red_team && <RedTeamLines redTeam={report.red_team} />}
      </ul>
      {fooled.length > 0 && (
        <ul className="fooled" aria-label="Fooled by">
          {fooled.map((f) => (
            <li key={f.lookalike}>
              Fooled by {f.lookalike} →{" "}
              {f.fix
                ? `${f.fix.kind.replace("_", " ")} "${f.fix.text}" on ${f.fix.concept}`
                : "fix pending"}
            </li>
          ))}
        </ul>
      )}
      {report.justification && <p className="muted">Justification: {report.justification}</p>}
      {report.failures.length > 0 && (
        <p className="muted">Failures: {report.failures.join("; ")}</p>
      )}
    </article>
  );
}
