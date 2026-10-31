"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CameraTiles } from "@/components/plan/CameraTiles";
import { RequestComposer } from "@/components/plan/RequestComposer";
import { StepProgress } from "@/components/plan/StepProgress";
import { api, type CameraInfo } from "@/lib/api";
import { useDemo, useStream } from "@/lib/stream";
import { usePlans } from "@/lib/usePlans";

export default function PlannerPage() {
  const state = useStream();
  const { plans } = usePlans();
  const [cameras, setCameras] = useState<CameraInfo[]>([]);
  const demo = useDemo() === true;

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
        <RequestComposer demo={demo} />
      </section>
      <section aria-label="Progress">
        <h2>Steps{planId ? ` · ${planId}` : ""}</h2>
        <StepProgress progress={state.planner} />
        {plan && (
          <p>
            <Link href={`/plans/${plan.plan_id}${query}`}>Open plan v{plan.version} →</Link>
          </p>
        )}
      </section>
      <section aria-label="Cameras">
        <h2>Cameras</h2>
        <CameraTiles cameras={cameras} inventory={plan?.inventory ?? []} />
      </section>
    </div>
  );
}
