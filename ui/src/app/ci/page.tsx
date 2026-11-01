"use client";

import { CiReportCard } from "@/components/ops/CiReportCard";
import { useStream } from "@/lib/stream";

export default function RuleCiPage() {
  const state = useStream();
  const reports = Object.values(state.ci);
  return (
    <section aria-label="Rule CI">
      <h2>Rule CI</h2>
      <p className="muted">
        Every rule is proven before it goes live: boundary traces, mutation tests, detectability
        scenes, a lookalike red-team, injection signs and an alert forecast.
      </p>
      {reports.length === 0 && <p className="muted">No Rule CI results yet.</p>}
      <div className="cards">
        {reports.map((report) => (
          <CiReportCard key={report.rule_id} report={report} />
        ))}
      </div>
    </section>
  );
}
