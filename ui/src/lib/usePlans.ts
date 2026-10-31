"use client";

import { useEffect, useState } from "react";

import { demoPlanV1 } from "@/fixtures/demo-plan";

import { api, ApiError } from "./api";
import { isDemo, useDispatch, useStream } from "./stream";

/**
 * Loads every plan into the store once (from `/plans`, or the recorded UC1 plan in demo mode)
 * and returns the store's plans plus any load error. `plan.updated` frames keep them current.
 */
export function usePlans() {
  const state = useStream();
  const dispatch = useDispatch();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isDemo()) {
      dispatch({ type: "plan-loaded", plan: demoPlanV1, approved: true });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const summaries = await api.plans();
        for (const summary of summaries) {
          const plan = await api.plan(summary.plan_id);
          if (!cancelled) dispatch({ type: "plan-loaded", plan, approved: summary.approved });
        }
      } catch (e) {
        if (!cancelled)
          setError(e instanceof ApiError ? e.message : "Could not reach the Rulecam API");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  return { plans: state.plans, error };
}
