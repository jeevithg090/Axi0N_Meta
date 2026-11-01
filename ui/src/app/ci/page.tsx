"use client";

import { CiReportCard } from "@/components/ops/CiReportCard";
import { useStream } from "@/lib/stream";

export default function RuleCiPage() {
  const state = useStream();
  const results = Object.values(state.ci);
  return (
    <section aria-label="Rule CI">
      <h2>Rule CI</h2>
      <p className="muted">
        Every rule is proven before it goes live: boundary traces, mutation tests, detectability
        scenes, a lookalike red-team, injection signs and an alert forecast.
      </p>
      {results.length === 0 && <p className="muted">No Rule CI results yet.</p>}
      <div className="cards">
        {results.map((result) => (
          <CiReportCard key={result.rule_id} result={result} />
        ))}
      </div>
    </section>
  );
}
