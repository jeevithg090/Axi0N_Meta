import type { ReactNode } from "react";

import { Skeleton } from "./Skeleton";
import styles from "./kit.module.css";
import { cx } from "./cx";

export interface MetricTileProps {
  /** What the number is, in words: "Event to checked decision". */
  label: string;
  /** The figure itself. `null` reads as unknown (a dash), never as zero. */
  value: ReactNode | null;
  /** Its unit, set smaller beside it: "s", "m", "people/m²". */
  unit?: string;
  /** One line of context under the label. */
  note?: ReactNode;
  /** Where the figure comes from: a result file, a run. Required for anything quoted as a result. */
  source?: { href: string; text: string };
  /** `ok`, `bad` (status), `cool` (an instrument reading) or `accent` (needs a person). */
  tone?: "default" | "ok" | "bad" | "cool" | "accent";
  /** `lg` sets the figure at statement size. */
  size?: "md" | "lg";
  /** Shows a skeleton in place of the figure. */
  loading?: boolean;
  className?: string;
}

/** One figure, its unit and what it means; never an invented number. */
export function MetricTile({
  label,
  value,
  unit,
  note,
  source,
  tone = "default",
  size = "md",
  loading = false,
  className,
}: MetricTileProps) {
  const unknown = value === null || value === undefined || value === "";
  return (
    <div className={cx(styles.metric, className)} data-tone={tone} data-size={size} data-testid="metric-tile">
      {loading ? (
        <Skeleton label={`Loading ${label}`} height="2.5rem" shape="block" />
      ) : (
        <p className={styles.metricValue}>
          {unknown ? <span title="Unknown">—</span> : value}
          {!unknown && unit && <span className={styles.metricUnit}>{unit}</span>}
        </p>
      )}
      <p className={styles.metricLabel}>{label}</p>
      {note && <p className={styles.metricNote}>{note}</p>}
      {source && (
        <p className={styles.metricSource}>
          <a href={source.href}>{source.text}</a>
        </p>
      )}
    </div>
  );
}
