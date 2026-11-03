"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";
import { IncidentView } from "@/components/ops/IncidentView";
import { ApiError } from "@/lib/api";
import { incidents, type IncidentReport } from "@/lib/incidents";

export default function IncidentPage() {
  const { id } = useParams<{ id: string }>();
  const [report, setReport] = useState<IncidentReport | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    incidents
      .get(id)
      .then(setReport)
      .catch((e: unknown) => setError(e instanceof ApiError ? e : new ApiError(0, "offline")));
  }, [id]);

  if (error?.status === 403) return <OwnerOnly what="Incident reports" checking={false} />;
  if (error)
    return (
      <p className="muted">
        Could not load incident {id}: {error.message}
      </p>
    );
  if (!report) return <p className="muted">Loading incident {id}…</p>;
  return <IncidentView report={report} />;
}
