"use client";

import { VERDICT_STYLE, type FeasibilityVerdict } from "@/lib/plan";

/** One concern: the camera "explaining itself", its evidence, and the cheapest fix or alternative. */
export function FeasibilityCard({ verdict }: { verdict: FeasibilityVerdict }) {
  const style = VERDICT_STYLE[verdict.verdict];
  return (
    <article
      className={`feas feas-${style.tone}`}
      data-testid="feasibility-card"
      data-verdict={verdict.verdict}
    >
      <header>
        <h3>{verdict.concern}</h3>
        <span className="pill">{style.label}</span>
      </header>
      <p className="says">“{verdict.says}”</p>
      {verdict.evidence.length > 0 && (
        <ul className="probes">
          {verdict.evidence.map((p) => (
            <li key={`${p.camera_id}-${p.concept}`}>
              {p.concept} on {p.camera_id}: seen in {Math.round(p.detect_rate * 100)}% of{" "}
              {p.n_frames} frames (lower bound {Math.round(p.detect_lb * 100)}%),{" "}
              {Math.round(p.median_px_height)} px tall
            </li>
          ))}
        </ul>
      )}
      {verdict.reason && <p className="muted">Why: {verdict.reason}</p>}
      {verdict.alternative && (
        <p>
          <strong>{verdict.verdict === "caveat" ? "Fix" : "Instead"}:</strong> {verdict.alternative}
        </p>
      )}
    </article>
  );
}
