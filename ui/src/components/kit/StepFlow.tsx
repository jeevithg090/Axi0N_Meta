import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

import { ModelChip, type ModelChipProps } from "./ModelChip";
import styles from "./kit.module.css";
import { cx } from "./cx";

export type StepState = "done" | "current" | "todo";

export interface FlowStep {
  /** The step's name: "Describe", "Plan", "Prove", "Trust". */
  name: string;
  /** One line on what happens in it. */
  text?: ReactNode;
  /** The models that do this step, as chips beneath the line. */
  models?: (string | ModelChipProps)[];
  /** Overrides the state `current` would give this step. */
  state?: StepState;
  /** Where to see this step at work. */
  href?: string;
}

export interface StepFlowProps {
  steps: FlowStep[];
  /** Index of the step in progress: earlier steps read as done, later ones as to do. */
  current?: number;
  /** Screen-reader name for the list, e.g. "How a rule goes live". */
  label?: string;
  /** `row` (default; one column on a phone) or `column` for a narrow side panel. */
  orientation?: "row" | "column";
  /** `compact` sets the step names a size smaller. */
  size?: "default" | "compact";
  /** The element each step name is set in, to keep heading levels in order. */
  as?: "h2" | "h3" | "h4" | "p";
  className?: string;
}

function stateOf(step: FlowStep, index: number, current: number | undefined): StepState {
  if (step.state) return step.state;
  if (current === undefined) return "todo";
  if (index < current) return "done";
  return index === current ? "current" : "todo";
}

/** The journey stepper: an ordered list of steps, each with its line and its models. */
export function StepFlow({
  steps,
  current,
  label = "Steps",
  orientation = "row",
  size = "default",
  as: Name = "h3",
  className,
}: StepFlowProps) {
  const style = { "--flow-count": steps.length } as CSSProperties;
  return (
    <ol
      className={cx(styles.flow, className)}
      data-orientation={orientation}
      data-size={size}
      aria-label={label}
      style={style}
      data-testid="step-flow"
    >
      {steps.map((step, i) => {
        const state = stateOf(step, i, current);
        return (
          <li
            key={step.name}
            className={styles.flowStep}
            data-state={state}
            aria-current={state === "current" ? "step" : undefined}
          >
            <div className={styles.flowHead}>
              <span className={styles.flowNum} aria-hidden="true">
                {i + 1}
              </span>
              <Name className={styles.flowName}>
                {step.href ? (
                  <Link href={step.href} className={styles.flowLink}>
                    {step.name}
                  </Link>
                ) : (
                  step.name
                )}
              </Name>
            </div>
            <div>
              {step.text && <p className={styles.flowText}>{step.text}</p>}
              {step.models && step.models.length > 0 && (
                <ul className={styles.flowModels} aria-label={`Models in ${step.name}`}>
                  {step.models.map((m) => {
                    const chip = typeof m === "string" ? { model: m } : m;
                    return (
                      <li key={chip.model}>
                        <ModelChip {...chip} />
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
