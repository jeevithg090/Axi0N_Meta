import { afterEach, describe, expect, it, vi } from "vitest";

import {
  askReducer,
  isRunning,
  newExchange,
  parseSse,
  stepSummary,
  streamAsk,
  type AskEvent,
  type AskStep,
} from "./ask";

const sqlAction = { thought: "count", action: "sql" as const, sql: "SELECT 1" };
const lookAction = { thought: "see", action: "look" as const, camera_id: "c", times: ["t"] };

const step = (result: unknown, index = 0): AskStep => ({
  turn: 0,
  action: index === 0 ? sqlAction : lookAction,
  result,
  seconds: 1.2,
  model: index === 0 ? null : "muse-glimmer-30b",
  frames: [],
});

const answer = {
  question: "q",
  answer: "Two.",
  steps: [],
  seconds: 3,
  models: ["muse-spark-1.3"],
  conversation_id: "abc",
  turns: [],
};

describe("parseSse", () => {
  it("splits complete events, keeps the partial one and drops keep-alives", () => {
    const { messages, rest } = parseSse(
      'event: thinking\ndata: {"type":"thinking"}\n\n: keep-alive\n\nevent: step\ndata: {"ty',
    );
    expect(messages).toEqual([{ event: "thinking", data: '{"type":"thinking"}' }]);
    expect(rest).toBe('event: step\ndata: {"ty');
  });

  it("handles CRLF line endings", () => {
    expect(parseSse("event: a\r\ndata: 1\r\n\r\n").messages).toEqual([{ event: "a", data: "1" }]);
  });
});

describe("askReducer", () => {
  it("tracks a review in progress, then the review, then the answer's reviews", () => {
    const review = {
      verdict: "revise" as const,
      problems: ["counts a cut result"],
      advice: "page the rows",
      draft: "Ten.",
      turn: 0,
      model: "muse-spark-open",
      seconds: 4.2,
    };
    const events: AskEvent[] = [
      { type: "thinking", turn: 0 },
      { type: "actions", turn: 0, actions: [{ thought: "d", action: "answer", answer: "Ten." }] },
      { type: "reviewing", turn: 0 },
    ];
    const reviewing = events.reduce(askReducer, newExchange("q"));
    expect(reviewing.reviewing).toBe(0);
    const reviewed = askReducer(reviewing, { type: "review", turn: 0, review });
    expect(reviewed.reviewing).toBeNull();
    expect(reviewed.reviews).toEqual([review]);
    const done = askReducer(reviewed, { type: "answer", answer: { ...answer, reviews: [review] } });
    expect(done.reviews).toEqual([review]);
    expect(isRunning(done)).toBe(false);
  });

  it("builds the timeline turn by turn, steps finishing in any order", () => {
    const events: AskEvent[] = [
      { type: "start", conversation_id: "abc" },
      { type: "thinking", turn: 0 },
      { type: "actions", turn: 0, seconds: 2.5, actions: [sqlAction, lookAction] },
      { type: "step", turn: 0, index: 1, step: step({ answer: "two" }, 1) },
    ];
    let exchange = events.reduce(askReducer, newExchange("How many?"));
    expect(exchange.conversationId).toBe("abc");
    expect(exchange.turns[0]?.status).toBe("running");
    expect(exchange.turns[0]?.actions[1]?.step?.model).toBe("muse-glimmer-30b");
    expect(exchange.turns[0]?.actions[0]?.step).toBeNull();

    exchange = askReducer(exchange, {
      type: "step",
      turn: 0,
      index: 0,
      step: step({ columns: ["n"], rows: [[2]] }),
    });
    expect(exchange.turns[0]?.status).toBe("done");
    expect(isRunning(exchange)).toBe(true);

    exchange = askReducer(exchange, { type: "answer", answer });
    expect(isRunning(exchange)).toBe(false);
    expect(exchange.answer?.answer).toBe("Two.");
  });

  it("ends on an error", () => {
    const exchange = askReducer(newExchange("q"), { type: "error", detail: "boom" });
    expect(exchange.error).toBe("boom");
    expect(isRunning(exchange)).toBe(false);
  });
});

describe("stepSummary", () => {
  it("says how much of a capped result Spark got", () => {
    expect(stepSummary(step({ columns: ["a"], rows: [[1], [2]], total_rows: 900 }))).toBe(
      "2 of 900 rows",
    );
    expect(stepSummary(step({ frames: [{ count: 3 }, { count: 4 }] }))).toBe("counts 3, 4");
    expect(stepSummary(step({ error: "bad" }))).toBe("error: bad");
  });
});

describe("streamAsk", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads events from a chunked stream until it ends", async () => {
    const encoder = new TextEncoder();
    const parts = [
      'event: start\ndata: {"type":"start","conversation_id":"x"}\n\nevent: ans',
      'wer\ndata: {"type":"answer","answer":{"answer":"ok"}}\n\n',
    ];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const part of parts) controller.enqueue(encoder.encode(part));
        controller.close();
      },
    });
    const fetchMock = vi.fn(async () => new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const seen: AskEvent[] = [];
    await streamAsk({ question: "q" }, (e) => seen.push(e));
    expect(seen.map((e) => e.type)).toEqual(["start", "answer"]);
    expect(fetchMock).toHaveBeenCalledWith("/rulecam-api/ask/stream", expect.anything());
  });

  it("turns an error reply into an ApiError with the API's detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "asking needs prototype mode" }, { status: 503 })),
    );
    await expect(streamAsk({ question: "q" }, () => undefined)).rejects.toMatchObject({
      status: 503,
      message: "asking needs prototype mode",
    });
  });
});
