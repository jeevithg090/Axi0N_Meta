import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ago,
  cameras,
  fillTemplate,
  healthLine,
  imageUrl,
  rankFits,
  snapshotUrl,
  surveyStep,
  type StreamHealth,
  type UseCaseFit,
} from "./cameras";

const HEALTH: StreamHealth = {
  state: "live",
  fps: 9.96,
  speed: 1,
  frames: 300,
  chunks: 10,
  connected_at: null,
  first_chunk_s: 4.6,
  last_frame_at: "2026-11-04T00:00:00Z",
  last_chunk_at: null,
  last_chunk_id: null,
  restarts: 2,
  errors: 2,
  last_error: null,
  next_retry_at: null,
  input: { codec: "hevc", width: 2560, height: 1440, fps: 25 },
  title: null,
};

describe("camera helpers", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fills a maker's template", () => {
    expect(fillTemplate("rtsp://{host}:554/Streaming/Channels/101", " 10.0.0.5 ")).toBe(
      "rtsp://10.0.0.5:554/Streaming/Channels/101",
    );
    expect(fillTemplate("rtsp://{host}/x", "")).toBe("rtsp://<camera-ip>/x");
  });

  it("says how long ago", () => {
    const now = Date.parse("2026-11-04T00:00:30Z");
    expect(ago(null, now)).toBe("never");
    expect(ago("2026-11-04T00:00:00Z", now)).toBe("30 s ago");
    expect(ago("2026-11-03T23:50:30Z", now)).toBe("10 min ago");
  });

  it("writes one health line", () => {
    const line = healthLine(HEALTH, Date.parse("2026-11-04T00:00:03Z"));
    expect(line).toBe(
      "live · 10.0 fps · 10 chunks · last frame 3 s ago · hevc 2560x1440 @ 25 · 2 reconnects",
    );
  });

  it("names survey steps and ranks fits", () => {
    expect(surveyStep("detecting")).toContain("SAM 3.1");
    const fits: UseCaseFit[] = [
      { use_case: "warehouse", fit: "poor", why: "", first_rules: [] },
      { use_case: "crowd", fit: "strong", why: "", first_rules: [] },
      { use_case: "supermarket", fit: "possible", why: "", first_rules: [] },
    ];
    expect(rankFits(fits).map((f) => f.use_case)).toEqual(["crowd", "supermarket", "warehouse"]);
  });

  it("builds image URLs through the proxy", () => {
    expect(snapshotUrl("cam 1", 5)).toBe("/rulecam-api/connectors/snapshot/cam%201.jpg?t=5");
    expect(imageUrl("a.jpg")).toBe("/rulecam-api/connectors/images/a.jpg");
  });

  it("posts a camera and surfaces the API's error detail", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ camera_id: "cam-1" }), { status: 201 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "unsupported source scheme 'ftp'" }), {
          status: 422,
        }),
      );
    const added = await cameras.add({ url: "rtsp://cam/x", username: "u", password: "p" });
    expect(added.camera_id).toBe("cam-1");
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/rulecam-api/connectors/cameras");
    expect((init as RequestInit).method).toBe("POST");
    await expect(cameras.add({ url: "ftp://x" })).rejects.toThrow("unsupported source scheme");
  });
});
