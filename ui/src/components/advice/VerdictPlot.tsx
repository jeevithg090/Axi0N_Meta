"use client";

import { scaleX, verdictLine, type Verdict } from "@/lib/advice";

const W = 300;
const H = 40;

/** The "did it work?" verdict: the effect as a dot, its CI as a bar, and zero as a reference line. */
export function VerdictPlot({ verdict, unit }: { verdict: Verdict; unit: string }) {
  const range = Math.max(1, ...[verdict.effect, ...verdict.ci].map((v) => Math.abs(v))) * 1.2;
  const x = (v: number): number => scaleX(v, range, W);
  return (
    <figure className="verdict" data-testid="verdict-plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={verdictLine(verdict, unit)}>
        <line x1={x(0)} x2={x(0)} y1="4" y2={H - 4} className="verdict-zero" />
        <line
          x1={x(verdict.ci[0])}
          x2={x(verdict.ci[1])}
          y1={H / 2}
          y2={H / 2}
          className="verdict-ci"
        />
        <circle cx={x(verdict.effect)} cy={H / 2} r="5" className="verdict-dot" />
      </svg>
      <figcaption>
        <strong>{verdictLine(verdict, unit)}</strong>
        <span className="muted"> · p {verdict.p_value.toFixed(3)}</span>
      </figcaption>
    </figure>
  );
}
