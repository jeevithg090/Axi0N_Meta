"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "./api";
import { useDemo, useDispatch, useGolden, useStream } from "./stream";

/**
 * Loads every plan into the store once (from `/plans`, or the golden run's approved plan in demo
 * mode) and returns the store's plans plus any load error. `plan.updated` frames keep them current.
 * `forbidden` is true when the API refused the caller (403: plans are for the site owner), so a
 * page can show the owner-only notice instead of the API's refusal text.
 */
export function usePlans() {
  const state = useStream();
  const dispatch = useDispatch();
  const demo = useDemo();
  const golden = useGolden();
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    setError(null);
    setForbidden(false);
    if (demo === null) return undefined;
    if (demo) {
      if (golden) dispatch({ type: "plan-loaded", plan: golden.plan, approved: true });
      return undefined;
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
        if (!cancelled) {
          setError(e instanceof ApiError ? e.message : "Could not reach the Rulecam API");
          setForbidden(e instanceof ApiError && e.status === 403);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [demo, golden, dispatch]);

  return { plans: state.plans, error, forbidden };
}
