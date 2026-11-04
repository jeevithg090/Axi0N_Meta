import type { ReactNode } from "react";

import { icons, type IconName } from "../icons";
import styles from "./kit.module.css";
import { cx } from "./cx";

export interface EmptyStateProps {
  /** What is empty or what broke: "No alerts yet", "Could not reach the API". */
  title: string;
  /** Why it matters, and what fills it or what to do next. */
  children?: ReactNode;
  /** The one action that fills it (a Link or a button). */
  action?: ReactNode;
  /** An icon from components/icons. */
  icon?: IconName;
  /** `empty` (default) or `error`, which is announced to screen readers. */
  tone?: "empty" | "error";
  className?: string;
}

/** What belongs here, why it is empty (or what broke), and the one action that fills it. */
export function EmptyState({ title, children, action, icon, tone = "empty", className }: EmptyStateProps) {
  return (
    <div
      className={cx(styles.empty, className)}
      data-tone={tone}
      role={tone === "error" ? "alert" : undefined}
      data-testid="empty-state"
    >
      {icon && <span className={styles.emptyIcon}>{icons[icon]}</span>}
      <p className={styles.emptyTitle}>{title}</p>
      {children && <div className={styles.emptyText}>{children}</div>}
      {action && <div className={styles.emptyAction}>{action}</div>}
    </div>
  );
}
