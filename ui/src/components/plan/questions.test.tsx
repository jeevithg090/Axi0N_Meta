import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Answer, PlanQuestion, PlanQuestions } from "@/lib/api";

import { FLOOR_SHAPES, QuestionsPanel, toAnswer, toImagePoint } from "./QuestionsPanel";

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const schedule: PlanQuestion = {
  step: "ask",
  question_id: "q2",
  text: "Opening hours?",
  target_field: "schedule",
  requires_frame_tap: false,
  camera_id: null,
  options: ["6am-10pm", "24 hours"],
};
const calibrate: PlanQuestion = {
  step: "accept",
  question_id: "calibrate-1",
  text: "Mark a known distance on cam1",
  target_field: "calibration",
  requires_frame_tap: true,
  camera_id: "cam1",
  options: [],
};

let reply: PlanQuestions;
let answerStatus: number;
const sent: Answer[][] = [];

beforeEach(() => {
  reply = { plan_id: "p1", questions: [schedule, calibrate], running: false, answered: 0 };
  answerStatus = 202;
  sent.length = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/plans/p1/answers")) {
      sent.push((JSON.parse(String(init?.body)) as { answers: Answer[] }).answers);
      if (answerStatus !== 202)
        return json({ detail: "the calibration does not fit" }, answerStatus);
      reply = { ...reply, running: true, answered: 1 };
      return json({ plan_id: "p1", answers: 1 }, 202);
    }
    if (url.endsWith("/plans/p9/questions")) return json({ detail: "owner login required" }, 403);
    return json(reply);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("planner questions", () => {
  it("sends an option and re-plans", async () => {
    render(<QuestionsPanel planId="p1" />);
    expect(await screen.findAllByTestId("question")).toHaveLength(2);
    const send = screen.getByText("Send answers and re-plan") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.click(screen.getByText("24 hours"));
    expect(screen.getByLabelText("Answer: Opening hours?")).toHaveProperty("value", "24 hours");
    fireEvent.click(send);
    await waitFor(() => expect(sent).toEqual([[{ question_id: "q2", text: "24 hours" }]]));
    expect((await screen.findByRole("status")).textContent).toContain("Re-planning with 1 answer");
  });

  it("shows API errors, including a login prompt", async () => {
    answerStatus = 422;
    render(<QuestionsPanel planId="p1" />);
    fireEvent.change(await screen.findByLabelText("Answer: Opening hours?"), {
      target: { value: "always" },
    });
    fireEvent.click(screen.getByText("Send answers and re-plan"));
    expect((await screen.findByRole("status")).textContent).toBe("the calibration does not fit");
    cleanup();
    render(<QuestionsPanel planId="p9" />);
    expect((await screen.findByRole("alert")).textContent).toContain("Log in as the owner");
  });

  it("says when there is nothing to answer or a run is going", async () => {
    reply = { ...reply, questions: [] };
    render(<QuestionsPanel planId="p1" />);
    expect(await screen.findByText("The planner has no open questions.")).toBeDefined();
    cleanup();
    reply = { ...reply, running: true };
    render(<QuestionsPanel planId="p1" />);
    expect((await screen.findByRole("status")).textContent).toContain("working on this plan");
  });

  it("shows the camera frame for tap questions", async () => {
    render(<QuestionsPanel planId="p1" />);
    const image = (await screen.findByAltText("Latest frame of cam1")) as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("/rulecam-api/plans/p1/frames/cam1");
    expect(screen.getByText(/four corners/).textContent).toContain("(0/4)");
  });
});

describe("answer building", () => {
  const box = { left: 10, top: 20, width: 320, height: 240 };
  const natural = { width: 640, height: 480 };

  it("maps a click on the scaled frame to image pixels", () => {
    expect(toImagePoint(10, 20, box, natural)).toEqual([0, 0]);
    expect(toImagePoint(170, 140, box, natural)).toEqual([320, 240]);
    expect(toImagePoint(999, 999, box, natural)).toEqual([640, 480]);
    expect(toImagePoint(10, 20, { ...box, width: 0 }, natural)).toBeNull();
  });

  it("needs four corners for a calibration and one tap otherwise", () => {
    const taps: [number, number][] = [
      [1, 2],
      [3, 4],
      [5, 6],
      [7, 8],
    ];
    expect(toAnswer(calibrate, { text: "", taps: taps.slice(0, 3), shape: "pallet" })).toBeNull();
    expect(toAnswer(calibrate, { text: "", taps, shape: "pallet" })).toEqual({
      question_id: "calibrate-1",
      calibration: { image_px: taps, ground_m: FLOOR_SHAPES.pallet!.ground },
    });
    const door = { ...calibrate, target_field: "exit", question_id: "q3" };
    expect(toAnswer(door, { text: " back ", taps: [[9, 9]], shape: "square_1m" })).toEqual({
      question_id: "q3",
      text: "back",
      tap: { x: 9, y: 9 },
    });
    expect(toAnswer(schedule, { text: "  ", taps: [], shape: "square_1m" })).toBeNull();
  });
});
