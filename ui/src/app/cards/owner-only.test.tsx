// QA-B6 / QA-B7: a visitor refused by the API (403) sees the owner-only notice with a Log in link,
// never the API's raw "owner login required" or a false "could not reach the API".

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "@/app/alerts/page";
import CardGalleryPage from "@/app/cards/page";
import { StreamProvider } from "@/lib/stream";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams("") }));

class SilentSocket {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  close(): void {}
}

function refuse(status: number) {
  return vi.fn(async () => ({
    ok: false,
    status,
    statusText: "Forbidden",
    json: async () => ({ detail: "owner login required" }),
  }));
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", SilentSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("owner-only pages for a visitor", () => {
  it("rule cards show the owner-only notice on a 403", async () => {
    vi.stubGlobal("fetch", refuse(403));
    render(
      <StreamProvider>
        <CardGalleryPage />
      </StreamProvider>,
    );
    expect(await screen.findByTestId("owner-only")).toBeTruthy();
    expect(screen.getByRole("link", { name: /log in/i }).getAttribute("href")).toBe("/login");
    expect(screen.queryByText(/owner login required/i)).toBeNull();
  });

  it("alerts show the owner-only notice on a 403, not a network error", async () => {
    vi.stubGlobal("fetch", refuse(403));
    render(
      <StreamProvider>
        <AlertsPage />
      </StreamProvider>,
    );
    expect(await screen.findByTestId("owner-only")).toBeTruthy();
    expect(screen.queryByText(/could not reach/i)).toBeNull();
    expect(screen.queryByText("No alerts.")).toBeNull();
  });

  it("alerts still say so when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );
    render(
      <StreamProvider>
        <AlertsPage />
      </StreamProvider>,
    );
    expect(await screen.findByText(/could not reach the rulecam api/i)).toBeTruthy();
    expect(screen.queryByTestId("owner-only")).toBeNull();
  });
});
