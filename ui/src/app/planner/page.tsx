"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";
import { CameraTiles } from "@/components/plan/CameraTiles";
import { QuestionsPanel } from "@/components/plan/QuestionsPanel";
import { RequestComposer } from "@/components/plan/RequestComposer";
import { StepProgress } from "@/components/plan/StepProgress";
import { api, type CameraInfo } from "@/lib/api";
import { useAuth, useOwnerControls } from "@/lib/auth";
import { useDemo, useStream } from "@/lib/stream";
import { usePlans } from "@/lib/usePlans";

export default function PlannerPage() {
  const state = useStream();
  const { plans } = usePlans();
  const [cameras, setCameras] = useState<CameraInfo[]>([]);
  const demo = useDemo() === true;
  const owner = useOwnerControls();
  const { phase } = useAuth();

  useEffect(() => {
    setCameras([]);
    if (!demo)
      api
        .cameras()
        .then(setCameras)
        .catch(() => setCameras([]));
  }, [demo]);

  const planId = state.planner.planId;
  const plan = planId ? plans[planId]?.latest : undefined;
  const query = demo ? "?demo=1" : "";

  return (
    <div className="planner">
      <section aria-label="Request">
        <h2>Planner</h2>
        {owner ? (
          <RequestComposer demo={demo} />
        ) : (
          <OwnerOnly checking={phase === "checking"} what="Planning a site" />
        )}
      </section>
      <section aria-label="Progress">
        <h2>Steps{planId ? ` · ${planId}` : ""}</h2>
        <StepProgress progress={state.planner} />
        {plan && (
          <p>
            <Link href={`/plans/${plan.plan_id}${query}`}>Open plan v{plan.version} →</Link>
          </p>
        )}
        {planId && owner && !demo && (
          <>
            <h3>Questions</h3>
            <QuestionsPanel planId={planId} version={plan?.version} />
          </>
        )}
      </section>
      <section aria-label="Cameras">
        <h2>Cameras</h2>
        <CameraTiles cameras={cameras} inventory={plan?.inventory ?? []} />
      </section>
    </div>
  );
}
