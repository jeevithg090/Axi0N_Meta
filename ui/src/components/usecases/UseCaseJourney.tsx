import type { ReactNode } from "react";

import { LiveBadge, StepFlow, type FlowStep, type LiveMode } from "@/components/kit";

import styles from "./usecases.module.css";

export interface UseCaseJourneyProps {
  id: string;
  title: string;
  /** Where the steps come from; omit while they are the live page's own state. */
  badge?: { mode: LiveMode; detail: string };
  steps: FlowStep[];
  /** Screen-reader name for the list. */
  label: string;
  columns?: number;
  current?: number;
  /** Anything that belongs with the journey: the planner's caveats, an approve button. */
  children?: ReactNode;
}

/** The journey under the proof: a StepFlow on its own panel, the same on every use-case page. */
export function UseCaseJourney({ id, title, badge, steps, label, columns = 4, current, children }: UseCaseJourneyProps) {
  return (
    <section className={styles.journeyPanel} aria-labelledby={id}>
      <div className={styles.journeyHead}>
        <h2 id={id} className={styles.journeyTitle}>
          {title}
        </h2>
        {badge && <LiveBadge mode={badge.mode} detail={badge.detail} />}
      </div>
      <StepFlow steps={steps} columns={columns} size="compact" as="h3" label={label} current={current} />
      {children}
    </section>
  );
}
