"use client";

import { ruleSentence, type PlanDiff } from "@/lib/plan";

const show = (value: unknown): string => (Array.isArray(value) ? value.join(", ") : String(value));

/** What a proposed version changes, with approve / keep-current buttons. */
export function PlanDiffView({
  diff,
  fromVersion,
  toVersion,
  onApprove,
  onKeep,
  busy,
}: {
  diff: PlanDiff;
  fromVersion: number;
  toVersion: number;
  onApprove: () => void;
  onKeep: () => void;
  busy: boolean;
}) {
  return (
    <section className="diff" data-testid="plan-diff" aria-label="Plan changes">
      <h3>
        Proposed changes · v{fromVersion} → v{toVersion}
      </h3>
      <ul>
        {diff.added.map((r) => (
          <li key={`a-${r.id}`} className="diff-add">
            + Added: {ruleSentence(r)}
          </li>
        ))}
        {diff.removed.map((r) => (
          <li key={`r-${r.id}`} className="diff-remove">
            − Removed: {ruleSentence(r)}
          </li>
        ))}
        {diff.changed.map(({ before, after }) => (
          <li key={`c-${after.id}`} className="diff-change">
            ~ Changed {after.id}: v{before.version} → v{after.version}
          </li>
        ))}
        {diff.settings.map((s) => (
          <li key={`s-${s.field}`} className="diff-change">
            ~ {s.field}: {show(s.before)} → {show(s.after)}
          </li>
        ))}
      </ul>
      <div className="buttons">
        <button type="button" className="primary" onClick={onApprove} disabled={busy}>
          Approve v{toVersion}
        </button>
        <button type="button" onClick={onKeep} disabled={busy}>
          Keep v{fromVersion}
        </button>
      </div>
    </section>
  );
}
