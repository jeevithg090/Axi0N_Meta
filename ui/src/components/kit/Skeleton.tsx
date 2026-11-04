import type { CSSProperties } from "react";

import styles from "./kit.module.css";
import { cx } from "./cx";

export interface SkeletonProps {
  /** Text lines to draw (shape `text`); the last is shorter. */
  lines?: number;
  /** `text` (default), `block` (a panel, a frame) or `circle`. */
  shape?: "text" | "block" | "circle";
  /** Height of a block or circle, e.g. "160px" or "2.5rem". */
  height?: string;
  /** What is loading, for screen readers: "Loading alerts". */
  label?: string;
  className?: string;
}

/** A placeholder in the shape of what is coming, announced as busy. */
export function Skeleton({ lines = 1, shape = "text", height, label = "Loading", className }: SkeletonProps) {
  const count = shape === "text" ? Math.max(1, lines) : 1;
  const style = (height ? { "--bone-h": height } : undefined) as CSSProperties | undefined;
  return (
    <span
      className={cx(styles.skeleton, className)}
      data-shape={shape}
      role="status"
      aria-busy="true"
      aria-label={label}
      style={style}
      data-testid="skeleton"
    >
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className={styles.bone} aria-hidden="true" />
      ))}
    </span>
  );
}
