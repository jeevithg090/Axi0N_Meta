import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UseCaseFrame, worldOf } from "./UseCaseFrame";
import { WORLDS } from "./worlds";

let pathname = "/usecases/warehouse";
let demo: boolean | null = false;

vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/lib/stream", () => ({
  useDemo: () => demo,
  useDemoQuery: () => (demo ? "?demo=1" : ""),
}));

beforeEach(() => {
  pathname = "/usecases/warehouse";
  demo = false;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("UseCaseFrame", () => {
  it("knows the three worlds from the path", () => {
    expect(worldOf("/usecases/crowd")?.name).toBe("Crowd");
    expect(worldOf("/usecases/unknown")).toBeNull();
    expect(WORLDS.map((w) => w.promise)).toEqual([
      "Ask your store anything",
      "Forklift rules that prove themselves",
      "Crowd-crush early warning",
    ]);
  });

  it("frames the page with its promise, the switcher, its still and the live status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: true, mode: "live", gpus: { glimmer: { state: "warm" } } }))),
    );
    render(
      <UseCaseFrame>
        <p>live data</p>
      </UseCaseFrame>,
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Forklift rules that prove themselves");
    const switcher = screen.getByRole("navigation", { name: "Use cases" });
    const current = within(switcher).getByRole("link", { current: "page" });
    expect(current.textContent).toContain("Warehouse");
    expect(within(switcher).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "/usecases/supermarket",
      "/usecases/warehouse",
      "/usecases/crowd",
    ]);
    // Each page tells its own journey with real data; the frame carries the world's still.
    expect(screen.getByTestId("still-warehouse")).toBeTruthy();
    expect(screen.queryByTestId("step-flow")).toBeNull();
    expect(screen.getByText("live data")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("usecase-status").textContent).toContain("LIVE"));
    expect(screen.getByTestId("usecase-status").textContent).toContain("warm");
  });

  it("says REPLAY on a golden replay and polls nothing", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    demo = true;
    pathname = "/usecases/crowd";
    render(<UseCaseFrame>page</UseCaseFrame>);
    expect(screen.getByTestId("live-badge").textContent).toContain("REPLAY");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /Crowd/ }).getAttribute("href")).toBe("/usecases/crowd?demo=1");
  });

  it("shows a still from the footage it ran on, labelled honestly", () => {
    vi.stubGlobal("fetch", vi.fn());
    demo = true;
    pathname = "/usecases/crowd";
    render(<UseCaseFrame>page</UseCaseFrame>);
    const still = screen.getByTestId("still-crowd");
    expect(within(still).getByRole("img").getAttribute("src")).toBe("/usecases/crowd.jpg");
    expect(still.textContent).toContain("Research footage, Jülich bottleneck experiment");
    expect(still.textContent).toContain("Not a real venue");
    for (const w of WORLDS) expect(w.still.credit).toMatch(/^(Stock|Research) footage/);
  });

  it("says OFFLINE when the API does not answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("down");
      }),
    );
    render(<UseCaseFrame>page</UseCaseFrame>);
    await waitFor(() => expect(screen.getByTestId("usecase-status").textContent).toContain("OFFLINE"));
    expect(screen.getByTestId("usecase-status").textContent).toContain("API not reachable");
  });
});
