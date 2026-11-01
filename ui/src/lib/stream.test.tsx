import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_GOLDEN, GOLDEN_RUNS } from "@/fixtures/golden";

import { badgeLabel } from "./store";
import { StreamProvider, useDemoQuery, useGolden, useStream } from "./stream";
import { usePlans } from "./usePlans";

let search = "";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));

/** A WebSocket double: tests open, message and close it by hand. No network. */
class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  close(): void {
    this.closed = true;
  }
  open(): void {
    this.onopen?.();
  }
  send(frame: object): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  drop(): void {
    this.onclose?.();
  }
}

function Probe() {
  const state = useStream();
  return (
    <p data-testid="probe">
      {badgeLabel(state)}|{state.connection}|{state.events.length}
    </p>
  );
}

const goldenEvents = GOLDEN_RUNS[0]!.frames.filter((f) => f.topic === "candidate.new").length;
const probe = (): string => screen.getByTestId("probe").textContent ?? "";
const latest = (): FakeSocket => FakeSocket.all[FakeSocket.all.length - 1]!;

beforeEach(() => {
  search = "";
  FakeSocket.all = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ mode: "live", badge: "LIVE" }) })),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("StreamProvider", () => {
  it("asks for the mode through the same-origin proxy once the socket opens", async () => {
    render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    expect(probe()).toBe("…|connecting|0");
    await act(async () => latest().open());
    expect(fetch).toHaveBeenCalledWith("/rulecam-api/mode");
    expect(probe()).toBe("LIVE|open|0");
  });

  it("stays OFFLINE through reconnect attempts, with a capped backoff", async () => {
    render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    await act(async () => latest().open());
    act(() => latest().drop());
    expect(probe()).toBe("OFFLINE|closed|0");
    act(() => vi.advanceTimersByTime(500));
    expect(FakeSocket.all).toHaveLength(2);
    expect(probe()).toBe("OFFLINE|closed|0"); // not the stale LIVE while reconnecting
    act(() => latest().drop());
    act(() => vi.advanceTimersByTime(999));
    expect(FakeSocket.all).toHaveLength(2);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeSocket.all).toHaveLength(3);
    await act(async () => latest().open());
    expect(probe()).toBe("LIVE|open|0");
  });

  it("ignores a closed socket's late close event and stops retrying on unmount", async () => {
    const view = render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    const first = latest();
    await act(async () => first.open());
    view.unmount();
    expect(first.closed).toBe(true);
    first.drop();
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("switches to the demo on client navigation without mixing in live data", async () => {
    const tree = (
      <StreamProvider>
        <Probe />
      </StreamProvider>
    );
    const view = render(tree);
    const live = latest();
    await act(async () => live.open());
    act(() =>
      live.send({
        topic: "candidate.new",
        payload: { event_id: "live-1", rule_id: "r", camera_id: "cam9" },
      }),
    );
    expect(probe()).toBe("LIVE|open|1");
    search = "demo=1";
    view.rerender(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    expect(live.closed).toBe(true);
    act(() => live.drop()); // a late close from the old socket
    act(() => vi.advanceTimersByTime(60_000));
    expect(probe()).toBe(`REPLAY|open|${goldenEvents}`); // only the golden run's events
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("plays the recorded demo without a socket, labelled REPLAY", () => {
    search = "demo=1";
    render(
      <StreamProvider>
        <Probe />
      </StreamProvider>,
    );
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeSocket.all).toHaveLength(0);
    expect(probe()).toBe(`REPLAY|open|${goldenEvents}`);
  });

  it("selects a golden run by name, and treats an unknown name as live", () => {
    function Which() {
      return (
        <p data-testid="which">
          {useGolden()?.name ?? "live"}|{useDemoQuery()}
        </p>
      );
    }
    search = `demo=${DEFAULT_GOLDEN}`;
    const view = render(
      <StreamProvider>
        <Which />
      </StreamProvider>,
    );
    expect(screen.getByTestId("which").textContent).toBe(
      `${DEFAULT_GOLDEN}|?demo=${DEFAULT_GOLDEN}`,
    );
    expect(FakeSocket.all).toHaveLength(0);
    view.unmount();
    search = "demo=no-such-run";
    render(
      <StreamProvider>
        <Which />
      </StreamProvider>,
    );
    expect(screen.getByTestId("which").textContent).toBe("live|");
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("keeps the plans a screen loads for the demo (the switch's reset happens first)", () => {
    function Plans() {
      const { plans } = usePlans();
      const text = Object.values(plans)
        .map((e) => `${e.latest.plan_id}:v${e.latest.version}:approved v${e.approved?.version}`)
        .join(",");
      return <p data-testid="plans">{text}</p>;
    }
    search = "demo=1";
    render(
      <StreamProvider>
        <Plans />
      </StreamProvider>,
    );
    act(() => vi.advanceTimersByTime(60_000));
    // The golden run's approved plan, loaded on mount, survives the replayed `plan.updated`.
    const plan = GOLDEN_RUNS[0]!.plan;
    expect(screen.getByTestId("plans").textContent).toBe(
      `${plan.plan_id}:v${plan.version}:approved v${plan.version}`,
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
