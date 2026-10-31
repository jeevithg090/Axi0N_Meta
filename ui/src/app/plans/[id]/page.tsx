"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { AcceptanceTrace } from "@/components/plan/AcceptanceTrace";
import { AuditFindings } from "@/components/plan/AuditFindings";
import { FeasibilityCard } from "@/components/plan/FeasibilityCard";
import { PlanDiffView } from "@/components/plan/PlanDiffView";
import { PlanView } from "@/components/plan/PlanView";
import { api, ApiError } from "@/lib/api";
import { diffPlans, isEmptyDiff } from "@/lib/plan";
import { useDemo, useDispatch } from "@/lib/stream";
import { usePlans } from "@/lib/usePlans";

export default function PlanPage() {
  const { id } = useParams<{ id: string }>();
  const { plans, error } = usePlans();
  const dispatch = useDispatch();
  const demo = useDemo();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [kept, setKept] = useState<number | null>(null);

  const entry = plans[id];
  if (!entry) {
    return <p className="muted">{error ?? `Loading plan ${id}…`}</p>;
  }
  const { latest, approved } = entry;
  const isApproved = approved?.version === latest.version;
  const diff = approved && !isApproved ? diffPlans(approved, latest) : null;
  const showDiff = diff && !isEmptyDiff(diff) && kept !== latest.version;

  const approve = async (): Promise<void> => {
    setBusy(true);
    try {
      if (!demo) await api.approve(latest.plan_id);
      dispatch({ type: "plan-approved", planId: latest.plan_id });
      setMessage(`Approved v${latest.version}.`);
    } catch (e) {
      setMessage(e instanceof ApiError ? `Not approved: ${e.message}` : "Not approved");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="plan-page">
      <header className="plan-header">
        <h2>
          {latest.site_name} <span className="muted">· v{latest.version}</span>
        </h2>
        <span className="pill" data-testid="approval-state">
          {isApproved ? "Approved" : "Needs approval"}
        </span>
        {!approved && (
          <button type="button" className="primary" onClick={approve} disabled={busy}>
            Approve plan
          </button>
        )}
      </header>
      {message && (
        <p role="status" className="muted">
          {message}
        </p>
      )}
      {showDiff && approved && diff && (
        <PlanDiffView
          diff={diff}
          fromVersion={approved.version}
          toVersion={latest.version}
          busy={busy}
          onApprove={approve}
          onKeep={() => {
            setKept(latest.version);
            setMessage(`Kept v${approved.version}; v${latest.version} was not approved.`);
          }}
        />
      )}
      <PlanView plan={latest} />
      <section aria-label="Feasibility">
        <h2>What the cameras can check</h2>
        <div className="feas-grid">
          {latest.feasibility.map((v) => (
            <FeasibilityCard key={v.concern} verdict={v} />
          ))}
        </div>
      </section>
      <section aria-label="Acceptance">
        <h2>Acceptance trace</h2>
        <AcceptanceTrace plan={latest} />
      </section>
      <section aria-label="Audit">
        <h2>Plan audit</h2>
        <AuditFindings findings={latest.audit} />
      </section>
    </div>
  );
}
