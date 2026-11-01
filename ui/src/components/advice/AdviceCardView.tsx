"use client";

import { useState } from "react";

import {
  claimParts,
  formatNumber,
  type AdviceCard,
  type NumberCheck,
  type SealStatus,
} from "@/lib/advice";

import { NumberModal } from "./NumberModal";
import { VerdictPlot } from "./VerdictPlot";

const EXPLANATION_TEXT = {
  supported: "supported",
  partly: "partly",
  ruled_out: "ruled out",
  untestable: "untestable",
} as const;

function Saw({ card, onOpen }: { card: AdviceCard; onOpen: (c: NumberCheck) => void }) {
  return (
    <section aria-label="Saw">
      <h4>Saw</h4>
      {card.recommendation.saw.map((claim, i) => (
        <p key={i} className="claim">
          {claimParts(claim, card).map((part, j) => {
            if ("text" in part) return <span key={j}>{part.text}</span>;
            if ("missing" in part)
              return (
                <span key={j} className="num-hidden">
                  [no number]
                </span>
              );
            if (part.hidden) {
              return (
                <button
                  key={j}
                  type="button"
                  className="num num-hidden"
                  onClick={() => onOpen(part.check)}
                >
                  hidden: not reproduced
                </button>
              );
            }
            return (
              <button key={j} type="button" className="num" onClick={() => onOpen(part.check)}>
                {formatNumber(part.check.catalogue_value, part.check.unit)}
              </button>
            );
          })}
        </p>
      ))}
    </section>
  );
}

/** The six-part Site Advisor card: Saw, Why, Do, Gain, Check, Safety (SA §1). */
export function AdviceCardView({
  card,
  seals,
  busy,
  onAccept,
}: {
  card: AdviceCard;
  seals: SealStatus[];
  busy: boolean;
  onAccept: () => void;
}) {
  const [open, setOpen] = useState<NumberCheck | null>(null);
  const rec = card.recommendation;
  const plan = card.measurement_plan;
  const revealed = seals.find((s) => s.revealed);
  return (
    <article
      className={card.flagged ? "advice advice-flagged" : "advice"}
      data-testid="advice-card"
    >
      <header className="advice-head">
        <h3>
          {rec.scope_ids.join(", ")} <span className="muted">· {rec.klass.toUpperCase()}</span>
        </h3>
        <span className={`pill src-${rec.provenance}`}>{rec.provenance}</span>
        <span className="pill" data-testid="reproduced">
          {rec.numbers_reproduced}/{rec.numbers_total} numbers reproduced by Glimmer
        </span>
      </header>
      {card.flagged && (
        <p className="advice-warning" role="alert">
          Some numbers could not be reproduced independently, so they are hidden.
        </p>
      )}
      <Saw card={card} onOpen={setOpen} />
      <section aria-label="Why">
        <h4>Why</h4>
        <ul>
          {rec.why.map((e) => (
            <li key={e.id} className={`why-${e.status}`}>
              {e.statement} <span className="muted">({EXPLANATION_TEXT[e.status]})</span>
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="Do">
        <h4>Do</h4>
        <ul>
          {rec.do.map((c, i) => (
            <li key={i}>
              {c.op.replaceAll("_", " ")}:{" "}
              {Object.entries(c.params)
                .map(([k, v]) => `${k} ${v}`)
                .join(", ")}
            </li>
          ))}
        </ul>
        <p className="muted">
          Who: {rec.who_role} · To undo: {rec.revert}
        </p>
      </section>
      <section aria-label="Gain">
        <h4>Gain</h4>
        <p>
          {rec.expect.interval[0]} to {rec.expect.interval[1]} {rec.expect.unit}{" "}
          <span className="muted">
            ({Math.round(rec.expect.level * 100)}% range,{" "}
            {rec.expect.model_rung.replaceAll("_", " ")})
          </span>
        </p>
        <p className="muted">
          Assumptions:{" "}
          {Object.entries(rec.expect.assumptions)
            .map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`)
            .join(" · ")}
        </p>
      </section>
      <section aria-label="Check">
        <h4>Check</h4>
        {plan ? (
          <>
            <p data-testid="preregistered">
              Pre-registered at event {card.preregistered_seq}: {plan.design}, {plan.n_pairs} pairs
              of {plan.block_minutes} min, smallest possible p {plan.smallest_possible_p.toFixed(3)}
            </p>
            <p className="muted">
              Method hash <code>{plan.preregistration_hash.slice(0, 12)}…</code>
            </p>
          </>
        ) : (
          <button type="button" className="primary" onClick={onAccept} disabled={busy}>
            Accept and pre-register the check
          </button>
        )}
        {card.verdict && <VerdictPlot verdict={card.verdict} unit={rec.expect.unit} />}
        {revealed && (
          <p className={revealed.matches ? "seal-ok" : "seal-bad"} data-testid="seal-badge">
            {revealed.matches ? "✓ Sealed cause revealed: match" : "✗ Sealed cause does not match"}
          </p>
        )}
      </section>
      <section aria-label="Safety">
        <h4>Safety</h4>
        <p>
          {rec.safety.verdict === "veto" ? "✗ Vetoed" : "✓ Passes"} under{" "}
          {rec.safety.rules_checked.join(", ")}
        </p>
        {rec.safety.considered_not_recommended.map(([change, reason]) => (
          <p key={change} className="not-recommended">
            Considered and not recommended: {change}, which {reason}.
          </p>
        ))}
      </section>
      {open && (
        <NumberModal
          check={open}
          hidden={card.hidden.includes(open.result_id)}
          onClose={() => setOpen(null)}
        />
      )}
    </article>
  );
}
