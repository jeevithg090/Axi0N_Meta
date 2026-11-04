import type { ReactNode } from "react";

import { LiveBadge, type LiveMode } from "./LiveBadge";
import { VerdictPill, type VerdictPillProps } from "./VerdictPill";
import styles from "./kit.module.css";
import { cx } from "./cx";

export interface AlertCardProps {
  /** What happened, in plain words: "Person within 2 m of a moving forklift". */
  title: ReactNode;
  /** The rule id or its plain text. */
  rule?: string;
  /** The camera id. */
  camera?: string;
  /** When, as the API wrote it (ISO) or a display string. */
  time?: string;
  /** The rule's severity as the plan set it. */
  severity?: "low" | "medium" | "high" | string;
  /** The verifier's verdict on this alert. */
  verdict?: VerdictPillProps;
  /** Where the data comes from; shown as a badge when given. */
  mode?: LiveMode;
  /** Extra facts beyond rule, camera and time. */
  facts?: { label: string; value: ReactNode }[];
  /** The body: an EvidenceCard, the reasoning, a timeline. */
  children?: ReactNode;
  /** Buttons or links: On my way, False alarm, Escalate. */
  actions?: ReactNode;
  className?: string;
}

/** One alert: what fired, where and when, what the verifier said, and what to do about it. */
export function AlertCard({
  title,
  rule,
  camera,
  time,
  severity,
  verdict,
  mode,
  facts = [],
  children,
  actions,
  className,
}: AlertCardProps) {
  const rows = [
    ...(rule ? [{ label: "Rule", value: rule }] : []),
    ...(camera ? [{ label: "Camera", value: camera }] : []),
    ...(time ? [{ label: "Time", value: time }] : []),
    ...facts,
  ];
  const sev = severity ? severity.toLowerCase() : undefined;
  return (
    <article className={cx(styles.alert, className)} data-severity={sev} data-testid="alert-card">
      <header className={styles.alertHead}>
        <div className={styles.alertTitleWrap}>
          <h3 className={styles.alertTitle}>{title}</h3>
          {(sev || mode) && (
            <div className={styles.alertTags}>
              {sev && (
                <span className={styles.sev} data-severity={sev}>
                  {`Severity ${sev}`}
                </span>
              )}
              {mode && <LiveBadge mode={mode} />}
            </div>
          )}
        </div>
        {verdict && <VerdictPill {...verdict} />}
      </header>
      {rows.length > 0 && (
        <dl className={styles.alertFacts}>
          {rows.map((r) => (
            <div key={r.label}>
              <dt>{r.label}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {children}
      {actions && <footer className={styles.alertActions}>{actions}</footer>}
    </article>
  );
}
