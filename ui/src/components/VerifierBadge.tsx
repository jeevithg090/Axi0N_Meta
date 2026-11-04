import type { RuntimeMetrics } from "@/lib/api";

/** A red header badge while Tier 1's circuit is not closed: candidates go to the human queue. */
export function VerifierBadge({ metrics }: { metrics: RuntimeMetrics | null }) {
  if (!metrics?.verifier_down) return null;
  const health = metrics.verifier;
  const failed = metrics.verifications_failed ?? 0;
  const detail = [
    "The Tier 1 check is unavailable: every candidate goes to the human queue.",
    health?.last_error ? `Last error: ${health.last_error}.` : "",
    `${failed} candidate${failed === 1 ? "" : "s"} decided without a verdict.`,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span className="badge badge-alarm" data-testid="verifier-badge" role="alert" title={detail}>
      <span className="badge-dot" aria-hidden="true" />
      VERIFIER DOWN{health?.state === "half_open" ? " · retrying" : ""}
    </span>
  );
}
