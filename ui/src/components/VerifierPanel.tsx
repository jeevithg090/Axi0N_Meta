"use client";

import type { EventView } from "@/lib/store";

const VERDICT_TEXT = {
  violation: "Violation",
  no_violation: "No violation",
  unsure: "Unsure",
} as const;

/** Prefix of a Tier 1 error verdict's reason (`rulecam.verify.guard`): no real verdict. */
export const VERIFIER_ERROR_PREFIX = "verifier_error: ";

/** Why Tier 1 could not verify this event, if it could not (a person then reviews it). */
export function verifierError(view: EventView): string | null {
  const failed = view.verdicts.find(
    (v) => v.verdict === "unsure" && v.reason.startsWith(VERIFIER_ERROR_PREFIX),
  );
  return failed ? failed.reason.slice(VERIFIER_ERROR_PREFIX.length) : null;
}

/** Predicate trace plus each verifier tier's verdict, with Spark Open's reasoning expandable. */
export function VerifierPanel({ view }: { view: EventView }) {
  const { candidate } = view;
  const error = verifierError(view);
  return (
    <section
      className="verifier"
      data-testid="verifier-panel"
      aria-label={`Event ${candidate.event_id}`}
    >
      <h3>
        {candidate.rule_id} <span className="muted">· {candidate.camera_id}</span>
      </h3>
      <dl className="trace">
        {Object.entries(candidate.predicate_trace).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>
      {error !== null && (
        <p className="needs-review" data-testid="needs-review" role="status">
          Needs review: verifier unavailable — {error}
        </p>
      )}
      {view.verdicts.length === 0 && <p className="muted">Waiting for the verifier…</p>}
      {view.verdicts.map((v, index) => (
        <div key={`${v.tier}-${index}`} className={`tier tier-${v.verdict}`}>
          <p>
            <strong>{v.tier === "glimmer" ? "Tier 1 · Glimmer" : "Tier 2 · Spark Open"}</strong>{" "}
            {VERDICT_TEXT[v.verdict]} ({Math.round(v.score * 100)}%) · {v.latency_ms} ms
            {v.tamper && <span className="pill pill-warn">tamper attempt</span>}
          </p>
          <p>{v.reason}</p>
          {v.scene_text && <p className="muted">Text in view (data only): “{v.scene_text}”</p>}
          {v.reasoning && (
            <details>
              <summary>Reasoning</summary>
              <pre>{v.reasoning}</pre>
            </details>
          )}
        </div>
      ))}
      {view.actions.length > 0 && (
        <ul className="actions">
          {view.actions.map((a, index) => (
            <li key={`${a.kind}-${index}`} className={a.ok ? "" : "action-failed"}>
              {a.ok ? "✓" : "✗"} {a.kind}
              {a.role ? ` → ${a.role}` : ""} <span className="muted">{a.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
