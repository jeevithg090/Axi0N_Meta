import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accessFor } from "./crowdApi";
import CrowdPage from "./page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function answer(status: number, body: unknown = { detail: "owner login required" }): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

describe("crowd page for visitors and failing APIs", () => {
  it("maps the API's answers to what the page shows", () => {
    expect(accessFor(403)).toBe("owner");
    expect(accessFor(401)).toBe("owner");
    expect(accessFor(404)).toBe("unavailable");
    expect(accessFor(503)).toBe("off");
    expect(accessFor(500)).toBe("offline");
  });

  it("shows a visitor the owner notice and the recorded run, never the raw refusal", async () => {
    answer(403);
    render(<CrowdPage />);
    expect(await screen.findByTestId("owner-only")).toBeTruthy();
    expect(screen.getByText(/RECORDED LIVE RUN/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("owner login required");
    expect(screen.getByText("chain verified")).toBeTruthy();
  });

  it("says when this server has no crowd watch instead of loading forever", async () => {
    answer(404, { detail: "Not Found" });
    render(<CrowdPage />);
    expect(await screen.findByText(/not available on this server/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("Not Found");
    expect(screen.getByText(/RECORDED LIVE RUN/)).toBeTruthy();
  });

  it("says when crowd watch is off, without operator hints", async () => {
    answer(503, { detail: "crowd watch needs RULECAM_PROTOTYPE_MODE=true" });
    render(<CrowdPage />);
    expect(await screen.findByText(/switched off on this server/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("RULECAM_PROTOTYPE_MODE");
  });
});
