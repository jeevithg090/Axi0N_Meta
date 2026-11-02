// Ask: the owner's free-form questions about what the cameras saw, answered by Spark with SQL,
// SAM 3.1 and Glimmer (`POST /ask/stream`, owner-only, prototype mode). The API streams each
// step as a server-sent event the moment it happens; `askReducer` folds them into a timeline.

import { ApiError, withCsrf } from "./api";
import { API_PROXY } from "./config";

export type AskActionKind = "sql" | "count_objects" | "look" | "answer";

/** One action of a turn: a tool call, or the final answer. */
export interface AskAction {
  thought: string;
  action: AskActionKind;
  sql?: string | null;
  concept?: string | null;
  camera_id?: string | null;
  times?: string[];
  question?: string | null;
  marks?: boolean;
  answer?: string | null;
}

/** What one action did: its result, seconds, the model that ran it and the frames it saw. */
export interface AskStep {
  turn: number;
  action: AskAction;
  result: unknown;
  seconds: number;
  model: string | null;
  frames: string[];
}

export interface AskTurnTiming {
  turn: number;
  model: string;
  seconds: number;
  actions: number;
}

export interface AskAnswer {
  question: string;
  answer: string;
  steps: AskStep[];
  seconds: number;
  models: string[];
  conversation_id: string;
  turns: AskTurnTiming[];
  /** The numbers the answer gives, in the order asked (for benchmarks). */
  numbers?: (number | null)[];
  /** SHA-256 of the instructions the models got. */
  prompt_sha256?: string;
  /** SHA-256 of this answer's row in the hash-chained run log (empty if unlogged). */
  receipt?: string;
}

export type AskEventType = "start" | "thinking" | "actions" | "step" | "answer" | "error";

export interface AskEvent {
  type: AskEventType;
  conversation_id?: string | null;
  turn?: number | null;
  index?: number | null;
  seconds?: number | null;
  actions?: AskAction[];
  step?: AskStep | null;
  answer?: AskAnswer | null;
  detail?: string | null;
}

export interface AskRequest {
  question: string;
  conversation_id?: string | null;
}

/** One action in the timeline: what Spark asked for, and its step once it finished. */
export interface ActionView {
  action: AskAction;
  step: AskStep | null;
}

export interface TurnView {
  turn: number;
  /** thinking: Spark is choosing; running: tools are working; done: all finished. */
  status: "thinking" | "running" | "done";
  seconds: number | null;
  actions: ActionView[];
}

/** One question and everything that happened while answering it. */
export interface Exchange {
  question: string;
  conversationId: string | null;
  turns: TurnView[];
  answer: AskAnswer | null;
  error: string | null;
}

export function newExchange(question: string, conversationId: string | null = null): Exchange {
  return { question, conversationId, turns: [], answer: null, error: null };
}

/** Whether an exchange is still being answered. */
export function isRunning(exchange: Exchange): boolean {
  return exchange.answer === null && exchange.error === null;
}

function updateTurn(exchange: Exchange, turn: number, change: (t: TurnView) => TurnView): Exchange {
  return { ...exchange, turns: exchange.turns.map((t) => (t.turn === turn ? change(t) : t)) };
}

function settled(actions: ActionView[]): boolean {
  return actions.every((a) => a.step !== null || a.action.action === "answer");
}

/** Fold one streamed event into the exchange. */
export function askReducer(exchange: Exchange, event: AskEvent): Exchange {
  const turn = event.turn ?? -1;
  switch (event.type) {
    case "start":
      return { ...exchange, conversationId: event.conversation_id ?? exchange.conversationId };
    case "thinking":
      return {
        ...exchange,
        turns: [
          ...exchange.turns.filter((t) => t.turn !== turn),
          { turn, status: "thinking", seconds: null, actions: [] },
        ],
      };
    case "actions": {
      const actions = (event.actions ?? []).map((action) => ({ action, step: null }));
      return updateTurn(exchange, turn, (t) => ({
        ...t,
        actions,
        seconds: event.seconds ?? null,
        status: settled(actions) ? "done" : "running",
      }));
    }
    case "step":
      return updateTurn(exchange, turn, (t) => {
        const actions = t.actions.map((a, k) =>
          k === event.index ? { ...a, step: event.step ?? null } : a,
        );
        return { ...t, actions, status: settled(actions) ? "done" : t.status };
      });
    case "answer":
      return {
        ...exchange,
        answer: event.answer ?? null,
        conversationId: event.answer?.conversation_id ?? exchange.conversationId,
        turns: exchange.turns.map((t) => ({ ...t, status: "done" })),
      };
    case "error":
      return { ...exchange, error: event.detail ?? "Answering failed." };
  }
}

/** One parsed server-sent event (`event:` name and joined `data:` lines). */
export interface SseMessage {
  event: string;
  data: string;
}

/** Split complete SSE blocks off `buffer`; comments (keep-alives) are dropped. */
export function parseSse(buffer: string): { messages: SseMessage[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const messages: SseMessage[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) messages.push({ event, data: data.join("\n") });
  }
  return { messages, rest };
}

/** Ask a question and call `onEvent` with each step as the API streams it. */
export async function streamAsk(
  body: AskRequest,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `${API_PROXY}/ask/stream`,
    withCsrf({
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    }),
  );
  if (!response.ok || response.body === null) {
    let detail = response.statusText;
    try {
      const parsed = (await response.json()) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const { messages, rest } = parseSse(done ? `${buffer}\n\n` : buffer);
    buffer = rest;
    for (const message of messages) onEvent(JSON.parse(message.data) as AskEvent);
    if (done) return;
  }
}

/** The thumbnail URL of a frame a step showed a model. */
export function frameUrl(name: string): string {
  return `${API_PROXY}/ask/frames/${encodeURIComponent(name)}`;
}

const TOOL_LABEL: Record<AskActionKind, string> = {
  sql: "SQL",
  count_objects: "Count (SAM 3.1)",
  look: "Look (Glimmer)",
  answer: "Answer",
};

export function toolLabel(kind: AskActionKind): string {
  return TOOL_LABEL[kind];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A one-line summary of a step's result for the collapsed timeline row. */
export function stepSummary(step: AskStep): string {
  const result = step.result;
  if (!isRecord(result)) return "";
  if (typeof result.error === "string") return `error: ${result.error}`;
  if (Array.isArray(result.results)) return `${result.results.length} statements`;
  if (Array.isArray(result.rows)) {
    const shown = result.rows.length;
    const total = typeof result.total_rows === "number" ? result.total_rows : null;
    return total !== null && total > shown ? `${shown} of ${total} rows` : `${shown} rows`;
  }
  if (Array.isArray(result.frames)) {
    const counts = result.frames.map((f) =>
      isRecord(f) && typeof f.count === "number" ? f.count : "?",
    );
    return `counts ${counts.join(", ")}`;
  }
  if (typeof result.answer === "string") return result.answer;
  return "";
}
