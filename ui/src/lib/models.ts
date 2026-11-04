// Which model does each job, as the API says it (`GET /health` → `models`, or a verdict's
// `model_version`). The UI keeps no copy of the running models: a job the API has not named is
// called by its role ("Tier 1 check"). Recorded runs keep the model that really ran.
// Pure functions only (no "use client"), so server components can name models too.

import type { Health } from "./health";

/** One endpoint probe in `GET /health` → `gpus` (`runtime.serverless.GpuHealth`). */
export interface GpuHealth {
  state?: string;
  up?: boolean;
  serverless?: boolean;
  workers_max?: number;
  workers?: { running?: number; idle?: number; initializing?: number };
  error?: string | null;
}

/** Where a model runs: the Meta API, our own server or GPU, or another provider. */
export type ModelHost = "meta-api" | "self-hosted" | "hosted";

/** `models` in `GET /health` (`rulecam.api.app.ModelsInUse`). */
export interface ModelsInUse {
  tier1?: string;
  tier1_host?: ModelHost;
  tier2?: string | null;
  tier2_host?: ModelHost | null;
  sam?: string;
  sam_host?: ModelHost;
}

/** The jobs by role, for when the API has not named the model. */
export const ROLES = {
  tier1: "Tier 1 check",
  tier2: "Tier 2 review",
  sam: "SAM 3.1",
} as const;

/** A GPU line: its state as the API wrote it, and its workers when it is serverless. */
export function gpuLine(entry: GpuHealth | string | undefined): { state: string; workers: string | null } {
  if (entry === undefined) return { state: "unknown", workers: null };
  if (typeof entry === "string") return { state: entry, workers: null };
  // The serverless counts exactly as the API reports them (they can exceed workers_max while
  // RunPod scales), never added up into a figure the API did not give.
  const w = entry.workers;
  const workers = w
    ? [`${w.running ?? 0} running`, `${w.idle ?? 0} idle`, ...(w.initializing ? [`${w.initializing} starting`] : [])].join(", ")
    : null;
  return { state: entry.state ?? (entry.up ? "up" : "down"), workers };
}

const ACRONYMS = new Set(["sam", "vlm", "llm"]);

/**
 * A model id as people say it: "muse-spark-1.3" → "Muse Spark 1.3", "muse-glimmer-30b" →
 * "Muse Glimmer 30B", "sam-3.1" → "SAM 3.1". A provider prefix ("meta-models/") and a variant
 * suffix (":free") are dropped. Empty for no id.
 */
export function modelLabel(id: string | null | undefined): string {
  const bare = (id ?? "").trim().split("/").pop()?.split(":")[0] ?? "";
  return bare
    .split(/[-_]/)
    .filter(Boolean)
    .map((word) => {
      if (ACRONYMS.has(word.toLowerCase())) return word.toUpperCase();
      if (/^\d+(\.\d+)?[bm]$/i.test(word)) return word.toUpperCase();
      return word.charAt(0).toUpperCase() + word.slice(1);
    })
    .join(" ");
}

const HOSTS: Record<ModelHost, string> = {
  "meta-api": "Meta API",
  "self-hosted": "our GPU",
  hosted: "hosted",
};

/** "Meta API", "our GPU" or "hosted"; empty when the API has not said. */
export function hostLabel(host: ModelHost | null | undefined): string {
  return host ? HOSTS[host] : "";
}

/** "Muse Spark 1.3 · Meta API", or `fallback` (a role) when the API has not named the model. */
export function modelLine(id: string | null | undefined, host: ModelHost | null | undefined, fallback: string): string {
  const name = modelLabel(id);
  if (!name) return fallback;
  const where = hostLabel(host);
  return where ? `${name} · ${where}` : name;
}

/** A verifier tier as a role: `glimmer` (the Tier 1 client) → "Tier 1", `spark_open` → "Tier 2". */
export function tierRole(tier: string | null | undefined): string {
  if (!tier || tier === "glimmer") return "Tier 1";
  if (tier === "spark_open" || tier === "spark-open") return "Tier 2";
  return modelLabel(tier);
}

/** A verdict's heading: its tier's role and the model that ran it, "Tier 1 · Muse Spark 1.3". */
export function verifierLabel(tier: string | null | undefined, modelVersion: string | null | undefined): string {
  const role = tierRole(tier);
  const name = modelLabel(modelVersion);
  return name && name !== role ? `${role} · ${name}` : role;
}

/** One job's line for the status lists (sidebar, landing band, use-case chip, System page). */
export interface ModelRow {
  key: keyof typeof ROLES;
  role: string;
  /** The model's name, or `null` when the API has not named it. */
  model: string | null;
  host: ModelHost | null;
  /** Where it runs and, when it has a probe, how that endpoint answered ("Meta API", "warm"). */
  status: string;
  /** Serverless worker counts, when the probe reports them. */
  workers: string | null;
}

function row(
  key: ModelRow["key"],
  id: string | null | undefined,
  host: ModelHost | null | undefined,
  probe: GpuHealth | string | undefined,
): ModelRow {
  const line = probe === undefined ? null : gpuLine(probe);
  const fine = line === null || line.state === "warm" || line.state === "up";
  let status: string;
  if (id === null) status = "not configured";
  else if (host === "meta-api") status = fine ? "Meta API" : `Meta API · ${line.state}`;
  else status = line?.state ?? (hostLabel(host) || "unknown");
  return { key, role: ROLES[key], model: modelLabel(id) || null, host: host ?? null, status, workers: line?.workers ?? null };
}

/**
 * The Tier 1, Tier 2 and SAM lines of a `GET /health` answer: each named from `models` and stated
 * from its endpoint probe (`gpus`). Without `models` (an older API) the lines keep their roles and
 * the probes' states.
 */
export function modelRows(health: Health | null): ModelRow[] {
  const models = health?.models ?? null;
  const gpus = health?.gpus ?? {};
  return [
    row("tier1", models?.tier1, models?.tier1_host, gpus.glimmer),
    // `null` from the API means Tier 2 is not configured; missing means the API did not say.
    row("tier2", models ? (models.tier2 ?? null) : undefined, models?.tier2_host, undefined),
    row("sam", models?.sam, models?.sam_host, gpus.sam),
  ];
}
