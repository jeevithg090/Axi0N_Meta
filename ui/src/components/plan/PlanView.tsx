"use client";

import { useState } from "react";

import { planYaml, ruleSentence, type SitePlan } from "@/lib/plan";

/** The plan in plain words, or the exact YAML behind it. */
export function PlanView({ plan }: { plan: SitePlan }) {
  const [showYaml, setShowYaml] = useState(false);
  return (
    <section className="plan-view" aria-label="Plan">
      <div className="toggle" role="group" aria-label="View">
        <button type="button" aria-pressed={!showYaml} onClick={() => setShowYaml(false)}>
          Plain language
        </button>
        <button type="button" aria-pressed={showYaml} onClick={() => setShowYaml(true)}>
          YAML
        </button>
      </div>
      {showYaml ? (
        <pre className="yaml" data-testid="plan-yaml">
          {planYaml(plan)}
        </pre>
      ) : (
        <div data-testid="plan-plain">
          <p className="muted">
            {plan.site_name} · {plan.setting_inferred.replaceAll("_", " ")} · languages{" "}
            {plan.languages.join(", ")} · privacy {plan.privacy_mode} · kept {plan.retention_days}{" "}
            days
          </p>
          <ol className="rules">
            {plan.rules.map((rule) => (
              <li key={rule.id}>{ruleSentence(rule)}</li>
            ))}
          </ol>
          {plan.back_translation && (
            <p>
              <strong>In the crew&apos;s language:</strong> {plan.back_translation}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
