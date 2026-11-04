import { describe, expect, it } from "vitest";

import type { Alert } from "@/lib/ops";

import {
  alertLine,
  audioUrl,
  frameUrl,
  glimmerLabel,
  linesToSay,
  lookForm,
  progressLabel,
  modelName,
  scaledSize,
  shouldSpeak,
  stageLine,
  unspokenAlerts,
  verdictLabel,
  untilDone,
  type Job,
  type PovAnswer,
} from "./glasses";

function answer(over: Partial<PovAnswer> = {}): PovAnswer {
  return {
    answer_id: "a1",
    at: "2026-11-04T01:00:00Z",
    mode: "warehouse",
    kind: "question",
    question: "Safe?",
    heard: null,
    first: null,
    spoken: "Stop, forklift ahead.",
    detail: "",
    alert: true,
    verified: true,
    verified_by: ["muse-spark-1.3", "muse-glimmer-30b", "sam-3.1"],
    severity: "danger",
    confidence: 0.9,
    hazards: [],
    counts: [],
    gaps: [],
    look: null,
    spark_look: null,
    rejected: [],
    privacy_edits: 0,
    looked_by: "muse-glimmer-30b",
    frame: "a1.jpg",
    marked: null,
    audio: null,
    voice: null,
    stages: [],
    total_ms: 0,
    walk_id: null,
    source: "phone-camera",
    ...over,
  };
}

function alert(id: string, status: Alert["status"] = "open"): Alert {
  return {
    alert_id: id,
    event_id: null,
    role: "supervisor",
    title: "Forklift near people",
    body: "Dock 2\nmore",
    clip_path: null,
    buttons: [],
    ts: "2026-11-04T01:00:00Z",
    status,
  };
}

describe("glasses helpers", () => {
  it("builds the look form", () => {
    const form = lookForm({
      frame: new Blob(["x"], { type: "image/jpeg" }),
      mode: "aisle",
      source: "phone-camera",
      question: "  any gaps? ",
      audio: new Blob(["RIFF"], { type: "audio/wav" }),
      walkId: "walk-1",
    });
    expect(form.get("question")).toBe("any gaps?");
    expect(form.get("mode")).toBe("aisle");
    expect(form.get("walk_id")).toBe("walk-1");
    expect(form.get("speak")).toBe("true");
    expect(form.get("lang")).toBe("en");
    expect((form.get("image") as File).name).toBe("frame.jpg");
    expect((form.get("audio") as File).name).toBe("question.wav");
    const bare = lookForm({
      frame: new Blob(["x"], { type: "image/png" }),
      mode: "crowd",
      source: "upload",
      speak: false,
    });
    expect(bare.get("question")).toBeNull();
    expect(bare.get("audio")).toBeNull();
    expect(bare.get("speak")).toBe("false");
    expect(bare.get("wait")).toBe("false");
    expect((bare.get("image") as File).name).toBe("frame.png");
  });

  it("labels verdicts", () => {
    expect(verdictLabel(answer())).toBe("Verified alert · Spark + Glimmer + SAM 3.1");
    expect(verdictLabel(answer({ verified: false, verified_by: ["muse-spark-1.3"] }))).toBe(
      "Unverified alert · only one model looked",
    );
    expect(verdictLabel(answer({ alert: false, severity: "none" }))).toBe("No hazard confirmed");
    expect(verdictLabel(answer({ alert: false, severity: "info" }))).toBe("Noted, no alert");
    expect(modelName("muse-voice-transcribe-1.0")).toBe("Voice Transcribe");
    expect(modelName("other")).toBe("other");
    expect(glimmerLabel("down")).toBe("Glimmer down · Spark looks");
    expect(glimmerLabel("up")).toBe("Glimmer up");
    expect(glimmerLabel("checking")).toBe("Waking Glimmer…");
    expect(glimmerLabel("none")).toBe("No Glimmer · Spark looks");
    expect(glimmerLabel("unknown")).toBe("Glimmer ?");
  });

  it("says the first line once, then the checked line", () => {
    const first = { text: "Forklift ahead.", audio: "a1-1.wav", by: "muse-spark-1.3", at_ms: 6200 };
    const both = answer({ first, spoken: "Both models agree.", audio: "phrase-agree.wav" });
    expect(linesToSay(both, false)).toEqual([
      { text: "Forklift ahead.", audio: "a1-1.wav" },
      { text: "Both models agree.", audio: "phrase-agree.wav" },
    ]);
    expect(linesToSay(both, true)).toEqual([
      { text: "Both models agree.", audio: "phrase-agree.wav" },
    ]);
    expect(linesToSay(answer({ first, spoken: "" }), true)).toEqual([]);
  });

  it("speaks only what the server wrote", () => {
    expect(shouldSpeak(answer())).toBe(true);
    expect(shouldSpeak(answer({ spoken: "  " }))).toBe(false);
  });

  it("formats stages and urls", () => {
    expect(
      stageLine([
        { name: "look", model: "g", ms: 3100, ok: true, note: "" },
        { name: "compose", model: "s", ms: 5650, ok: false, note: "" },
      ]),
    ).toBe("look 3.1 s · compose 5.7 s ✕");
    expect(frameUrl("a b.jpg")).toBe("/rulecam-api/wearables/frames/a%20b.jpg");
    expect(audioUrl("x.wav")).toBe("/rulecam-api/wearables/audio/x.wav");
  });

  it("scales frames to at most 960 px", () => {
    expect(scaledSize(1920, 1080)).toEqual([960, 540]);
    expect(scaledSize(720, 1280)).toEqual([540, 960]);
    expect(scaledSize(640, 480)).toEqual([640, 480]);
    expect(scaledSize(0, 480)).toEqual([0, 0]);
  });

  it("speaks each new open site alert once, oldest first", () => {
    const alerts = [alert("new"), alert("acked", "acknowledged"), alert("old")];
    expect(unspokenAlerts(alerts, new Set(["seen"])).map((a) => a.alert_id)).toEqual([
      "old",
      "new",
    ]);
    expect(unspokenAlerts(alerts, new Set(["new", "old"]))).toEqual([]);
    expect(alertLine(alert("x"))).toBe("Site alert. Forklift near people. Dock 2");
  });

  it("polls a job until it is done or failed", async () => {
    const running: Job = {
      job_id: "j",
      status: "running",
      answer: null,
      report: null,
      error: null,
    };
    const seen: string[] = [];
    const states: Job[] = [running, { ...running, status: "done", answer: answer() }];
    const done = await untilDone(
      running,
      async (id) => {
        seen.push(id);
        return states.shift() ?? running;
      },
      { wait: async () => undefined },
    );
    expect(done.answer?.spoken).toBe("Stop, forklift ahead.");
    expect(seen).toEqual(["j", "j"]);
    const progress: string[] = [];
    const withStage: Job = {
      ...running,
      stages: [{ name: "look", model: "muse-glimmer-30b", ms: 10_200, ok: true, note: "" }],
    };
    const queue: Job[] = [withStage, { ...running, status: "done", answer: answer() }];
    await untilDone(running, async () => queue.shift() ?? running, {
      wait: async () => undefined,
      onProgress: (j) => progress.push(progressLabel(j) ?? ""),
    });
    expect(progress).toEqual(["Glimmer look 10.2 s ✓"]);
    expect(progressLabel(running)).toBeNull();
    await expect(
      untilDone(running, async () => ({ ...running, status: "failed", error: "Spark down" }), {
        wait: async () => undefined,
      }),
    ).rejects.toThrow("Spark down");
    await expect(
      untilDone(running, async () => running, {
        every: 10,
        limitMs: 30,
        wait: async () => undefined,
      }),
    ).rejects.toThrow("No answer in time");
  });
});
