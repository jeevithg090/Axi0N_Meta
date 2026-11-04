import styles from "./kit.module.css";
import { cx } from "./cx";

export type LiveMode = "live" | "replay" | "sample" | "offline";

/** The words are the API's own badge strings, so a badge never needs translating. */
export const LIVE_WORDS: Record<LiveMode, string> = {
  live: "LIVE",
  replay: "REPLAY",
  sample: "SAMPLE",
  offline: "OFFLINE",
};

const MEANING: Record<LiveMode, string> = {
  live: "Live data from the deployed stack",
  replay: "A recorded run, replayed with no model calls",
  sample: "Hand-written sample content, not a run or a measured result",
  offline: "The Rulecam API is not reachable",
};

export interface LiveBadgeProps {
  /** Where the data on screen comes from. Never guess: unknown is `offline`. */
  mode: LiveMode;
  /** A short visible note beside the word, e.g. "recorded 1 Nov 2026". */
  detail?: string;
  className?: string;
}

/**
 * LIVE, REPLAY, SAMPLE or OFFLINE, said in words on every surface that shows data, so a
 * recording is never mistaken for live.
 */
export function LiveBadge({ mode, detail, className }: LiveBadgeProps) {
  return (
    <span
      className={cx(styles.badge, className)}
      data-mode={mode}
      data-testid="live-badge"
      role="status"
      title={MEANING[mode]}
    >
      {LIVE_WORDS[mode]}
      {detail && <span className={styles.badgeDetail}>{detail}</span>}
      <span className="visually-hidden">{`: ${MEANING[mode]}`}</span>
    </span>
  );
}
