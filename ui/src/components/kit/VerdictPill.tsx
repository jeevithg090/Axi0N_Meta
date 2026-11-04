import styles from "./kit.module.css";
import { cx } from "./cx";

export type Verdict = "yes" | "no" | "unsure";

/** Glimmer's verdict strings (and plain yes/no) folded onto yes, no and unsure. */
export function normaliseVerdict(verdict: string | boolean | null | undefined): Verdict {
  if (verdict === true) return "yes";
  if (verdict === false) return "no";
  const v = String(verdict ?? "").toLowerCase();
  if (v === "yes" || v === "violation" || v === "confirmed") return "yes";
  if (v === "no" || v === "no_violation" || v === "rejected") return "no";
  return "unsure";
}

const WORDS: Record<Verdict, string> = { yes: "Violation", no: "No violation", unsure: "Unsure" };

export interface VerdictPillProps {
  /** `yes` / `no` / `unsure`, or Glimmer's own `violation` / `no_violation` / `uncertain`. */
  verdict: Verdict | string | boolean | null;
  /** The model's score in 0..1 (Glimmer reports P(violation)); omitted when there is none. */
  confidence?: number | null;
  /** Who gave the verdict; shown as the pill's first segment. */
  model?: string;
  /** What the score means, for the tooltip and screen readers. */
  scoreLabel?: string;
  className?: string;
}

/** A verifier's verdict: the model, the answer in words and its score, never colour alone. */
export function VerdictPill({
  verdict,
  confidence,
  model = "Glimmer",
  scoreLabel = "P(violation)",
  className,
}: VerdictPillProps) {
  const v = normaliseVerdict(verdict);
  const score = typeof confidence === "number" && Number.isFinite(confidence) ? confidence : null;
  const said = `${model} says ${WORDS[v].toLowerCase()}${score !== null ? `, ${scoreLabel} ${score.toFixed(2)}` : ""}`;
  return (
    <span className={cx(styles.pill, className)} data-verdict={v} data-testid="verdict-pill" title={said}>
      <span className="visually-hidden">{said}</span>
      {model && (
        <span className={styles.pillModel} aria-hidden="true">
          {model}
        </span>
      )}
      <span className={styles.pillWord} aria-hidden="true">
        {WORDS[v]}
      </span>
      {score !== null && (
        <span className={styles.pillScore} aria-hidden="true">
          {score.toFixed(2)}
        </span>
      )}
    </span>
  );
}
