import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CostReport } from "@/lib/api";
import { StreamProvider } from "@/lib/stream";

import { CostBadge, useCost } from "./CostBadge";

let search = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
  useRouter: () => ({ push: vi.fn() }),
}));

/** No network: the live socket never opens. */
class QuietSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  close(): void {}
}

const report = (over: Partial<CostReport> = {}): CostReport => ({
  budget_usd_per_camera_hour: 12,
  gpu_usd_per_hour: 0,
  alarm: false,
  over_budget: [],
  usd: 0.5,
  usd_per_camera_hour: 0.42,
  ...over,
});

function Probe() {
  return <CostBadge cost={useCost(10)} />;
}

beforeEach(() => {
  search = "";
  vi.stubGlobal("WebSocket", QuietSocket);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("cost badge", () => {
  it("shows the cost per camera-hour while under budget", () => {
    render(<CostBadge cost={report()} />);
    const badge = screen.getByTestId("cost-badge");
    expect(badge.textContent).toBe("$0.42/cam-h");
    expect(badge.className).not.toContain("badge-alarm");
  });

  it("turns into a red alarm badge naming the cameras over budget", () => {
    render(<CostBadge cost={report({ alarm: true, over_budget: ["cam1", "cam2"] })} />);
    const badge = screen.getByTestId("cost-badge");
    expect(badge.className).toContain("badge-alarm");
    expect(badge.getAttribute("role")).toBe("alert");
    expect(badge.textContent).toContain("BUDGET · cam1, cam2");
    expect(badge.getAttribute("title")).toContain("$12.00/camera-hour");
  });

  it("renders nothing without a report and a dash before camera time", () => {
    const { container } = render(<CostBadge cost={null} />);
    expect(container.textContent).toBe("");
    render(<CostBadge cost={report({ usd_per_camera_hour: null })} />);
    expect(screen.getByTestId("cost-badge").textContent).toBe("$ —/cam-h");
  });

  it("polls /runtime/metrics on the live site", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ cost: report({ alarm: true, over_budget: ["cam1"] }) }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("cost-badge").textContent).toContain("cam1"));
    expect(String(fetch.mock.calls[0]![0])).toContain("/runtime/metrics");
  });

  it("shows no badge when the runtime is off or in demo mode", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "runtime loop not running" }), { status: 503 }),
      );
    render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("cost-badge")).toBeNull();
    cleanup();
    fetch.mockClear();
    search = "demo=1";
    render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(fetch.mock.calls.some(([url]) => String(url).includes("/runtime/metrics"))).toBe(false);
  });
});
