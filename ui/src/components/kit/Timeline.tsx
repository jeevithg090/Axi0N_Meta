import type { ReactNode } from "react";

import styles from "./kit.module.css";
import { cx } from "./cx";

export type TimelineTone = "person" | "instrument" | "ok" | "bad" | "neutral";

export interface TimelineProps {
  children: ReactNode;
  /** Screen-reader name for the trail, e.g. "Escalation". */
  label?: string;
  className?: string;
}

/** An audit trail: entries on one spine, oldest first unless the caller says otherwise. */
export function Timeline({ children, label = "Timeline", className }: TimelineProps) {
  return (
    <ol className={cx(styles.timeline, className)} aria-label={label} data-testid="timeline">
      {children}
    </ol>
  );
}

export interface TimelineItemProps {
  /** An ISO timestamp (shown as HH:MM:SS) or any short label ("+12 s"). */
  time: string;
  /** What happened, in a few words. */
  title: ReactNode;
  /** One line of meta under the title: who, which rule, which camera. */
  meta?: ReactNode;
  /** `person` (amber: an operator acted), `instrument` (cool: a model or sensor), `ok`, `bad`. */
  tone?: TimelineTone;
  children?: ReactNode;
}

/** HH:MM:SS for an ISO timestamp, else the label as given. */
export function timeLabel(time: string): string {
  if (!/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(time)) return time;
  const d = new Date(time);
  if (Number.isNaN(d.getTime())) return time;
  return d.toISOString().slice(11, 19);
}

/** One entry on a Timeline. */
export function TimelineItem({ time, title, meta, tone = "neutral", children }: TimelineItemProps) {
  const iso = /\d{4}-\d{2}-\d{2}T/.test(time) ? time : undefined;
  return (
    <li className={styles.tlItem} data-tone={tone}>
      <time className={styles.tlTime} dateTime={iso}>
        {timeLabel(time)}
      </time>
      <div className={styles.tlBody}>
        <p className={styles.tlTitle}>{title}</p>
        {meta && <p className={styles.tlMeta}>{meta}</p>}
        {children}
      </div>
    </li>
  );
}
