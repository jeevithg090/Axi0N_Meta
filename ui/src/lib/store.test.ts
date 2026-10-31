import { describe, expect, it } from "vitest";

import { demoFrames } from "@/fixtures/demo-frames";

import { parseFrame, type Frame } from "./events";
import {
  badgeLabel,
  blindCameras,
  initialState,
  isBlind,
  MAX_EVENTS,
  noAlertsText,
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
    expect(fold([{ topic: "ci.result", payload: {} }])).toEqual(initialState);
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

describe("connection honesty", () => {
  const live = reducer(reducer(initialState, { type: "connection", connection: "open" }), {
    type: "mode",
    mode: { mode: "live", badge: "LIVE" },
  });

  it("forgets the mode when the socket drops and stays OFFLINE while reconnecting", () => {
    const dropped = reducer(live, { type: "connection", connection: "closed" });
    expect(dropped.badge).toBeNull();
    const retrying = reducer(dropped, { type: "connection", connection: "connecting" });
    expect(badgeLabel(retrying)).toBe("OFFLINE");
    const reopened = reducer(retrying, { type: "connection", connection: "open" });
    expect(badgeLabel(reopened)).toBe("…"); // until GET /mode answers, never a stale LIVE
  });

  it("resets everything when the source switches", () => {
    expect(reducer(fold(demoFrames, live), { type: "reset" })).toEqual(initialState);
  });

  it("lists BLIND cameras even when they have no alerts", () => {
    const state = fold([
      { topic: "coverage.changed", payload: { camera_id: "cam2", state: "blind" } },
      { topic: "coverage.changed", payload: { camera_id: "cam1", state: "ok" } },
      { topic: "coverage.changed", payload: { camera_id: "cam0", state: "blind" } },
    ]);
    expect(ruleCards(state)).toEqual([]);
    expect(blindCameras(state)).toEqual(["cam0", "cam2"]);
    expect(noAlertsText(state)).toBe("Not connected to the Rulecam server: no live data.");
    const open = reducer(state, { type: "connection", connection: "open" });
    expect(noAlertsText(open)).toBe("No alerts from the cameras that can see.");
    expect(noAlertsText(reducer(initialState, { type: "connection", connection: "open" }))).toBe(
      "No alerts yet.",
    );
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
