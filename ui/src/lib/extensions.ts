// Rule extensions (owner-only): when the rule language cannot express a request, Muse Code writes
// a small check; Rulecam validates it, runs it in a sandbox against its own tests, boundary cases
// and mutants, has a second model review it, and runs it only after the owner approves it
// (`/extensions`). Every call goes through the same-origin rewrite with the session's CSRF token.

import { ApiError, withCsrf } from "./api";
import { API_PROXY } from "./config";
import { modelLabel } from "./models";

export type StepStatus = "passed" | "failed" | "skipped";
export type ExtensionStatus = "proposed" | "rejected" | "approved" | "retired";
export type Point = [number, number];

export interface CaseResult {
  name: string;
  source: string;
  expect: boolean | null;
  got: boolean | null;
  error: string | null;
  passed: boolean;
}

export interface StepResult {
  name: string;
  status: StepStatus;
  detail: string;
  metrics: Record<string, number>;
  cases: CaseResult[];
}

export interface ReviewResult {
  verdict: "approve" | "reject" | "error" | "skipped";
  reasons: string[];
  risks: string[];
  reviewer: "spark-open" | "spark-1.3-fallback" | "none";
  model: string;
  reasoning_ref: string | null;
}

/** One version of an extension (`ExtensionRecord` on the API). */
export interface ExtensionRecord {
  name: string;
  sha256: string;
  parent_sha: string | null;
  request: string;
  concept: string;
  camera_id: string;
  lines: Record<string, [Point, Point]>;
  relation: "above" | "below" | null;
  frame_w: number | null;
  frame_h: number | null;
  code: string;
  tests: string;
  generator: "muse-code" | "spark-fallback";
  model: string;
  proof: { steps: StepResult[]; passed: boolean };
  review: ReviewResult;
  status: ExtensionStatus;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
}

/** What the owner sends to `POST /extensions`. */
export interface ExtensionRequestBody {
  request: string;
  concept: string;
  camera_id: string;
  lines: Record<string, [Point, Point]>;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PROXY}${path}`, withCsrf(init));
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const extensions = {
  list: () => call<ExtensionRecord[]>("/extensions"),
  propose: (body: ExtensionRequestBody) => call<ExtensionRecord>("/extensions", post(body)),
  approve: (record: ExtensionRecord) =>
    call<ExtensionRecord>(
      `/extensions/${encodeURIComponent(record.name)}/approve`,
      post({ sha256: record.sha256 }),
    ),
  retire: (record: ExtensionRecord) =>
    call<ExtensionRecord>(
      `/extensions/${encodeURIComponent(record.name)}/retire`,
      post({ sha256: record.sha256 }),
    ),
};

/** The `StateCheck.state` that runs this version in a rule. */
export function extensionRef(record: ExtensionRecord): string {
  return `ext:${record.name}@${record.sha256.slice(0, 12)}`;
}

/** Proposed, proof passed and review approved: the owner may approve it. */
export function canApprove(record: ExtensionRecord): boolean {
  return record.status === "proposed" && record.proof.passed && record.review.verdict === "approve";
}

/** The mutation step's score in [0, 1], or `null` when it did not run. */
export function mutationScore(record: ExtensionRecord): number | null {
  const step = record.proof.steps.find((s) => s.name === "mutation");
  const score = step?.metrics.score;
  return typeof score === "number" ? score : null;
}

const STEP_NAMES: Record<string, string> = {
  validator: "Sandbox validator",
  muse_code_tests: "Muse Code's own tests",
  boundary: "Rulecam boundary cases",
  mutation: "Mutation testing",
  muse_image_scenes: "Muse Image scenes",
  common_sense: "Common-sense scene changes",
  math_proof: "Z3 math proof",
};

/** A proof step's name in plain words. */
export function stepTitle(name: string): string {
  return STEP_NAMES[name] ?? name.replaceAll("_", " ");
}

/** Who wrote the code, in words; the Spark fallback is never shown as Muse Code. */
export function generatorNote(record: ExtensionRecord): string {
  return record.generator === "muse-code"
    ? `Written by Muse Code (${record.model})`
    : `Written by Muse Spark (${record.model}) because the Muse Code CLI was not available`;
}

/** Who reviewed it, in words. */
export function reviewerNote(review: ReviewResult): string {
  if (review.reviewer === "spark-open")
    return `Reviewed by ${modelLabel(review.model) || "the Tier 2 model"} (Tier 2, ${review.model})`;
  if (review.reviewer === "spark-1.3-fallback")
    return `Reviewed by Muse Spark 1.3 (no Tier 2 model on this deployment)`;
  return "Not reviewed";
}

/** Parse `x1,y1,x2,y2` into a line, or `null` when it is not four numbers. */
export function parseLine(text: string): [Point, Point] | null {
  const values = text.split(",").map((v) => Number(v.trim()));
  const [x1, y1, x2, y2] = values;
  if (
    values.length !== 4 ||
    x1 === undefined ||
    y1 === undefined ||
    x2 === undefined ||
    y2 === undefined ||
    values.some((v) => !Number.isFinite(v))
  ) {
    return null;
  }
  return [
    [x1, y1],
    [x2, y2],
  ];
}
