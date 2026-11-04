import type { ReactNode } from "react";

import { LiveBadge, ModelChip, type LiveMode, type ModelChipProps } from "@/components/kit";

import styles from "./usecases.module.css";

export interface UseCaseProofProps {
  /** Id for the heading (the section is labelled by it). */
  id: string;
  /** What the proof shows, as a head: "What one forklift rule proved". */
  title: string;
  /** Where the proof comes from: always said, never live by default. */
  badge: { mode: LiveMode; detail: string };
  /** The one reading set at display size, with what it replaced when there is one. */
  reading: {
    value: string;
    unit?: string;
    struck?: { value: string; label: string };
    caption: ReactNode;
  };
  /** The verifier's verdict (a VerdictPill) or any short judgement. */
  verdict?: ReactNode;
  /** The model's reasoning or the reconciliation, in its words. */
  reason?: ReactNode;
  /** The models behind the reading. */
  chips?: ModelChipProps[];
  /** One more measured line: a time, a count. */
  footnote?: ReactNode;
  /** The evidence beside the reading (EvidenceCards, a Timeline card), two across. */
  children?: ReactNode;
  /** A line under the evidence: where to replay it. */
  after?: ReactNode;
  testId?: string;
}

/**
 * The proof block every use-case page leads with: one reading set large (with the value it
 * replaced, struck), the verdict and the reasoning beside it, then the evidence. Same rhythm on
 * every world, so the three pages read as one product.
 */
export function UseCaseProof({
  id,
  title,
  badge,
  reading,
  verdict,
  reason,
  chips,
  footnote,
  children,
  after,
  testId,
}: UseCaseProofProps) {
  return (
    <section className={styles.proof} aria-labelledby={id} data-testid={testId}>
      <div className={styles.proofHead}>
        <h2 id={id} className={styles.proofTitle}>
          {title}
        </h2>
        <LiveBadge mode={badge.mode} detail={badge.detail} />
      </div>
      <div className={styles.reading}>
        <div className={styles.readingFigure}>
          {reading.struck && (
            <p className={styles.struck}>
              <s className={styles.struckValue}>{reading.struck.value}</s>
              <span>{reading.struck.label}</span>
            </p>
          )}
          <p className={styles.readingValue}>
            {reading.value}
            {reading.unit && <span className={styles.readingUnit}>{reading.unit}</span>}
          </p>
          <p className={styles.readingCaption}>{reading.caption}</p>
        </div>
        <div className={styles.readingVerdict}>
          {verdict}
          {reason && <p className={styles.reason}>{reason}</p>}
          {chips && chips.length > 0 && (
            <div className={styles.chipRow}>
              {chips.map((c) => (
                <ModelChip key={`${c.model}-${c.job ?? ""}`} {...c} />
              ))}
            </div>
          )}
          {footnote && <p className={styles.readingCaption}>{footnote}</p>}
        </div>
      </div>
      {children && <div className={styles.proofGrid}>{children}</div>}
      {after && <p className={styles.readingCaption}>{after}</p>}
    </section>
  );
}
