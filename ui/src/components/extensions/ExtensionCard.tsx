import {
  canApprove,
  extensionRef,
  generatorNote,
  mutationScore,
  reviewerNote,
  stepTitle,
  type ExtensionRecord,
  type StepStatus,
} from "@/lib/extensions";

const STATUS_COLOUR: Record<StepStatus, string> = {
  passed: "var(--ok, #2E7D32)",
  failed: "var(--bad, #C62828)",
  skipped: "var(--muted, #8E8B82)",
};

function MutationBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  return (
    <div aria-label={`Mutation score ${pct}%`} role="img" style={{ display: "grid", gap: 4 }}>
      <div
        style={{
          height: 10,
          borderRadius: 5,
          background: "var(--line, #ddd)",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: score >= 0.8 ? STATUS_COLOUR.passed : STATUS_COLOUR.failed,
          }}
        />
        <div
          title="needed: 80%"
          style={{
            position: "absolute",
            left: "80%",
            top: 0,
            bottom: 0,
            width: 2,
            background: "var(--ink, #111)",
          }}
        />
      </div>
      <span className="muted">{pct}% of mutants caught (80% needed)</span>
    </div>
  );
}

/** One extension version: who wrote it, every proof step, the review, the code. */
export function ExtensionCard({
  record,
  onApprove,
  busy = false,
}: {
  record: ExtensionRecord;
  onApprove?: (record: ExtensionRecord) => void;
  busy?: boolean;
}) {
  const score = mutationScore(record);
  const ref = extensionRef(record);
  return (
    <article className="site-panel" aria-label={`Extension ${record.name}`}>
      <header style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline" }}>
        <h3 style={{ margin: 0, overflowWrap: "anywhere" }}>{record.request}</h3>
        <strong data-testid="extension-status">{record.status.toUpperCase()}</strong>
      </header>
      <p className="muted" style={{ overflowWrap: "anywhere" }}>
        {generatorNote(record)}. Sees <code>{record.concept}</code> on {record.camera_id}
        {Object.keys(record.lines).length > 0 && (
          <>
            {" "}
            with the line{Object.keys(record.lines).length === 1 ? "" : "s"}{" "}
            {Object.keys(record.lines).join(", ")}
          </>
        )}
        . Version <code>{record.sha256.slice(0, 12)}</code>
        {record.parent_sha && <> (replaces {record.parent_sha.slice(0, 12)})</>}.
      </p>

      <h4 style={{ margin: "8px 0 0" }}>Proof</h4>
      <ul className="site-items">
        {record.proof.steps.map((step) => (
          <li key={step.name}>
            <strong style={{ color: STATUS_COLOUR[step.status] }}>{step.status}</strong>{" "}
            {stepTitle(step.name)}: <span className="muted">{step.detail}</span>
          </li>
        ))}
      </ul>
      {score !== null && <MutationBar score={score} />}

      <h4 style={{ margin: "8px 0 0" }}>Second opinion</h4>
      <p>
        <strong>{record.review.verdict}</strong> · {reviewerNote(record.review)}
      </p>
      {record.review.reasons.length > 0 && (
        <ul className="site-items">
          {record.review.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {record.review.risks.map((risk) => (
        <p key={risk} className="muted">
          Risk: {risk}
        </p>
      ))}

      <details>
        <summary>
          The code ({record.code.split("\n").length} lines, runs only in the sandbox)
        </summary>
        <pre
          style={{
            overflowX: "auto",
            maxWidth: "100%",
            padding: 10,
            borderRadius: 8,
            background: "var(--cam, #0a0c0e)",
            color: "#ECEAE3",
            fontSize: 12,
          }}
        >
          <code>{record.code}</code>
        </pre>
      </details>

      {record.status === "approved" ? (
        <p>
          Use it in a rule: <code style={{ overflowWrap: "anywhere" }}>{`state: "${ref}"`}</code>
        </p>
      ) : (
        onApprove && (
          <button
            type="button"
            className="primary"
            disabled={busy || !canApprove(record)}
            onClick={() => onApprove(record)}
          >
            {canApprove(record) ? "Approve this version" : "Cannot be approved"}
          </button>
        )
      )}
    </article>
  );
}
