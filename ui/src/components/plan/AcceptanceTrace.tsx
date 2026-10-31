"use client";

import { acceptanceSummary, type SitePlan } from "@/lib/plan";

/** "27/27 checks" plus every failing check, grouped by plan item (strict AND). */
export function AcceptanceTrace({ plan }: { plan: SitePlan }) {
  const summary = acceptanceSummary(plan);
  return (
    <section className="acceptance" data-testid="acceptance-trace" aria-label="Acceptance trace">
      <p className={summary.allPassed ? "acc-ok" : "acc-bad"}>
        <strong>
          {summary.allPassed ? "✓" : "✗"} {summary.passed}/{summary.total} checks
        </strong>{" "}
        <span className="muted">
          · {summary.rounds} repair round{summary.rounds === 1 ? "" : "s"}
        </span>
      </p>
      {summary.failed.length > 0 && (
        <table className="checks">
          <caption>Failing checks</caption>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">Check</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {summary.failed.map((c, i) => (
              <tr key={`${c.item}-${c.check}-${i}`}>
                <td>{c.item}</td>
                <td>{c.check}</td>
                <td>{c.detail || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
