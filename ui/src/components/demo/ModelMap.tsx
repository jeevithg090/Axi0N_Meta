"use client";

import { useSharedHealth } from "@/lib/health";
import { modelLabel, ROLES, type ModelHost } from "@/lib/models";

import styles from "./landing.module.css";

/** A model whose job and status do not depend on the deployment's configuration. */
export interface FixedModel {
  model: string;
  job: string;
  status: string;
}

function liveStatus(host: ModelHost | null | undefined, configured: boolean): string {
  if (!configured) return "Not configured in this deployment";
  if (host === "meta-api") return "Live (Meta API)";
  if (host === "self-hosted") return "Live (our GPU)";
  if (host === "hosted") return "Live (hosted)";
  return "Live";
}

/** "Muse Spark 1.3 (Tier 1 check)", or the role alone when the API has not named the model. */
function named(id: string | null | undefined, role: string): string {
  const name = modelLabel(id);
  return name && name !== role ? `${name} (${role})` : role;
}

/**
 * Which Meta model does what. The Tier 1, Tier 2 and SAM rows are read from `GET /health`
 * (`models`), so they always name the model the deployment runs now; the others are fixed.
 */
export function ModelMap({ fixed }: { fixed: readonly FixedModel[] }) {
  const load = useSharedHealth();
  const live = load.phase === "ok" ? (load.health.models ?? null) : null;
  const unknown = load.phase === "loading" ? "Checking the API…" : "Not known (the API did not say)";
  const jobs = [
    {
      key: "tier1",
      model: named(live?.tier1, ROLES.tier1),
      job: "Checks every alert on the video frames before anyone is bothered",
      status: live ? liveStatus(live.tier1_host, Boolean(live.tier1)) : unknown,
    },
    {
      key: "tier2",
      model: named(live?.tier2, ROLES.tier2),
      job: "Second opinion: worker disputes, the plan audit and Ask's answers",
      status: live ? liveStatus(live.tier2_host, Boolean(live.tier2)) : unknown,
    },
    {
      key: "sam",
      model: named(live?.sam, ROLES.sam),
      job: "Finds and tracks people and objects; probes what each camera can see",
      status: live ? liveStatus(live.sam_host, true) : unknown,
    },
  ];
  const rows = [
    ...fixed.slice(0, 1).map((m) => ({ key: m.model, ...m })),
    ...jobs,
    ...fixed.slice(1).map((m) => ({ key: m.model, ...m })),
  ];
  return (
    <>
      <div className="scroll-x">
        <table className={styles.models} data-testid="model-map">
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Job</th>
              <th scope="col">Today</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.key} data-testid={`model-row-${m.key}`}>
                <th scope="row">{m.model}</th>
                <td>{m.job}</td>
                <td className={styles.modelStatus} data-live={m.status.startsWith("Live") ? "true" : "false"}>
                  {m.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.modelsNote}>
        The recorded runs on this page (1 and 2 Nov 2026) were checked by Muse Glimmer 30B (open
        weights) on our own GPUs.
      </p>
    </>
  );
}
