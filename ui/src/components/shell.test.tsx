import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initialState, type UiState } from "@/lib/store";

import { DashboardShell, shellBadge } from "./DashboardShell";
import { SampleDataTag } from "./SampleDataTag";

let streamState: UiState = initialState;
let demoQuery = "";
let pathname = "/monitor";

vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/lib/stream", () => ({
  useStream: () => streamState,
  useDemoQuery: () => demoQuery,
  useDemo: () => demoQuery !== "",
}));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ phase: "visitor", via: null, loginEnabled: true, expiresAt: null }),
}));
vi.mock("@/lib/theme", () => ({ useTheme: () => ({ isDark: false, setTheme: vi.fn() }) }));

/** Figures the shell used to print whatever the server said; none may come back. */
const MADE_UP = ["11/12", "22/22", "1 BLIND", "65 INTACT", "M. Vance", "7 ACTS", "3 OPEN"];

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  streamState = initialState;
  demoQuery = "";
  pathname = "/monitor";
});

describe("DashboardShell", () => {
  it("shows REPLAY on a golden replay, keeps ?demo= in links and polls nothing", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    streamState = { ...initialState, badge: "REPLAY" };
    demoQuery = "?demo=1";

    render(<DashboardShell>page</DashboardShell>);

    expect(screen.getByTestId("mode-badge").textContent).toBe("REPLAY");
    expect(fetchSpy).not.toHaveBeenCalled();
    const monitor = screen.getAllByRole("link").find((a) => a.textContent?.includes("Monitor"));
    expect(monitor?.getAttribute("href")).toBe("/monitor?demo=1");
    for (const text of MADE_UP) expect(document.body.textContent).not.toContain(text);
    expect(document.body.textContent).toContain("Visitor · read-only");
  });

  it("reads counts from the API through the proxy and leaves unknown ones blank", async () => {
    const fetchSpy = vi.fn(async (url: string) => {
      if (url === "/rulecam-api/health") {
        return respond({ gpus: { sam: { state: "warm" }, glimmer: "cold-ready" } });
      }
      if (url === "/rulecam-api/plans") return respond([{ plan_id: "a" }, { plan_id: "b" }]);
      if (url === "/rulecam-api/cameras") return respond([{ camera_id: "phone-1" }]);
      return respond({ detail: "owner only" }, 403);
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<DashboardShell>page</DashboardShell>);

    await waitFor(() => expect(document.body.textContent).toContain("warm"));
    expect(document.body.textContent).toContain("cold-ready");
    expect(screen.getByTestId("shell-cameras").textContent).toBe("Cameras 1");
    // Incidents answered 403: no count is shown for it, rather than a made-up one.
    const incidents = screen.getAllByRole("link").find((a) => a.textContent?.includes("Incidents"));
    expect(incidents?.textContent).toBe("Incidents");
    expect(fetchSpy).toHaveBeenCalledWith("/rulecam-api/health", { cache: "no-store" });
    for (const text of MADE_UP) expect(document.body.textContent).not.toContain(text);
  });

  it("names the current page in the breadcrumb, and shows none on the landing page or replay", () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond({}, 404)));
    pathname = "/ci";
    render(<DashboardShell>page</DashboardShell>);
    expect(document.querySelector(".shell-crumb")?.textContent).toContain("Rule CI");
    cleanup();

    for (const path of ["/", "/demo"]) {
      pathname = path;
      render(<DashboardShell>page</DashboardShell>);
      expect(document.querySelector(".shell-crumb")).toBeNull();
      cleanup();
    }
  });

  it("says unknown, not a remembered value, when the API is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );

    render(<DashboardShell>page</DashboardShell>);

    await waitFor(() => expect(document.body.textContent).toContain("unknown"));
    expect(screen.getByTestId("shell-cameras").textContent).toBe("Cameras —");
  });
});

describe("DashboardShell badge comes from GET /health", () => {
  const health = (body: unknown) =>
    vi.fn(async (url: string) => (url === "/rulecam-api/health" ? respond(body) : respond({}, 403)));

  it("says LIVE when /health is ok and live, even with the event socket down", async () => {
    vi.stubGlobal("fetch", health({ ok: true, mode: "live", gpus: {} }));
    streamState = { ...initialState, connection: "closed" };
    render(<DashboardShell>page</DashboardShell>);
    await waitFor(() => expect(screen.getByTestId("mode-badge").textContent).toBe("LIVE"));
  });

  it("says OFFLINE when /health does not answer, whatever the socket said", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("down");
      }),
    );
    streamState = { ...initialState, connection: "open", badge: "LIVE" };
    render(<DashboardShell>page</DashboardShell>);
    await waitFor(() => expect(screen.getByTestId("mode-badge").textContent).toBe("OFFLINE"));
  });

  it("says REPLAY when the API runs in replay mode, and on the landing page too", async () => {
    vi.stubGlobal("fetch", health({ ok: true, mode: "replay", gpus: {} }));
    pathname = "/";
    render(<DashboardShell>page</DashboardShell>);
    await waitFor(() => expect(screen.getByTestId("mode-badge").textContent).toBe("REPLAY"));
  });

  it("maps modes the way the API's badge_for does", () => {
    const at = new Date();
    expect(shellBadge(false, { phase: "loading" }, initialState)).toBe("…");
    expect(shellBadge(false, { phase: "ok", health: { ok: true, mode: "record" }, at }, initialState)).toBe("LIVE");
    expect(shellBadge(false, { phase: "ok", health: { ok: false, mode: "live" }, at }, initialState)).toBe("OFFLINE");
    expect(shellBadge(true, { phase: "error", at }, initialState)).toBe("REPLAY");
    // The socket may fill the badge only while /health has not answered yet.
    const open = { ...initialState, connection: "open" as const, badge: "LIVE" as const };
    expect(shellBadge(false, { phase: "loading" }, open)).toBe("LIVE");
    expect(shellBadge(false, { phase: "error", at }, open)).toBe("OFFLINE");
  });
});

describe("DashboardShell information architecture", () => {
  it("groups the app as use cases, build, run, trust and system", () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond({}, 404)));
    render(<DashboardShell>page</DashboardShell>);
    const nav = screen.getByRole("navigation", { name: "Primary navigation" });
    for (const group of ["Use cases", "Build", "Run", "Trust", "System"]) {
      expect(screen.getByRole("list", { name: group })).toBeTruthy();
    }
    const useCases = screen.getByRole("list", { name: "Use cases" });
    expect(
      Array.from(useCases.querySelectorAll("a")).map((a) => a.getAttribute("href")),
    ).toEqual(["/usecases/supermarket", "/usecases/warehouse", "/usecases/crowd"]);
    const current = nav.querySelector('[aria-current="page"]');
    expect(current?.textContent).toContain("Monitor");
  });

  it("gives the landing page a wordmark bar and the worker QR page a bare frame", () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond({}, 404)));
    pathname = "/";
    render(<DashboardShell>page</DashboardShell>);
    expect(document.querySelector(".shell-sidebar")).toBeNull();
    expect(document.querySelector(".site-header")).not.toBeNull();
    expect(screen.getByTestId("mode-badge")).toBeTruthy();
    cleanup();

    pathname = "/worker/plan-1/phone-1";
    render(<DashboardShell>page</DashboardShell>);
    expect(document.querySelector(".shell-sidebar")).toBeNull();
    expect(document.querySelector(".bare-header")).not.toBeNull();
    expect(screen.getByTestId("mode-badge")).toBeTruthy();
  });
});

describe("SampleDataTag", () => {
  it("says SAMPLE DATA and what is sample", () => {
    render(<SampleDataTag what="The fleet below is built in." />);
    const tag = screen.getByTestId("sample-data-tag");
    expect(tag.textContent).toContain("SAMPLE DATA");
    expect(tag.textContent).toContain("The fleet below is built in.");
    expect(tag.textContent).toContain("not a live run");
  });
});
