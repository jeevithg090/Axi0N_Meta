"use client";

import { ciVerdict, type RuleCIReport } from "@/lib/ops";
import { ruleSentence, type FeasibilityVerdict, type Rule } from "@/lib/plan";

/** A rule card as P2-10 renders it: plain language, citation, Rule CI numbers and who checks it. */
export function RuleCardTile({ rule, report }: { rule: Rule; report?: RuleCIReport }) {
  const v = report ? ciVerdict(report) : null;
  return (
    <article className="gallery-card" data-testid="gallery-rule">
      <p className="gallery-kind">Rule · {rule.severity}</p>
      <h3>{rule.source.text ?? rule.id}</h3>
      {rule.source.standard.length > 0 && (
        <p className="muted">{rule.source.standard.join(" · ")}</p>
      )}
      <p className="gallery-ci">
        {v
          ? [v.boundary, v.mutations, v.lookalikes].filter(Boolean).join(" · ")
          : "Rule CI pending"}
      </p>
      <p className="muted">Checked by Glimmer + Spark Open</p>
      <p className="visually-hidden">{ruleSentence(rule)}</p>
    </article>
  );
}

/**
 * A declined card: the category, the reason and the alternative. It never shows the raw request
 * text, which may contain a name (P2-10 guardrail), so `concern` is deliberately not rendered.
 */
export function DeclinedCardTile({ verdict }: { verdict: FeasibilityVerdict }) {
  return (
    <article className="gallery-card gallery-declined" data-testid="gallery-declined">
      <p className="gallery-kind">Declined</p>
      <h3>{verdict.says}</h3>
      {verdict.reason && <p>Why: {verdict.reason}</p>}
      {verdict.alternative && (
        <p>
          <strong>Instead:</strong> {verdict.alternative}
        </p>
      )}
    </article>
  );
}
