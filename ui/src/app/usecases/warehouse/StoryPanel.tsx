"use client";

// The connected story of one plan, step by step, each step read from what the system recorded.

import { useState } from "react";

import { api, errorText } from "@/lib/api";

import { progress, type Planning, type StepStatus, type WarehouseStory } from "./warehouse";
import styles from "./warehouse.module.css";

const MARK: Record<StepStatus, string> = { done: "✓", waiting: "…", not_yet: "○", failed: "✕" };

/** Each planner step with its time, the running one counting up (read from the saved run). */
export function PlanningTimeline({ planning }: { planning: Planning }) {
  return (
    <div data-testid="planning">
      <h3>
        {planning.total_s !== null
          ? `Planned in ${planning.total_s.toFixed(0)} s`
          : "Planning now (each step appears as it finishes)"}
      </h3>
      <ol className={styles.row} style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {planning.steps.map((s) => (
          <li
            key={s.step}
            className={`${styles.chip} ${s.status === "done" ? styles.done : s.status === "running" ? styles.waiting : styles.not_yet}`}
            title={s.detail || undefined}
          >
            {s.step} {s.seconds !== null ? `${s.seconds.toFixed(1)} s` : "…"}
            {s.status === "running" ? " ⏳" : ""}
          </li>
        ))}
      </ol>
      {planning.steps
        .filter((s) => s.detail)
        .map((s) => (
          <p key={s.step} className={styles.muted}>
            {s.step}: {s.detail}
          </p>
        ))}
    </div>
  );
}

export function StoryPanel({
  story,
  onApproved,
}: {
  story: WarehouseStory | null;
  onApproved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (story === null) return <p className={styles.muted}>Loading the story…</p>;
  const { done, total } = progress(story.steps);
  const approveWaiting = story.steps.some((s) => s.key === "approve" && s.status === "waiting");

  async function approve() {
    if (!story?.plan_id) return;
    setBusy(true);
    setError(null);
    try {
      await api.approve(story.plan_id);
      onApproved();
    } catch (failure) {
      setError(errorText(failure, "Approval failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.card} aria-labelledby="wh-story">
      <h2 id="wh-story">
        2. The story so far{" "}
        <span className={styles.chip}>
          {done}/{total} steps
        </span>
      </h2>
      {story.plan_id && (
        <p className={styles.muted}>
          Plan <span className={styles.mono}>{story.plan_id}</span>
          {story.request_lang ? ` · asked in ${story.request_lang}` : ""}
          {story.rules.length
            ? ` · ${story.rules.map((r) => `${r.id} (${r.severity})`).join(", ")}`
            : ""}
        </p>
      )}
      {story.planning && <PlanningTimeline planning={story.planning} />}
      {story.cant_check.length > 0 && (
        <div>
          <h3>What the planner says it cannot fully check</h3>
          <ul>
            {story.cant_check.map((item) => (
              <li key={item.concern}>
                <span className={styles.chip}>{item.status}</span> {item.explanation}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ol className={styles.steps}>
        {story.steps.map((step) => (
          <li key={step.key} className={`${styles.step} ${styles[step.status]}`}>
            <span aria-label={step.status}>{MARK[step.status]}</span>
            <div>
              <strong>{step.title}</strong>
              <span>{step.detail}</span>
            </div>
          </li>
        ))}
      </ol>
      {approveWaiting && (
        <div className={styles.row}>
          <button
            type="button"
            className={`${styles.button} ${styles.primary}`}
            disabled={busy}
            onClick={approve}
          >
            Approve this plan
          </button>
          <span className={styles.muted}>Nothing runs until a person approves.</span>
        </div>
      )}
      {error && <p className={styles.error}>{error}</p>}
    </section>
  );
}
