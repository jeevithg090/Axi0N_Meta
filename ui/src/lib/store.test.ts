import { describe, expect, it } from "vitest";

import { demoFrames } from "@/fixtures/demo-frames";

import { parseFrame, type Frame } from "./events";
import {
  badgeLabel,
  initialState,
  isBlind,
  MAX_EVENTS,
  reducer,
  ruleCards,
  type UiState,
} from "./store";

const fold = (frames: Frame[], start: UiState = initialState): UiState =>
  frames.reduce((s, frame) => reducer(s, { type: "frame", frame }), start);

const candidate = (id: string, camera = "cam1", rule = "wash-before-line"): Frame => ({
  topic: "candidate.new",
  payload: {
    event_id: id,
    rule_id: rule,
    camera_id: camera,
    ts_start: "t",
    ts_end: "t",
    severity: "medium",
    predicate_trace: { subject: "s" },
    strip_paths: [],
  },
});

describe("store reducer (event → state)", () => {
  it("folds the recorded UC1 sequence", () => {
    const state = fold(demoFrames);
    expect(state.badge).toBe("REPLAY");
    expect(state.events).toHaveLength(1);
    const [view] = state.events;
    expect(view?.verdicts[0]?.verdict).toBe("violation");
    expect(view?.actions.map((a) => a.kind)).toEqual(["coach_station", "notify"]);
    expect(isBlind(state, "cam1")).toBe(true);
  });

  it("keeps newest first, ignores duplicates and caps the list", () => {
    let state = fold([candidate("a"), candidate("b"), candidate("a")]);
    expect(state.events.map((v) => v.candidate.event_id)).toEqual(["b", "a"]);
    state = fold(
      Array.from({ length: MAX_EVENTS + 5 }, (_, i) => candidate(`e${i}`)),
      state,
    );
    expect(state.events).toHaveLength(MAX_EVENTS);
  });

  it("attaches verdicts and actions only to their event", () => {
    const state = fold([
      candidate("a"),
      candidate("b"),
      {
        topic: "action.done",
        payload: {
          event_id: "a",
          action: { kind: "log", gate: "auto", ts: "t", ok: true, detail: "" },
        },
      },
    ]);
    expect(state.events.find((v) => v.candidate.event_id === "a")?.actions).toHaveLength(1);
    expect(state.events.find((v) => v.candidate.event_id === "b")?.actions).toHaveLength(0);
  });

  it("ignores unknown topics", () => {
    expect(fold([{ topic: "plan.updated", payload: {} }])).toEqual(initialState);
  });

  it("builds one card per rule and camera, greyed when that camera is BLIND", () => {
    const state = fold([
      candidate("a", "cam1"),
      candidate("b", "cam1"),
      candidate("c", "cam2"),
      { topic: "coverage.changed", payload: { camera_id: "cam2", state: "BLIND" } },
    ]);
    const cards = ruleCards(state);
    expect(cards.map((c) => [c.cameraId, c.count, c.blind])).toEqual([
      ["cam2", 1, true],
      ["cam1", 2, false],
    ]);
  });

  it("labels the badge from the server and OFFLINE when disconnected", () => {
    expect(badgeLabel(initialState)).toBe("…");
    const live = reducer(initialState, { type: "mode", mode: { mode: "record", badge: "LIVE" } });
    expect(badgeLabel(live)).toBe("LIVE");
    expect(badgeLabel(reducer(live, { type: "connection", connection: "closed" }))).toBe("OFFLINE");
  });
});

describe("parseFrame", () => {
  it("accepts frames and rejects everything else", () => {
    expect(
      parseFrame('{"topic":"mode.changed","payload":{"mode":"live","badge":"LIVE"}}')?.topic,
    ).toBe("mode.changed");
    expect(parseFrame("not json")).toBeNull();
    expect(parseFrame('{"payload":1}')).toBeNull();
    expect(parseFrame("null")).toBeNull();
  });
});
