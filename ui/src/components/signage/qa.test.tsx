// QA fixes on the boards: B14 (no unbundled clip reaches the network), B15 (no per-person
// identifiers on the monitor), B19 (/system shows the API's own fields or "unknown").
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BUNDLED_CLIPS, ClipPlayer, stillFor } from "../ClipPlayer";
import { MonitorSignage } from "./MonitorSignage";
import { SystemSignage } from "./SystemSignage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ClipPlayer (QA-B14)", () => {
  it("shows the camera's committed still, and says so, for a clip that is not bundled", () => {
    render(<ClipPlayer src="/videos/cam09_excavator.mp4" autoPlay muted />);
    expect(document.querySelector("video")).toBeNull();
    const still = screen.getByTestId("clip-still");
    expect(still.querySelector("img")?.getAttribute("src")).toBe("/strips/CAM-09_strip_1.jpg");
    expect(still.textContent).toContain("not bundled");
  });

  it("maps the named clips onto their cameras", () => {
    expect(stillFor("/videos/warehouse_forklift.mp4")).toBe("/strips/CAM-04_strip_1.jpg");
    expect(stillFor("/videos/kitchen_prep.mp4")).toBe("/strips/CAM-05_strip_1.jpg");
    expect(stillFor("/videos/unknown.mp4")).toBeNull();
    expect(BUNDLED_CLIPS.size).toBe(0);
  });
});

describe("MonitorSignage (QA-B15)", () => {
  it("labels people and vehicles neutrally, with no numbers that read as ids", () => {
    render(<MonitorSignage />);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/WORKER|VISITOR|ENGINEER|SUPERVISOR|Aggressor/);
    expect(text).not.toMatch(/(Worker|Person|Pedestrian|Forklift|Visitor) #\d/);
    expect(document.querySelector("video")).toBeNull();
  });
});

describe("SystemSignage (QA-B19)", () => {
  it("shows the API's candidates and failures, never a default", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/health")) return new Response(JSON.stringify({ ok: true, mode: "live", gpus: {} }));
        if (url.endsWith("/runtime/metrics")) {
          return new Response(
            JSON.stringify({ chunks_processed: 3, candidates: 0, verify_pending: 0, verifications_failed: 0, verify_errors: 0 }),
          );
        }
        return new Response("", { status: 404 });
      }),
    );
    render(<SystemSignage />);
    await waitFor(() => expect(document.body.textContent).toContain("Mode: live"));
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("10.0 Hz");
    expect(text).not.toContain("Muse Spark Open 70B");
    expect(text).not.toContain("dev-worktree");
    const candidates = screen.getByText("Candidates").parentElement!;
    expect(candidates.textContent).toBe("Candidates0");
  });

  it("says unknown, not LIVE, when the API does not answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("down");
      }),
    );
    render(<SystemSignage />);
    await waitFor(() => expect(document.body.textContent).toContain("Mode: unknown"));
    expect(screen.getByText("Candidates").parentElement!.textContent).toBe("Candidatesunknown");
  });
});
