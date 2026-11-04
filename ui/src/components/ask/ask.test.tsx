import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { askReducer, newExchange, type AskEvent } from "@/lib/ask";

import { AskTimeline } from "./AskTimeline";

afterEach(cleanup);

const sql = { thought: "count people", action: "sql" as const, sql: "SELECT count(*) AS n" };
const count = {
  thought: "check with SAM",
  action: "count_objects" as const,
  concept: "person",
  camera_id: "checkout",
  times: ["2026-11-02T09:45:11Z"],
};

const events: AskEvent[] = [
  { type: "start", conversation_id: "c1" },
  { type: "thinking", turn: 0 },
  { type: "actions", turn: 0, seconds: 2.1, actions: [sql, count] },
  {
    type: "step",
    turn: 0,
    index: 0,
    step: {
      turn: 0,
      action: sql,
      result: { columns: ["n"], rows: [[5]], total_rows: 9, note: "showing the first 1 of 9" },
      seconds: 0.1,
      model: null,
      frames: [],
    },
  },
];

describe("AskTimeline", () => {
  it("shows each tool as it runs, with its SQL, rows and the API's cut note", () => {
    const exchange = events.reduce(askReducer, newExchange("How many people?"));
    render(<AskTimeline exchange={exchange} />);
    const actions = screen.getAllByTestId("ask-action");
    expect(actions).toHaveLength(2);
    expect(actions[0]?.textContent).toContain("SELECT count(*) AS n");
    expect(actions[0]?.textContent).toContain("1 of 9 rows");
    expect(actions[0]?.textContent).toContain("showing the first 1 of 9");
    expect(actions[1]?.textContent).toContain("working…");
    expect(screen.getByTestId("ask-turn").textContent).toContain("2 at once");
    expect(screen.queryByTestId("ask-answer")).toBeNull();
  });

  it("shows frames as thumbnails and the answer with its models and time", () => {
    const done = [
      ...events,
      {
        type: "step",
        turn: 0,
        index: 1,
        step: {
          turn: 0,
          action: count,
          result: { frames: [{ at: "09:45:11", count: 5, boxes: [[1, 2, 30, 40, 0.9]] }] },
          seconds: 3.2,
          model: "sam-3.1",
          frames: ["abc.jpg"],
        },
      },
      {
        type: "answer",
        answer: {
          question: "How many people?",
          answer: "5 people at 9:45:11.",
          steps: [],
          seconds: 9.5,
          models: ["muse-spark-1.3", "sam-3.1"],
          conversation_id: "c1",
          turns: [{ turn: 0, model: "muse-spark-1.3", seconds: 2.1, actions: 2 }],
          receipt: "0123456789abcdef".repeat(4),
        },
      },
    ] satisfies AskEvent[];
    const exchange = done.reduce(askReducer, newExchange("How many people?"));
    render(<AskTimeline exchange={exchange} />);
    const image = screen.getByRole("img", { name: /5 · 09:45:11/ });
    expect(image.getAttribute("src")).toBe("/rulecam-api/ask/frames/abc.jpg");
    const answer = screen.getByTestId("ask-answer").textContent ?? "";
    expect(answer).toContain("5 people at 9:45:11.");
    expect(answer).toContain("9.5 s");
    expect(answer).toContain("muse-spark-1.3, sam-3.1");
    expect(answer).toContain("receipt 0123456789ab");
  });

  it("shows a review that sent the draft back, with its problems and the draft", () => {
    const review = {
      verdict: "revise" as const,
      problems: ["contradicts the earlier answer"],
      advice: "explain the difference",
      draft: "Ten people.",
      turn: 0,
      model: "muse-wonder-1.0",
      seconds: 3.1,
    };
    const evts: AskEvent[] = [
      { type: "thinking", turn: 0 },
      {
        type: "actions",
        turn: 0,
        actions: [{ thought: "d", action: "answer", answer: "Ten people." }],
      },
      { type: "reviewing", turn: 0 },
    ];
    const running = evts.reduce(askReducer, newExchange("How many?"));
    const { rerender } = render(<AskTimeline exchange={running} />);
    expect(screen.getByTestId("ask-reviewing").textContent).toContain("Tier 2 model is checking");
    rerender(<AskTimeline exchange={askReducer(running, { type: "review", turn: 0, review })} />);
    const row = screen.getByTestId("ask-review").textContent ?? "";
    expect(row).toContain("Muse Wonder 1.0 sent the answer back"); // named from the review's model
    expect(row).toContain("contradicts the earlier answer");
    expect(row).toContain("Ten people.");
    expect(screen.queryByTestId("ask-reviewing")).toBeNull();
  });

  it("shows a watch step's answer with its moments and window", () => {
    const watch = {
      thought: "entries show as movement",
      action: "watch" as const,
      camera_id: "corridor",
      times: ["09:44:00", "09:46:00"],
      question: "Who goes into a shop?",
    };
    const evts: AskEvent[] = [
      { type: "thinking", turn: 0 },
      { type: "actions", turn: 0, actions: [watch] },
      {
        type: "step",
        turn: 0,
        index: 0,
        step: {
          turn: 0,
          action: watch,
          result: {
            answer: "One person enters the left shop.",
            count: 1,
            moments: [{ at: "09:45:02", what: "enters the left shop" }],
          },
          seconds: 12.5,
          model: "muse-spark-1.2",
          frames: [],
        },
      },
    ];
    render(<AskTimeline exchange={evts.reduce(askReducer, newExchange("q"))} />);
    const row = screen.getByTestId("ask-action").textContent ?? "";
    expect(row).toContain("Watch (Spark video)");
    expect(row).toContain("from 09:44:00 to 09:46:00");
    expect(row).toContain("muse-spark-1.2");
    expect(screen.getByTestId("ask-moments").textContent).toContain(
      "09:45:02 enters the left shop",
    );
  });
});
