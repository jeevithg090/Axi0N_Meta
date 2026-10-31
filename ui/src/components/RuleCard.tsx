"use client";

import type { RuleCardView } from "@/lib/store";

function measurementText(card: RuleCardView): string {
  const m = card.latest.candidate.measurement;
  if (!m) return "—";
  const range = m.lo != null && m.hi != null ? ` (${m.lo}–${m.hi})` : "";
  return `${m.value}${range} ${m.unit}`;
}

/** One rule on one camera. BLIND greys it out and says so: a blind camera is never "compliant". */
export function RuleCard({ card }: { card: RuleCardView }) {
  const status = card.blind ? "BLIND" : card.severity.toUpperCase();
  return (
    <article
      className={card.blind ? "rule-card rule-card-blind" : `rule-card sev-${card.severity}`}
      data-testid="rule-card"
      data-blind={card.blind}
      aria-label={`${card.ruleId} on ${card.cameraId}: ${status}`}
    >
      <header>
        <h3>{card.ruleId}</h3>
        <span className="pill">{status}</span>
      </header>
      <p className="muted">
        {card.cameraId} · {card.count} alert{card.count === 1 ? "" : "s"}
      </p>
      <p>
        Last measured: <strong>{measurementText(card)}</strong>
      </p>
      {card.blind && (
        <p className="blind-note">Camera blocked or moved: this rule is not being checked.</p>
      )}
    </article>
  );
}
