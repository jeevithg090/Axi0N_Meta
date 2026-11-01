"use client";

import Link from "next/link";
import { acceptanceSummary } from "@/lib/plan";
import { useDemoQuery } from "@/lib/stream";
import { usePlans } from "@/lib/usePlans";

export default function PlansPage() {
  const { plans, error } = usePlans();
  const query = useDemoQuery();
  const entries = Object.values(plans);

  return (
    <section aria-label="Plans">
      <h2>Plans</h2>
      {error && <p className="muted">{error}</p>}
      {entries.length === 0 && !error && (
        <p className="muted">No plans yet. Start one in the planner.</p>
      )}
      <ul className="plan-list">
        {entries.map(({ latest, approved }) => {
          const acc = acceptanceSummary(latest);
          const pending = !approved || approved.version !== latest.version;
          return (
            <li key={latest.plan_id}>
              <Link href={`/plans/${latest.plan_id}${query}`}>{latest.site_name}</Link>{" "}
              <span className="muted">
                v{latest.version} · {latest.rules.length} rules · {acc.passed}/{acc.total} checks
              </span>{" "}
              <span className="pill">{pending ? "Needs approval" : "Approved"}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
