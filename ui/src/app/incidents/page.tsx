"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";
import { ApiError } from "@/lib/api";
import { incidents, type IncidentSummary } from "@/lib/incidents";

export default function IncidentsPage() {
  const [rows, setRows] = useState<IncidentSummary[] | null>(null);
  const [message, setMessage] = useState("");
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    incidents
      .list()
      .then(setRows)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) setForbidden(true);
        else setMessage("Could not reach the Rulecam API.");
      });
  }, []);

  if (forbidden) return <OwnerOnly what="Incident reports" checking={false} />;
  return (
    <section aria-label="Incidents">
      <h2>Incidents</h2>
      {message && <p className="muted">{message}</p>}
      {rows?.length === 0 && <p className="muted">No incidents on the latest chain day.</p>}
      <ul>
        {(rows ?? []).map((row) => (
          <li key={row.event_id}>
            <Link href={`/incidents/${encodeURIComponent(row.event_id)}`}>{row.rule_id}</Link>{" "}
            <span className="muted">
              {row.camera_id} · {row.severity} · {row.ts.slice(0, 19).replace("T", " ")} UTC
              {row.verdict ? ` · ${row.verdict}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
