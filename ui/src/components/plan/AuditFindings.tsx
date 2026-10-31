"use client";

import type { AuditFinding } from "@/lib/plan";

/** Spark Open's plan audit. Each finding links to its stored reasoning, shown word for word. */
export function AuditFindings({ findings }: { findings: AuditFinding[] }) {
  if (findings.length === 0) {
    return <p className="muted">Spark Open found no gaps.</p>;
  }
  return (
    <ul className="audit" data-testid="audit-findings">
      {findings.map((f, i) => (
        <li key={`${f.reasoning_ref ?? "f"}-${i}`}>
          <strong>Spark Open:</strong> {f.text}
          {f.reasoning_ref && (
            <details>
              <summary>Reasoning</summary>
              <p className="muted">
                Stored as <code>{f.reasoning_ref}</code> (shown verbatim once the reasoning route
                lands).
              </p>
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}
