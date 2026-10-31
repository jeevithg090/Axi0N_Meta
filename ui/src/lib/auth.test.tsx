import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";
import { OwnerControl } from "@/components/Shell";

import { api, CSRF_HEADER, errorText, ApiError, setCsrfToken, withCsrf } from "./api";
import { AuthProvider, useOwnerControls } from "./auth";
import { StreamProvider } from "./stream";

const push = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
  useRouter: () => ({ push }),
}));

/** No network: the live socket never opens. */
class QuietSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  close(): void {}
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const visitor = {
  owner: false,
  via: null,
  csrf_token: null,
  expires_at: null,
  login_enabled: true,
};
const ownerSession = {
  owner: true,
  via: "session",
  csrf_token: "tok-1",
  expires_at: "2026-11-01T00:00:00Z",
  login_enabled: true,
};

let me: typeof visitor | typeof ownerSession;
let loginStatus: number;
const calls: { url: string; method: string; csrf: string | null; body: unknown }[] = [];

beforeEach(() => {
  search = "";
  me = visitor;
  loginStatus = 0;
  calls.length = 0;
  push.mockReset();
  setCsrfToken(null);
  vi.stubGlobal("WebSocket", QuietSocket);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const csrf = new Headers(init?.headers).get(CSRF_HEADER);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    calls.push({ url, method, csrf, body });
    if (url.endsWith("/auth/me")) return json(me);
    if (url.endsWith("/auth/login")) {
      if (loginStatus) return json({ detail: "no" }, loginStatus);
      if ((body as { passphrase: string }).passphrase !== "right horse battery") {
        return json({ detail: "wrong passphrase" }, 401);
      }
      me = ownerSession;
      return json(ownerSession);
    }
    if (url.endsWith("/auth/logout")) {
      me = visitor;
      return new Response(null, { status: 204 });
    }
    return json({ plan_id: "p1", version: 1, site_name: "s", approved: true });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function Controls() {
  return <p data-testid="controls">{useOwnerControls() ? "owner controls" : "hidden"}</p>;
}

function renderLogin() {
  return render(
    <StreamProvider>
      <AuthProvider>
        <OwnerControl />
        <Controls />
        <LoginPage />
      </AuthProvider>
    </StreamProvider>,
  );
}

const type = (value: string): void => {
  fireEvent.change(screen.getByLabelText("Passphrase"), { target: { value } });
  fireEvent.submit(screen.getByLabelText("Passphrase").closest("form")!);
};

describe("owner login", () => {
  it("logs in, sends the CSRF token on writes only, and logs out", async () => {
    renderLogin();
    expect(await screen.findByText("Owner login", { selector: "a" })).toBeDefined();
    expect(screen.getByTestId("controls").textContent).toBe("hidden");

    type("not the passphrase");
    expect((await screen.findByRole("alert")).textContent).toBe("That passphrase is not right.");
    expect(push).not.toHaveBeenCalled();

    type("right horse battery");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/planner"));
    expect(await screen.findByText("Owner · Log out")).toBeDefined();
    expect(screen.getByTestId("controls").textContent).toBe("owner controls");

    await api.approve("p1");
    await api.plans();
    const approve = calls.find((c) => c.url.endsWith("/plans/p1/approve"))!;
    expect(approve.csrf).toBe("tok-1");
    expect(calls.find((c) => c.url.endsWith("/rulecam-api/plans"))!.csrf).toBeNull();

    fireEvent.click(screen.getByText("Owner · Log out"));
    expect(await screen.findByText("Owner login", { selector: "a" })).toBeDefined();
    expect(calls.find((c) => c.url.endsWith("/auth/logout"))!.csrf).toBe("tok-1");
    calls.length = 0;
    await api.approve("p1");
    expect(calls[0]!.csrf).toBeNull();
    expect(screen.getByTestId("controls").textContent).toBe("hidden");
  });

  it.each([
    [429, "Too many attempts. Wait a minute, then try again."],
    [503, "Owner login is not set up on this server yet."],
  ])("explains a %i", async (status, text) => {
    loginStatus = status;
    renderLogin();
    await screen.findByText("Owner login", { selector: "a" });
    type("right horse battery");
    expect((await screen.findByRole("alert")).textContent).toBe(text);
  });

  it("says when login is off or the API is unreachable", async () => {
    me = { ...visitor, login_enabled: false };
    const { unmount } = renderLogin();
    expect((await screen.findByRole("alert")).textContent).toContain("not set up");
    expect((screen.getByText("Log in") as HTMLButtonElement).disabled).toBe(true);
    unmount();
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    renderLogin();
    expect((await screen.findByRole("alert")).textContent).toContain("Could not reach");
  });

  it("knows the owner's own machine and the demo without logging in", async () => {
    me = { ...visitor, owner: true, via: "loopback" } as unknown as typeof visitor;
    const { unmount } = renderLogin();
    expect(await screen.findByText("Owner (this machine)")).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain("no login needed");
    unmount();
    search = "demo=1";
    calls.length = 0;
    renderLogin();
    await act(async () => undefined);
    expect(screen.getByTestId("controls").textContent).toBe("owner controls");
    expect(calls).toEqual([]);
  });
});

describe("api helpers", () => {
  it("adds the CSRF header to writes only", () => {
    setCsrfToken("t");
    expect(new Headers(withCsrf({ method: "POST" }).headers).get(CSRF_HEADER)).toBe("t");
    expect(withCsrf({ method: "GET" }).headers).toBeUndefined();
    expect(withCsrf().headers).toBeUndefined();
    setCsrfToken(null);
    expect(withCsrf({ method: "PUT" }).headers).toBeUndefined();
  });

  it("words a 403 as a login prompt", () => {
    expect(errorText(new ApiError(403, "owner login required"), "x")).toContain("Log in");
    expect(errorText(new ApiError(409, "busy"), "x")).toBe("busy");
    expect(errorText(new Error("boom"), "fallback")).toBe("fallback");
  });
});
