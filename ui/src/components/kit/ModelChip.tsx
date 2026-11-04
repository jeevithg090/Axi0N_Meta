import styles from "./kit.module.css";
import { cx } from "./cx";

/** A duration for a chip: "640 ms" under a second, else "1.2 s". */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export interface ModelChipProps {
  /** The model's name as Meta ships it, e.g. "Muse Spark 1.3", "SAM 3.1" (`modelLabel(id)`). */
  model: string;
  /** What it did in this step, in a few words: "measured the gap", "checked the frames". */
  job?: string;
  /** How long the call took, in milliseconds; omitted when not measured. */
  ms?: number | null;
  /** `ran` (default), `running`, `skipped` (wired but not called) or `off` (switched off). */
  state?: "ran" | "running" | "skipped" | "off";
  /** False for a non-Meta part (a tracker, a rule engine), shown in the neutral tone. */
  meta?: boolean;
  className?: string;
}

/** Which model did this step, what it did and how long it took. */
export function ModelChip({ model, job, ms, state = "ran", meta = true, className }: ModelChipProps) {
  return (
    <span className={cx(styles.chip, className)} data-state={state} data-meta={meta} data-testid="model-chip">
      <span className={styles.chipModel}>{model}</span>
      {job && <span className={styles.chipJob}>{job}</span>}
      {typeof ms === "number" && Number.isFinite(ms) && <span className={styles.chipTime}>{formatMs(ms)}</span>}
      {state !== "ran" && <span className={styles.chipState}>{state}</span>}
    </span>
  );
}
